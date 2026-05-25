from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import pygltflib
import rasterio
from py3dtiles.tileset.bounding_volume_box import BoundingVolumeBox
from py3dtiles.tileset.content import b3dm
from py3dtiles.tileset.content.gltf_utils import GltfAttribute, GltfMesh, GltfPrimitive
from py3dtiles.tileset.tile import Tile
from py3dtiles.tileset.tileset import TileSet
from pyproj import Transformer
from rasterio.windows import Window
from rasterio.windows import transform as window_transform


MAX_VERTS_PER_TILE = 200_000


@dataclass
class Terrain3DConfig:
    bounds_source: tuple[float, float, float, float]
    bounds_wgs84: tuple[float, float, float, float]
    crs: str
    elevation_vmin: float
    elevation_vmax: float


@dataclass
class Terrain3DOptions:
    step: int = 4
    tiles_x: int = 1
    tiles_y: int = 1
    vertical_exaggeration: float = 1.0


@dataclass
class SurveyOrigin:
    east: float
    north: float
    height: float
    transform: np.ndarray


@dataclass
class MeshChunk:
    points_local: np.ndarray
    triangles: np.ndarray
    colors: np.ndarray
    normals: np.ndarray
    transform: np.ndarray


def _build_dji_lut(size: int = 256) -> np.ndarray:
    stops = np.array(
        [
            [0.00, 0, 0, 130],
            [0.25, 0, 255, 255],
            [0.50, 0, 255, 0],
            [0.75, 255, 255, 0],
            [1.00, 139, 0, 0],
        ],
        dtype=np.float32,
    )
    positions = stops[:, 0]
    colors = stops[:, 1:4]
    lut = np.zeros((size, 3), dtype=np.uint8)

    for index in range(size):
        position = index / (size - 1)
        stop_index = int(np.searchsorted(positions, position, side="right") - 1)
        stop_index = max(0, min(stop_index, len(positions) - 2))
        left_position = positions[stop_index]
        right_position = positions[stop_index + 1]
        ratio = (position - left_position) / max(right_position - left_position, 1e-9)
        rgb = colors[stop_index] + ratio * (colors[stop_index + 1] - colors[stop_index])
        lut[index] = np.clip(rgb, 0, 255).astype(np.uint8)

    return lut


DJI_LUT = _build_dji_lut()


def build_terrain_config(dtm_path: Path) -> Terrain3DConfig:
    with rasterio.open(dtm_path) as dataset:
        if not dataset.crs:
            raise ValueError("DTM/DSM raster must have a CRS for 3D terrain generation.")

        crs_text = str(dataset.crs)
        bounds = dataset.bounds
        source_bounds = (bounds.left, bounds.bottom, bounds.right, bounds.top)
        transformer = Transformer.from_crs(dataset.crs, "EPSG:4326", always_xy=True)
        xs = [bounds.left, bounds.right, bounds.right, bounds.left]
        ys = [bounds.bottom, bounds.bottom, bounds.top, bounds.top]
        lon, lat = transformer.transform(xs, ys)

        max_preview_size = 1024
        scale = max(dataset.width / max_preview_size, dataset.height / max_preview_size, 1)
        data = dataset.read(
            1,
            out_shape=(max(1, int(dataset.height / scale)), max(1, int(dataset.width / scale))),
            masked=True,
        )

    values = data.compressed() if np.ma.isMaskedArray(data) else data.reshape(-1)
    values = values[np.isfinite(values)]

    if values.size == 0:
        raise ValueError("No valid elevation values found in DTM/DSM.")

    vmin, vmax = np.percentile(values, [5, 95])

    if float(vmin) == float(vmax):
        vmax = vmin + 1.0

    return Terrain3DConfig(
        bounds_source=source_bounds,
        bounds_wgs84=(min(lon), min(lat), max(lon), max(lat)),
        crs=crs_text,
        elevation_vmin=float(vmin),
        elevation_vmax=float(vmax),
    )


