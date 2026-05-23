from __future__ import annotations

import hashlib
import io
import json
import math
import shutil
import time
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

DEM_COLOR_STOPS = np.array(
    [
        [0.00, 30, 50, 200],
        [0.25, 0, 225, 225],
        [0.50, 0, 210, 0],
        [0.75, 230, 230, 0],
        [1.00, 230, 30, 30],
    ],
    dtype=np.float32,
)


def _normalize_dataset_type(value: str, fallback_name: str = "") -> str:
    normalized = (value or "").strip().lower().replace(" ", "")
    aliases = {
        "orthomosaic": "ortho",
        "ortho": "ortho",
        "dtm": "dtm",
        "dem": "dtm",
        "dsm": "dsm",
        "pointcloud": "pointcloud",
        "3dmodel": "3dmodel",
        "3dtiles": "3dmodel",
        "cesium3dtiles": "3dmodel",
    }
    if normalized in aliases:
        return aliases[normalized]
    lowered = fallback_name.lower()
    if "dtm" in lowered or "dem" in lowered:
        return "dtm"
    if "dsm" in lowered:
        return "dsm"
    return "ortho"


def _zoom_for_raster_resolution(ground_res_m: float, latitude: float, max_zoom_limit: int) -> int:
    if not math.isfinite(ground_res_m) or ground_res_m <= 0:
        return min(max_zoom_limit, 18)
    lat_factor = max(math.cos(math.radians(latitude)), 0.15)
    for zoom in range(max_zoom_limit, -1, -1):
        meters_per_pixel = 156543.03392 * lat_factor / (2**zoom)
        if meters_per_pixel <= ground_res_m * 1.75:
            return zoom
    return max_zoom_limit