def _utm_to_geodetic(easting: np.ndarray, northing: np.ndarray, elevation: np.ndarray, crs: str):
    transformer = Transformer.from_crs(crs, "EPSG:4979", always_xy=True)
    lon, lat, height = transformer.transform(
        np.asarray(easting, dtype=np.float64).ravel(),
        np.asarray(northing, dtype=np.float64).ravel(),
        np.asarray(elevation, dtype=np.float64).ravel(),
    )
    return (
        lon.reshape(easting.shape),
        lat.reshape(northing.shape),
        height.reshape(elevation.shape),
    )


def _enu_to_ecef_matrix(lon_deg: float, lat_deg: float, height_m: float) -> np.ndarray:
    lon = np.radians(lon_deg)
    lat = np.radians(lat_deg)
    sin_lon, cos_lon = np.sin(lon), np.cos(lon)
    sin_lat, cos_lat = np.sin(lat), np.cos(lat)

    east = np.array([-sin_lon, cos_lon, 0.0], dtype=np.float64)
    north = np.array([-sin_lat * cos_lon, -sin_lat * sin_lon, cos_lat], dtype=np.float64)
    up = np.array([cos_lat * cos_lon, cos_lat * sin_lon, sin_lat], dtype=np.float64)

    transformer = Transformer.from_crs("EPSG:4979", "EPSG:4978", always_xy=True)
    ox, oy, oz = transformer.transform(lon_deg, lat_deg, height_m)

    matrix = np.eye(4, dtype=np.float64)
    matrix[0:3, 0] = east
    matrix[0:3, 1] = north
    matrix[0:3, 2] = up
    matrix[0:3, 3] = (ox, oy, oz)
    return matrix


def _valid_mask(elevation: np.ndarray, nodata: float | None) -> np.ndarray:
    valid = np.isfinite(elevation)

    if nodata is not None and np.isfinite(nodata):
        valid &= np.abs(elevation - float(nodata)) > 0.5

    valid &= elevation > -500.0
    return valid


def _stride_for_vertex_cap(elevation: np.ndarray, nodata: float | None, base_step: int, max_verts: int) -> int:
    stride = max(1, base_step)

    while stride <= 128:
        subset = elevation[::stride, ::stride]

        if subset.shape[0] < 2 or subset.shape[1] < 2:
            return stride

        if int(np.count_nonzero(_valid_mask(subset, nodata))) <= max_verts:
            return stride

        stride *= 2

    return stride


def _axis_splits(start: int, size: int, parts: int) -> list[int]:
    return [int(round(start + index * size / parts)) for index in range(parts + 1)]


def _compute_hillshade(
    elevation: np.ndarray,
    valid: np.ndarray,
    res_x: float,
    res_y: float,
    azimuth: float = 315.0,
    altitude: float = 45.0,
    z_factor: float = 3.0,
) -> np.ndarray:
    fill_value = float(np.nanmedian(elevation[valid])) if np.any(valid) else 0.0
    filled = np.where(valid, elevation, fill_value) * z_factor
    dy, dx = np.gradient(filled, max(res_y, 1e-6), max(res_x, 1e-6))

    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(dy, -dx)
    azimuth_rad = np.radians(azimuth)
    altitude_rad = np.radians(altitude)
    shade = (
        np.sin(altitude_rad) * np.cos(slope)
        + np.cos(altitude_rad) * np.sin(slope) * np.cos(azimuth_rad - aspect)
    )
    return np.clip(np.nan_to_num(shade, nan=0.0, posinf=1.0, neginf=0.0), 0.0, 1.0)


def _elevation_to_rgb(
    elevation: np.ndarray,
    valid: np.ndarray,
    vmin: float,
    vmax: float,
    res_x: float,
    res_y: float,
) -> np.ndarray:
    rows, cols = elevation.shape
    rgb = np.zeros((rows, cols, 3), dtype=np.uint8)

    if not np.any(valid):
        return rgb

    span = max(vmax - vmin, 1e-6)
    normalized = np.clip((elevation - vmin) / span, 0.0, 1.0)
    colors = np.zeros((rows, cols, 3), dtype=np.float32)
    colors[valid] = DJI_LUT[(normalized[valid] * 255.0).astype(np.uint8)].astype(np.float32)

    shade = _compute_hillshade(elevation, valid, res_x, res_y)
    factor = 0.28 + 0.72 * shade
    colors[valid] *= factor[valid, np.newaxis]

    highlight = np.clip((shade - 0.72) / 0.28, 0.0, 1.0)
    colors[valid] = (
        colors[valid] * (1.0 - 0.18 * highlight[valid, np.newaxis])
        + 255.0 * (0.18 * highlight[valid, np.newaxis])
    )
    rgb[valid] = np.clip(colors[valid], 0, 255).astype(np.uint8)
    return rgb


def _survey_origin(config: Terrain3DConfig, dtm_path: Path) -> SurveyOrigin:
    left, bottom, right, top = config.bounds_source
    east = (left + right) / 2.0
    north = (bottom + top) / 2.0
    height = (config.elevation_vmin + config.elevation_vmax) / 2.0

    with rasterio.open(dtm_path) as dataset:
        try:
            row, col = dataset.index(east, north)
            value = float(dataset.read(1, window=Window(col, row, 1, 1))[0, 0])
            if _valid_mask(np.array([[value]]), dataset.nodata)[0, 0]:
                height = value
        except Exception:
            pass

    lon, lat, _ = _utm_to_geodetic(
        np.array([east]),
        np.array([north]),
        np.array([height]),
        config.crs,
    )
    transform = _enu_to_ecef_matrix(float(lon[0]), float(lat[0]), float(height))
    return SurveyOrigin(east, north, height, transform)


def _grid_normals(east: np.ndarray, north: np.ndarray, up: np.ndarray, res_x: float, res_y: float) -> np.ndarray:
    de = np.zeros_like(east)
    dn = np.zeros_like(north)
    du = np.zeros_like(up)
    de[:, 1:-1] = (east[:, 2:] - east[:, :-2]) / max(2.0 * res_x, 1e-6)
    dn[1:-1, :] = (north[2:, :] - north[:-2, :]) / max(2.0 * res_y, 1e-6)
    du[1:-1, 1:-1] = up[1:-1, 2:] - up[1:-1, :-2]
    du[0, :] = du[1, :]
    du[-1, :] = du[-2, :]
    du[:, 0] = du[:, 1]
    du[:, -1] = du[:, -2]
    normal = np.stack([-de, du, -dn], axis=-1)
    length = np.linalg.norm(normal, axis=-1, keepdims=True)
    return (normal / np.maximum(length, 1e-6)).astype(np.float32)


def _compact_mesh(
    elevation: np.ndarray,
    east_2d: np.ndarray,
    north_2d: np.ndarray,
    valid: np.ndarray,
    colors_rgb: np.ndarray,
    origin: SurveyOrigin,
    res_x: float,
    res_y: float,
    vertical_exaggeration: float,
):
    rows, cols = elevation.shape
    z = elevation.astype(np.float64)

    if vertical_exaggeration != 1.0:
        z_mean = float(np.mean(z[valid]))
        z = z_mean + (z - z_mean) * vertical_exaggeration

    de = east_2d - origin.east
    dn = north_2d - origin.north
    du = z - origin.height

    vertex_index = np.full(rows * cols, -1, dtype=np.int32)
    valid_count = int(np.count_nonzero(valid))

    if valid_count < 3:
        return None

    vertex_index[valid.ravel()] = np.arange(valid_count, dtype=np.int32)
    points = np.stack(
        [de.ravel()[valid.ravel()], du.ravel()[valid.ravel()], dn.ravel()[valid.ravel()]],
        axis=1,
    ).astype(np.float32)
    colors = colors_rgb.reshape(-1, 3)[valid.ravel()]

    de_filled = np.where(valid, de, 0.0)
    dn_filled = np.where(valid, dn, 0.0)
    du_filled = np.where(valid, du, 0.0)
    normals = _grid_normals(de_filled, dn_filled, du_filled, res_x, res_y).reshape(-1, 3)[valid.ravel()]

    row_grid, col_grid = np.mgrid[0 : rows - 1, 0 : cols - 1]
    i00 = (row_grid * cols + col_grid).ravel()
    i10 = (row_grid * cols + (col_grid + 1)).ravel()
    i01 = ((row_grid + 1) * cols + col_grid).ravel()
    i11 = ((row_grid + 1) * cols + (col_grid + 1)).ravel()
    cell_ok = (valid[:-1, :-1] & valid[:-1, 1:] & valid[1:, :-1] & valid[1:, 1:]).ravel()

    i00, i10, i01, i11 = i00[cell_ok], i10[cell_ok], i01[cell_ok], i11[cell_ok]
    tri_0 = np.stack([vertex_index[i00], vertex_index[i10], vertex_index[i01]], axis=1)
    tri_1 = np.stack([vertex_index[i10], vertex_index[i11], vertex_index[i01]], axis=1)
    triangles = np.concatenate([tri_0, tri_1], axis=0).astype(np.uint32)

    if len(triangles) == 0:
        return None

    return points, triangles, colors, normals