def _save_png_tile(rgba: np.ndarray, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(rgba, mode="RGBA")
    buffer = io.BytesIO()
    # Single-pass PNG writing is much faster for interactive portal uploads.
    img.save(buffer, format="PNG", optimize=False, compress_level=4)
    data = buffer.getvalue()
    out_path.write_bytes(data)
    return len(data)


def _compact_tile_tasks(mercantile_module, bounds_wgs84: tuple[float, float, float, float], max_zoom: int):
    west, south, east, north = bounds_wgs84
    for zoom in range(0, max_zoom + 1):
        for tile in mercantile_module.tiles(west, south, east, north, [zoom]):
            yield zoom, tile.x, tile.y


def _tile_tasks_in_range(mercantile_module, bounds_wgs84: tuple[float, float, float, float], min_zoom: int, max_zoom: int):
    west, south, east, north = bounds_wgs84
    for zoom in range(min_zoom, max_zoom + 1):
        for tile in mercantile_module.tiles(west, south, east, north, [zoom]):
            yield zoom, tile.x, tile.y


def _choose_compact_zoom(
    mercantile_module,
    bounds_wgs84: tuple[float, float, float, float],
    desired_max_zoom: int,
    dataset_type: str,
    tile_budget_mb: float,
    min_zoom: int = 0,
) -> tuple[int, int]:
    avg_kb = 70 if dataset_type in {"dtm", "dsm"} else 110
    budget_tiles = max(1, int((tile_budget_mb * 1024) / avg_kb))
    chosen_zoom = min_zoom
    chosen_count = 1
    for zoom in range(min_zoom, desired_max_zoom + 1):
        count = 0
        for z in range(min_zoom, zoom + 1):
            count += sum(1 for _ in mercantile_module.tiles(*bounds_wgs84, [z]))
        if count <= budget_tiles:
            chosen_zoom = zoom
            chosen_count = count
        else:
            break
    return chosen_zoom, chosen_count


def _sample_raster_percentiles(src, dataset_type: str) -> tuple[float, float] | None:
    if dataset_type not in {"dtm", "dsm"}:
        return None
    from rasterio.windows import Window

    samples: list[np.ndarray] = []
    windows = [
        (max(0, src.width // 4), max(0, src.height // 4), max(1, src.width // 2), max(1, src.height // 2)),
        (0, 0, max(1, src.width // 3), max(1, src.height // 3)),
        (
            max(0, src.width - max(1, src.width // 3)),
            max(0, src.height - max(1, src.height // 3)),
            max(1, src.width // 3),
            max(1, src.height // 3),
        ),
    ]
    for col, row, width, height in windows:
        block = src.read(1, window=Window(col, row, width, height), masked=False)
        valid = np.isfinite(block)
        if src.nodata is not None:
            valid &= block != src.nodata
        if np.any(valid):
            samples.append(block[valid])
    if not samples:
        return None
    values = np.concatenate(samples)
    return float(np.percentile(values, 5)), float(np.percentile(values, 95))


def _dem_lut() -> np.ndarray:
    positions = DEM_COLOR_STOPS[:, 0]
    colors = DEM_COLOR_STOPS[:, 1:4]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for idx in range(256):
        t = idx / 255.0
        stop_idx = int(np.searchsorted(positions, t, side="right") - 1)
        stop_idx = max(0, min(stop_idx, len(positions) - 2))
        t0, t1 = positions[stop_idx], positions[stop_idx + 1]
        frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        rgb = colors[stop_idx] + frac * (colors[stop_idx + 1] - colors[stop_idx])
        lut[idx] = np.clip(rgb, 0, 255).astype(np.uint8)
    return lut


def _compute_hillshade(elev: np.ndarray, valid: np.ndarray, res_x: float, res_y: float) -> np.ndarray:
    if not np.any(valid):
        return np.ones(elev.shape, dtype=np.float32)
    fill_value = float(np.nanmedian(elev[valid]))
    filled = np.where(valid, elev, fill_value)
    dx = (np.roll(filled, -1, 1) - np.roll(filled, 1, 1)) / max(2.0 * res_x, 1e-6)
    dy = (np.roll(filled, -1, 0) - np.roll(filled, 1, 0)) / max(2.0 * res_y, 1e-6)
    if dx.shape[1] > 2:
        dx[:, 0], dx[:, -1] = dx[:, 1], dx[:, -2]
    if dy.shape[0] > 2:
        dy[0, :], dy[-1, :] = dy[1, :], dy[-2, :]
    slope = np.arctan(np.hypot(dx, dy))
    aspect = np.arctan2(dy, -dx)
    az = np.radians(315.0)
    alt = np.radians(45.0)
    shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(((shade + 1.0) * 0.5).astype(np.float32), 0.0, 1.0)


def _elevation_to_rgba(data: np.ndarray, nodata: float | None, vmin: float, vmax: float, pixel_size: tuple[float, float]) -> np.ndarray:
    h, w = data.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(data)
    if nodata is not None:
        valid &= data != nodata
    if not np.any(valid):
        return out
    span = max(vmax - vmin, 1e-6)
    norm = np.clip((data - vmin) / span, 0.0, 1.0)
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    rgb[valid] = _dem_lut()[(norm[valid] * 255.0).astype(np.uint8)].astype(np.float32)
    shade = _compute_hillshade(data, valid, pixel_size[0], pixel_size[1])
    rgb = rgb * (0.62 + 0.38 * shade[:, :, None])
    out[:, :, :3] = np.clip(rgb, 0, 255).astype(np.uint8)
    out[valid, 3] = 255
    return out


def _read_ortho_tile(src, bounds_3857: tuple[float, float, float, float], zoom: int, tile_size: int) -> np.ndarray:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    west, south, east, north = bounds_3857
    transform = from_bounds(west, south, east, north, tile_size, tile_size)
    dst = np.zeros((3, tile_size, tile_size), dtype=np.float32)
    alpha_src = (src.read_masks(1) > 0).astype(np.uint8)
    alpha_dst = np.zeros((tile_size, tile_size), dtype=np.uint8)
    band_count = min(max(src.count, 1), 3)
    resampling = Resampling.bilinear if zoom >= 17 else Resampling.nearest
    for band in range(1, band_count + 1):
        reproject(
            source=rasterio.band(src, band),
            destination=dst[band - 1],
            src_transform=src.transform,
            src_crs=src.crs,
            src_nodata=src.nodata,
            dst_transform=transform,
            dst_crs=CRS.from_epsg(3857),
            dst_nodata=0,
            resampling=resampling,
        )
    reproject(
        source=alpha_src,
        destination=alpha_dst,
        src_transform=src.transform,
        src_crs=src.crs,
        src_nodata=0,
        dst_transform=transform,
        dst_crs=CRS.from_epsg(3857),
        dst_nodata=0,
        resampling=Resampling.nearest,
    )
    if band_count == 1:
        dst[1] = dst[0]
        dst[2] = dst[0]
    elif band_count == 2:
        dst[2] = dst[1]
    if max(float(np.nanmax(dst)), 0.0) > 255:
        dst = np.clip(dst / 256.0, 0, 255)
    rgb = np.clip(dst, 0, 255).astype(np.uint8)
    rgba = np.zeros((tile_size, tile_size, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.moveaxis(rgb, 0, -1)
    is_black = np.all(rgb < 8, axis=0)
    band_min = rgb.min(axis=0)
    band_max = rgb.max(axis=0)
    is_white_pad = (band_min >= 248) & ((band_max - band_min) <= 12)
    rgba[(alpha_dst > 0) & ~(is_black | is_white_pad), 3] = 255
    return rgba


def _read_dem_tile(src, bounds_3857: tuple[float, float, float, float], vmin: float, vmax: float, zoom: int, tile_size: int) -> np.ndarray:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    west, south, east, north = bounds_3857
    transform = from_bounds(west, south, east, north, tile_size, tile_size)
    dst = np.full((tile_size, tile_size), np.nan, dtype=np.float32)
    resampling = Resampling.bilinear if zoom >= 17 else Resampling.nearest
    reproject(
        source=rasterio.band(src, 1),
        destination=dst,
        src_transform=src.transform,
        src_crs=src.crs,
        src_nodata=src.nodata,
        dst_transform=transform,
        dst_crs=CRS.from_epsg(3857),
        dst_nodata=np.nan,
        resampling=resampling,
    )
    return _elevation_to_rgba(dst, src.nodata, vmin, vmax, (abs(transform.a), abs(transform.e)))


def _fingerprint(path: Path) -> str:
    stat = path.stat()
    raw = f"{path.resolve().as_posix()}:{stat.st_size}:{stat.st_mtime_ns}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:24]


def run_rasterio_tiler(
    input_tif: str,
    output_dir: str,
    project_id: str,
    dataset_name: str,
    dataset_type: str,
    local_data_path: str,
    tile_budget_mb: float = 100,
    min_zoom_limit: int = 14,
    max_zoom_limit: int = 20,
    tile_size: int = 256,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> None:
    try:
        import mercantile
        import rasterio
        from rasterio.warp import transform_bounds
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Rasterio tiler needs rasterio, mercantile, numpy, and Pillow installed.") from exc

    normalized_type = _normalize_dataset_type(dataset_type, dataset_name)
    min_zoom_limit = max(0, int(min_zoom_limit))
    max_zoom_limit = max(min_zoom_limit, int(max_zoom_limit))
    in_abs = Path(input_tif).resolve()
    out_abs = Path(output_dir).resolve()
    local_root = Path(local_data_path).resolve()
    if not out_abs.is_relative_to(local_root):
        raise RuntimeError("Refusing to write tiles outside Project_Data")
    if out_abs.exists():
        shutil.rmtree(out_abs)
    out_abs.mkdir(parents=True, exist_ok=True)
    last_progress_emit = 0.0

    def emit_progress(stage: str, progress: float, **extra: object) -> None:
        nonlocal last_progress_emit
        if not progress_callback:
            return
        now = time.time()
        if progress < 99 and now - last_progress_emit < 0.7:
            return
        last_progress_emit = now
        payload: dict[str, object] = {
            "stage": stage,
            "progress_percent": round(max(1.0, min(99.0, progress)), 1),
            **extra,
        }
        progress_callback(payload)

    emit_progress("Opening GeoTIFF", 8)
    with rasterio.open(in_abs) as src:
        if not src.crs:
            raise RuntimeError("TIFF has no CRS. Please export with EPSG/CRS before upload.")
        emit_progress("Reading bounds and CRS", 14)
        bounds_wgs84_raw = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        bounds_wgs84 = (
            max(-180.0, bounds_wgs84_raw[0]),
            max(-85.05112878, bounds_wgs84_raw[1]),
            min(180.0, bounds_wgs84_raw[2]),
            min(85.05112878, bounds_wgs84_raw[3]),
        )
        center_lat = (bounds_wgs84[1] + bounds_wgs84[3]) / 2.0
        ground_res = min(abs(float(src.res[0])), abs(float(src.res[1])))
        desired_zoom = _zoom_for_raster_resolution(ground_res, center_lat, max_zoom_limit)
        desired_zoom = max(min_zoom_limit, desired_zoom)
        max_zoom, estimated_tiles = _choose_compact_zoom(mercantile, bounds_wgs84, desired_zoom, normalized_type, tile_budget_mb, min_zoom_limit)
        emit_progress("Planning compact tile pyramid", 22, estimated_tiles=estimated_tiles, zoom_max=max_zoom)
        dem_range = _sample_raster_percentiles(src, normalized_type)
        if normalized_type in {"dtm", "dsm"} and dem_range is None:
            raise RuntimeError("No valid elevation cells found in DEM TIFF.")
        emit_progress("Rendering web map tiles", 28, estimated_tiles=estimated_tiles, zoom_max=max_zoom)

        meta: dict[str, object] = {
            "engine": "python-rasterio",
            "scheme": "xyz",
            "crs": "EPSG:3857",
            "source_crs": str(src.crs),
            "source_fingerprint": _fingerprint(in_abs),
            "bounds_wgs84": list(bounds_wgs84),
            "zoom_min": min_zoom_limit,
            "zoom_max": max_zoom,
            "tile_size": tile_size,
            "dataset_type": normalized_type,
            "dataset_name": dataset_name,
            "tile_budget_mb": tile_budget_mb,
            "estimated_tile_count": estimated_tiles,
        }
        if dem_range:
            meta["elevation_vmin"], meta["elevation_vmax"] = dem_range

        bytes_written = 0
        tiles_written = 0
        started = time.time()
        for zoom, x, y in _tile_tasks_in_range(mercantile, bounds_wgs84, min_zoom_limit, max_zoom):
            tile_bounds = mercantile.xy_bounds(x, y, zoom)
            bounds_3857 = (tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top)
            if normalized_type in {"dtm", "dsm"}:
                rgba = _read_dem_tile(src, bounds_3857, dem_range[0], dem_range[1], zoom, tile_size)  # type: ignore[index]
            else:
                rgba = _read_ortho_tile(src, bounds_3857, zoom, tile_size)
            bytes_written += _save_png_tile(rgba, out_abs / str(zoom) / str(x) / f"{y}.png")
            tiles_written += 1
            elapsed = max(time.time() - started, 0.1)
            if estimated_tiles > 0:
                render_fraction = min(tiles_written / estimated_tiles, 1.0)
                eta_seconds = max(0, int((elapsed / max(render_fraction, 0.01)) - elapsed))
                emit_progress(
                    "Rendering web map tiles",
                    28 + render_fraction * 62,
                    estimated_tiles=estimated_tiles,
                    tiles_written=tiles_written,
                    eta_seconds=eta_seconds,
                    zoom_max=max_zoom,
                )

    emit_progress("Optimizing tile package", 93, estimated_tiles=estimated_tiles, tiles_written=tiles_written)
    budget_bytes = int(tile_budget_mb * 1024 * 1024)
    while bytes_written > budget_bytes and max_zoom > min_zoom_limit:
        zoom_dir = out_abs / str(max_zoom)
        removed_bytes = sum(p.stat().st_size for p in zoom_dir.rglob("*.png")) if zoom_dir.is_dir() else 0
        if zoom_dir.is_dir():
            shutil.rmtree(zoom_dir)
        bytes_written = max(0, bytes_written - int(removed_bytes))
        max_zoom -= 1
        meta["zoom_max"] = max_zoom

    meta["tiles_written"] = tiles_written
    meta["bytes_written"] = bytes_written
    meta["elapsed_seconds"] = round(time.time() - started, 2)
    (out_abs / "tileset.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    emit_progress("Finalizing dataset", 99, estimated_tiles=estimated_tiles, tiles_written=tiles_written, eta_seconds=0)
    print(
        "Rasterio tiles ready: "
        f"project={project_id}, dataset={dataset_name}, type={normalized_type}, "
        f"zoom={min_zoom_limit}-{max_zoom}, tiles={tiles_written}, size={bytes_written / (1024 * 1024):.1f} MB"
    )