def _build_chunk_mesh(
    elevation: np.ndarray,
    east_2d: np.ndarray,
    north_2d: np.ndarray,
    *,
    origin: SurveyOrigin,
    config: Terrain3DConfig,
    nodata: float | None,
    res_x: float,
    res_y: float,
    vertical_exaggeration: float,
) -> MeshChunk | None:
    valid = _valid_mask(elevation, nodata)

    if not np.any(valid):
        return None

    colors = _elevation_to_rgb(
        elevation.astype(np.float32),
        valid,
        config.elevation_vmin,
        config.elevation_vmax,
        res_x,
        res_y,
    )
    packed = _compact_mesh(
        elevation,
        east_2d,
        north_2d,
        valid,
        colors,
        origin,
        res_x,
        res_y,
        vertical_exaggeration,
    )

    if packed is None:
        return None

    points, triangles, colors, normals = packed
    return MeshChunk(points, triangles, colors, normals, origin.transform)


def _iter_dtm_chunks(dtm_path: Path, config: Terrain3DConfig, options: Terrain3DOptions):
    step = max(1, options.step)
    left, bottom, right, top = config.bounds_source
    origin = _survey_origin(config, dtm_path)

    with rasterio.open(dtm_path) as dataset:
        full_window = rasterio.windows.from_bounds(left, bottom, right, top, dataset.transform)
        full_window = full_window.round_offsets().round_lengths()
        width, height = int(full_window.width), int(full_window.height)

        if width < 2 or height < 2:
            raise ValueError("DTM/DSM window is too small for 3D terrain mesh.")

        nodata = dataset.nodata
        col_off = int(full_window.col_off)
        row_off = int(full_window.row_off)
        col_end = col_off + width
        row_end = row_off + height
        survey_raster = dataset.read(1, window=Window(col_off, row_off, width, height))

        if options.tiles_x * options.tiles_y == 1:
            stride = _stride_for_vertex_cap(survey_raster, nodata, step, MAX_VERTS_PER_TILE)
        else:
            stride = _stride_for_vertex_cap(
                survey_raster,
                nodata,
                step,
                MAX_VERTS_PER_TILE * options.tiles_x * options.tiles_y,
            )

        col_splits = _axis_splits(col_off, width, options.tiles_x)
        row_splits = _axis_splits(row_off, height, options.tiles_y)

        for tile_y in range(options.tiles_y):
            for tile_x in range(options.tiles_x):
                x0 = col_splits[tile_x]
                x1 = col_splits[tile_x + 1]
                y0 = row_splits[tile_y]
                y1 = row_splits[tile_y + 1]

                if tile_x > 0:
                    x0 -= 1
                if tile_x < options.tiles_x - 1:
                    x1 += 1
                if tile_y > 0:
                    y0 -= 1
                if tile_y < options.tiles_y - 1:
                    y1 += 1

                x0 = max(col_off, x0)
                y0 = max(row_off, y0)
                x1 = min(col_end, x1)
                y1 = min(row_end, y1)
                win_width = max(2, x1 - x0)
                win_height = max(2, y1 - y0)
                window = Window(x0, y0, win_width, win_height)
                data = dataset.read(1, window=window)[::stride, ::stride]
                rows, cols = data.shape

                if rows < 2 or cols < 2:
                    continue

                win_transform = window_transform(window, dataset.transform)
                col_index = np.arange(cols) * stride
                row_index = np.arange(rows) * stride
                east_1d, _ = rasterio.transform.xy(win_transform, np.zeros(cols), col_index, offset="center")
                _, north_1d = rasterio.transform.xy(win_transform, row_index, np.zeros(rows), offset="center")
                east_2d = np.tile(np.asarray(east_1d), (rows, 1))
                north_2d = np.tile(np.asarray(north_1d).reshape(-1, 1), (1, cols))

                chunk = _build_chunk_mesh(
                    data.astype(np.float64),
                    east_2d,
                    north_2d,
                    origin=origin,
                    config=config,
                    nodata=nodata,
                    res_x=abs(dataset.transform.a) * stride,
                    res_y=abs(dataset.transform.e) * stride,
                    vertical_exaggeration=options.vertical_exaggeration,
                )

                if chunk is not None and len(chunk.points_local) <= MAX_VERTS_PER_TILE:
                    yield tile_x, tile_y, chunk


def _sanitize_gltf_for_cesium(gltf: pygltflib.GLTF2) -> None:
    for material in gltf.materials or []:
        material.alphaMode = pygltflib.OPAQUE
        pbr = material.pbrMetallicRoughness

        if pbr is not None:
            pbr.baseColorTexture = None
            pbr.metallicRoughnessTexture = None

        material.extensions = {**(material.extensions or {}), "KHR_materials_unlit": {}}

    for mesh in gltf.meshes or []:
        for primitive in mesh.primitives:
            attrs = primitive.attributes

            if attrs is not None:
                attrs.TEXCOORD_0 = None
                attrs.TEXCOORD_1 = None

            if attrs is not None and attrs.COLOR_0 is not None:
                accessor = gltf.accessors[attrs.COLOR_0]

                if accessor.componentType == pygltflib.UNSIGNED_BYTE:
                    accessor.normalized = True

    gltf.textures = []
    gltf.images = []
    gltf.samplers = []


def _b3dm_from_chunk(chunk: MeshChunk) -> b3dm.B3dm:
    colors_attr = GltfAttribute("COLOR_0", pygltflib.VEC3, pygltflib.UNSIGNED_BYTE, chunk.colors)
    material = pygltflib.Material(
        pbrMetallicRoughness=pygltflib.PbrMetallicRoughness(
            baseColorFactor=[1.0, 1.0, 1.0, 1.0],
            metallicFactor=0.0,
            roughnessFactor=1.0,
        ),
        doubleSided=True,
        alphaMode=pygltflib.OPAQUE,
        extensions={"KHR_materials_unlit": {}},
    )
    mesh = GltfMesh(
        chunk.points_local,
        primitives=[GltfPrimitive(triangles=chunk.triangles, material=material)],
        normals=chunk.normals,
        additional_attributes=[colors_attr],
    )
    tile_content = b3dm.B3dm.from_meshes([mesh])
    _sanitize_gltf_for_cesium(tile_content.body.gltf)
    return tile_content


def _geometric_error_meters(bounds_source: tuple[float, float, float, float]) -> float:
    left, bottom, right, top = bounds_source
    diagonal = float(np.hypot(right - left, top - bottom))
    return max(diagonal / 32.0, 8.0)


def generate_terrain_3d(
    dtm_path: Path,
    output_dir: Path,
    *,
    options: Terrain3DOptions | None = None,
    metadata: dict | None = None,
) -> Path:
    opts = options or Terrain3DOptions()
    opts.tiles_x = 1
    opts.tiles_y = 1
    config = build_terrain_config(dtm_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    tiles_dir = output_dir / "tiles"
    tiles_dir.mkdir(parents=True, exist_ok=True)

    root_error = _geometric_error_meters(config.bounds_source) * 4.0
    tileset = TileSet(geometric_error=root_error)
    root = tileset.root_tile
    root.refine_mode = "ADD"
    root.geometric_error = root_error

    chunks = list(_iter_dtm_chunks(dtm_path, config, opts))

    if not chunks:
        raise ValueError("No 3D mesh geometry produced from DTM/DSM.")

    for tile_x, tile_y, chunk in chunks:
        b3dm_name = f"tile_{tile_x}_{tile_y}.b3dm"
        content_uri = Path("tiles") / b3dm_name
        child = Tile(
            geometric_error=0.0,
            bounding_volume=BoundingVolumeBox.from_points(chunk.points_local.astype(np.float64)),
            transform=chunk.transform,
            content_uri=content_uri,
            refine_mode="REPLACE",
        )
        child.tile_content = _b3dm_from_chunk(chunk)
        root.add_child(child)

    root.sync_bounding_volume_with_children()
    tileset_path = output_dir / "tileset.json"
    tileset.write_to_directory(tileset_path, overwrite=True)

    meta = {
        "type": "3dtiles",
        "version": "1.0",
        "generator": "terrain_3d",
        "bounds_wgs84": list(config.bounds_wgs84),
        "bounds_source": list(config.bounds_source),
        "crs": config.crs,
        "elevation_vmin": config.elevation_vmin,
        "elevation_vmax": config.elevation_vmax,
        "mesh_step": opts.step,
        "mesh_grid": [opts.tiles_x, opts.tiles_y],
        "vertical_exaggeration": opts.vertical_exaggeration,
        "source": dtm_path.name,
    }

    if metadata:
        meta.update(metadata)

    (output_dir / "terrain3d.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    write_cesium_viewer(output_dir, title=meta.get("label", "Droid Survair 3D Terrain"))
    return tileset_path


def write_cesium_viewer(output_dir: Path, title: str = "Droid Survair 3D Terrain") -> Path:
    meta_path = output_dir / "terrain3d.json"
    meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    bounds = meta.get("bounds_wgs84")
    bounds_js = "null"

    if bounds and len(bounds) == 4:
        west, south, east, north = bounds
        bounds_js = f"{{ west: {west}, south: {south}, east: {east}, north: {north} }}"

    html = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{title}</title>
  <link rel="stylesheet" href="https://cesium.com/downloads/cesiumjs/releases/1.119/Build/Cesium/Widgets/widgets.css" />
  <style>
    html, body, #cesiumContainer {{ width: 100%; height: 100%; margin: 0; padding: 0; overflow: hidden; background: #092c34; }}
    #toolbar {{
      position: absolute; top: 12px; left: 12px; z-index: 10;
      width: 320px; padding: 12px 14px; color: #fff;
      background: rgba(14, 62, 73, 0.94); border: 1px solid rgba(121,217,206,.28);
      border-radius: 8px; font: 13px/1.45 Montserrat, Segoe UI, sans-serif;
      box-shadow: 0 10px 30px rgba(0,0,0,.25);
    }}
    #toolbar strong {{ display: block; margin-bottom: 8px; }}
    #toolbar label {{ display: block; margin-top: 10px; color: #a5b8c2; font-size: 11px; font-weight: 700; }}
    #toolbar input[type=range] {{ width: 100%; }}
    #status {{ margin-top: 8px; color: #79d9ce; font-size: 11px; }}
    #err {{ color: #ffd5cd; }}
  </style>
</head>
<body>
  <div id="cesiumContainer"></div>
  <div id="toolbar">
    <strong>{title}</strong>
    <div id="status">Loading 3D terrain...</div>
    <label>Vertical exaggeration
      <input type="range" id="exag" min="1" max="8" step="0.5" value="1" />
      <span id="exagVal">1x</span>
    </label>
  </div>
  <script src="https://cesium.com/downloads/cesiumjs/releases/1.119/Build/Cesium/Cesium.js"></script>
  <script>
    const surveyBounds = {bounds_js};
    const status = document.getElementById("status");
    function setStatus(message, error) {{
      status.innerHTML = error ? '<span id="err">' + message + '</span>' : message;
    }}

    const viewer = new Cesium.Viewer("cesiumContainer", {{
      animation: false,
      timeline: false,
      geocoder: false,
      homeButton: true,
      sceneModePicker: true,
      navigationHelpButton: false,
      baseLayerPicker: true,
      requestRenderMode: false,
    }});
    viewer.scene.globe.depthTestAgainstTerrain = false;
    viewer.scene.globe.show = false;
    viewer.scene.backgroundColor = Cesium.Color.fromCssColorString("#092c34");
    if (viewer.scene.skyBox) viewer.scene.skyBox.show = false;
    if (viewer.scene.skyAtmosphere) viewer.scene.skyAtmosphere.show = false;

    let tileset = null;
    let baseMatrix = Cesium.Matrix4.IDENTITY.clone();

    function flyToSurvey() {{
      if (!surveyBounds) return Promise.resolve();
      return new Promise((resolve) => {{
        viewer.camera.flyTo({{
          destination: Cesium.Rectangle.fromDegrees(
            surveyBounds.west, surveyBounds.south, surveyBounds.east, surveyBounds.north
          ),
          duration: 1.2,
          complete: resolve,
        }});
      }});
    }}

    function readyPromise(ts) {{
      if (ts.ready && typeof ts.ready.then === "function") return ts.ready;
      if (ts.readyPromise && typeof ts.readyPromise.then === "function") return ts.readyPromise;
      return Promise.resolve();
    }}

    function flyToTileset(ts) {{
      return readyPromise(ts).then(() => {{
        const sphere = ts.boundingSphere;
        if (!sphere || !isFinite(sphere.radius) || sphere.radius <= 0) return flyToSurvey();
        return new Promise((resolve) => {{
          viewer.camera.flyToBoundingSphere(sphere, {{
            duration: 1.2,
            complete: resolve,
            offset: new Cesium.HeadingPitchRange(0, Cesium.Math.toRadians(-45), Math.max(sphere.radius * 2.5, 120)),
          }});
        }});
      }});
    }}

    function updateStatus() {{
      if (!tileset || !tileset.statistics) return;
      const stats = tileset.statistics;
      const ready = stats.numberOfTilesWithContentReady || 0;
      const loading = stats.numberOfTilesLoading || 0;
      setStatus(loading > 0 ? `Loading terrain... ${{ready}} ready, ${{loading}} loading` : `Terrain loaded (${{ready}} tiles).`);
    }}

    Cesium.Cesium3DTileset.fromUrl("tileset.json", {{
      maximumScreenSpaceError: 8,
      dynamicScreenSpaceError: false,
      preloadWhenHidden: true,
      maximumMemoryUsage: 4096,
      maximumNumberOfLoadedTiles: 64,
    }}).then((ts) => {{
      tileset = ts;
      baseMatrix = Cesium.Matrix4.clone(ts.modelMatrix, baseMatrix);
      viewer.scene.primitives.add(ts);
      ts.tileLoad.addEventListener(updateStatus);
      ts.tileUnload.addEventListener(updateStatus);
      ts.allTilesLoaded.addEventListener(updateStatus);
      return flyToTileset(ts);
    }}).then(updateStatus).catch((error) => {{
      console.error(error);
      setStatus("Could not load 3D terrain: " + error, true);
      return flyToSurvey();
    }});

    viewer.homeButton.viewModel.command.beforeExecute.addEventListener((event) => {{
      event.cancel = true;
      if (tileset) flyToTileset(tileset);
      else flyToSurvey();
    }});

    function applyExag(value) {{
      document.getElementById("exagVal").textContent = value + "x";
      if (!tileset) return;
      tileset.modelMatrix = Cesium.Matrix4.multiplyByUniformScale(baseMatrix, value, new Cesium.Matrix4());
    }}
    document.getElementById("exag").addEventListener("input", (event) => applyExag(Number(event.target.value)));
  </script>
</body>
</html>
"""
    out = output_dir / "viewer_3d.html"
    out.write_text(html, encoding="utf-8")
    return out
