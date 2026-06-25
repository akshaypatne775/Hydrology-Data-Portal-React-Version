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
        [0.00, 0, 0, 130],
        [0.25, 0, 255, 255],
        [0.50, 0, 255, 0],
        [0.75, 255, 255, 0],
        [1.00, 139, 0, 0],
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


def _normalize_source_epsg(value: str) -> str:
    clean = (value or "").strip().upper().replace(" ", "")
    if not clean:
        return ""
    if clean.startswith("EPSG:"):
        clean = clean[5:]
    if not clean.isdigit() or len(clean) < 4 or len(clean) > 6:
        raise RuntimeError("Invalid EPSG code. Use EPSG:32644 or 32644.")
    return f"EPSG:{clean}"


def calculate_percentile_rescale(raster_path: str | Path) -> dict[str, float] | None:
    try:
        import rasterio
        from rasterio.enums import Resampling
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Percentile rescale calculation needs rasterio installed.") from exc

    with rasterio.open(Path(raster_path).resolve()) as dataset:
        if dataset.count < 1:
            return None
        max_preview_size = 1024
        scale = max(dataset.width / max_preview_size, dataset.height / max_preview_size, 1)
        out_width = max(1, int(dataset.width / scale))
        out_height = max(1, int(dataset.height / scale))
        data = dataset.read(
            1,
            out_shape=(out_height, out_width),
            masked=True,
            resampling=Resampling.bilinear,
        )

    values = data.compressed() if np.ma.isMaskedArray(data) else data.reshape(-1)
    values = values[np.isfinite(values)]
    if values.size == 0:
        return None
    low, high = np.percentile(values, [5, 95])
    if float(low) == float(high):
        high = low + 1
    return {
        "min": round(float(low), 3),
        "max": round(float(high), 3),
    }


def _scale_rgb_to_uint8(data: np.ndarray) -> np.ndarray:
    values = np.nan_to_num(data.astype("float32"), nan=0.0, posinf=255.0, neginf=0.0)
    if values.size and float(np.nanmax(values)) <= 1.0:
        values = values * 255.0
    return np.clip(values, 0, 255).astype("uint8")


def _scale_alpha_to_uint8(data: np.ndarray) -> np.ndarray:
    values = np.nan_to_num(data.astype("float32"), nan=0.0, posinf=255.0, neginf=0.0)
    if values.size and float(np.nanmax(values)) <= 1.0:
        values = values * 255.0
    return np.clip(values, 0, 255).astype("uint8")


def _edge_connected_mask(candidate: np.ndarray) -> np.ndarray:
    if candidate.ndim != 2 or not np.any(candidate):
        return np.zeros(candidate.shape, dtype=bool)

    height, width = candidate.shape
    visited = np.zeros(candidate.shape, dtype=bool)
    stack: list[tuple[int, int]] = []

    for col in range(width):
        if candidate[0, col]:
            stack.append((0, col))
        if height > 1 and candidate[height - 1, col]:
            stack.append((height - 1, col))
    for row in range(1, max(height - 1, 1)):
        if candidate[row, 0]:
            stack.append((row, 0))
        if width > 1 and candidate[row, width - 1]:
            stack.append((row, width - 1))

    while stack:
        row, col = stack.pop()
        if visited[row, col] or not candidate[row, col]:
            continue
        visited[row, col] = True
        if row > 0 and not visited[row - 1, col] and candidate[row - 1, col]:
            stack.append((row - 1, col))
        if row + 1 < height and not visited[row + 1, col] and candidate[row + 1, col]:
            stack.append((row + 1, col))
        if col > 0 and not visited[row, col - 1] and candidate[row, col - 1]:
            stack.append((row, col - 1))
        if col + 1 < width and not visited[row, col + 1] and candidate[row, col + 1]:
            stack.append((row, col + 1))

    return visited


def _build_ortho_preview_padding_mask(src, max_preview_size: int = 4096) -> np.ndarray:
    from rasterio.enums import Resampling

    scale = max(src.width / max_preview_size, src.height / max_preview_size, 1)
    preview_width = max(1, int(src.width / scale))
    preview_height = max(1, int(src.height / scale))
    preview = src.read(
        [1, 2, 3],
        out_shape=(3, preview_height, preview_width),
        masked=True,
        resampling=Resampling.nearest,
    )
    rgb = _scale_rgb_to_uint8(np.ma.filled(preview, 0))
    base_invalid = np.any(np.ma.getmaskarray(preview), axis=0)

    white_padding = _edge_connected_mask(np.all(rgb >= 248, axis=0) & ~base_invalid)
    black_padding = _edge_connected_mask(np.all(rgb <= 3, axis=0) & ~base_invalid)
    return base_invalid | white_padding | black_padding


def _preview_mask_for_window(preview_mask: np.ndarray, window, src_width: int, src_height: int) -> np.ndarray:
    rows = np.arange(int(window.height), dtype=np.float64) + float(window.row_off) + 0.5
    cols = np.arange(int(window.width), dtype=np.float64) + float(window.col_off) + 0.5
    preview_rows = np.clip((rows * preview_mask.shape[0] / src_height).astype(np.int64), 0, preview_mask.shape[0] - 1)
    preview_cols = np.clip((cols * preview_mask.shape[1] / src_width).astype(np.int64), 0, preview_mask.shape[1] - 1)
    return preview_mask[np.ix_(preview_rows, preview_cols)]


def _build_ortho_masked_source(input_tif: Path, masked_tif: Path) -> Path:
    import rasterio

    with rasterio.open(input_tif) as src:
        if src.count < 3:
            return input_tif
        preview_padding_mask = _build_ortho_preview_padding_mask(src)

        profile = src.profile.copy()
        profile.update(
            driver="GTiff",
            count=3,
            dtype="uint8",
            nodata=None,
            tiled=True,
            blockxsize=512,
            blockysize=512,
            compress="deflate",
            predictor=2,
            BIGTIFF="IF_SAFER",
        )
        profile.pop("photometric", None)
        profile.pop("alpha", None)

        masked_tif.unlink(missing_ok=True)
        with rasterio.open(masked_tif, "w", **profile) as dst:
            for _, window in src.block_windows(1):
                raw = src.read([1, 2, 3], window=window, masked=True)
                rgb = _scale_rgb_to_uint8(np.ma.filled(raw, 0))
                alpha = np.full(rgb.shape[1:], 255, dtype="uint8")

                if np.ma.isMaskedArray(raw):
                    alpha[np.any(np.ma.getmaskarray(raw), axis=0)] = 0

                masks = src.read_masks([1, 2, 3], window=window)
                alpha[np.any(masks == 0, axis=0)] = 0

                if src.nodata is not None and np.isfinite(float(src.nodata)):
                    nodata_values = src.read([1, 2, 3], window=window, masked=False)
                    alpha[np.any(np.isclose(nodata_values, float(src.nodata)), axis=0)] = 0

                if src.count >= 4:
                    source_alpha = _scale_alpha_to_uint8(src.read(4, window=window, masked=False))
                    alpha = np.minimum(alpha, source_alpha)

                alpha[_preview_mask_for_window(preview_padding_mask, window, src.width, src.height)] = 0

                dst.write(rgb, window=window)
                dst.write_mask(alpha, window=window)

    return masked_tif


def convert_tif_to_cog(
    input_tif: str,
    output_cog: str,
    dataset_name: str,
    dataset_type: str,
    local_data_path: str,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
    source_epsg: str = "",
) -> dict[str, object]:
    try:
        import rasterio
        from rasterio.crs import CRS
        from rasterio.warp import transform_bounds
        from rio_cogeo.cogeo import cog_translate
        from rio_cogeo.profiles import cog_profiles
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("COG conversion needs rasterio, rio-cogeo, and rio-tiler installed.") from exc

    normalized_type = _normalize_dataset_type(dataset_type, dataset_name)
    in_abs = Path(input_tif).resolve()
    out_abs = Path(output_cog).resolve()
    local_root = Path(local_data_path).resolve()
    if not out_abs.is_relative_to(local_root):
        raise RuntimeError("Refusing to write COG outside Project_Data")
    out_abs.parent.mkdir(parents=True, exist_ok=True)
    out_abs.unlink(missing_ok=True)
    last_progress_emit = 0.0

    def emit_progress(stage: str, progress: float, **extra: object) -> None:
        nonlocal last_progress_emit
        if not progress_callback:
            return
        now = time.time()
        if progress < 99 and now - last_progress_emit < 0.5:
            return
        last_progress_emit = now
        progress_callback(
            {
                "stage": stage,
                "progress_percent": round(max(1.0, min(99.0, progress)), 1),
                **extra,
            },
        )

    emit_progress("Opening GeoTIFF", 8)
    normalized_source_epsg = _normalize_source_epsg(source_epsg)
    applied_epsg = ""
    manual_crs = CRS.from_user_input(normalized_source_epsg) if normalized_source_epsg else None
    with rasterio.open(in_abs) as src:
        has_source_crs = bool(src.crs)
    if not has_source_crs and manual_crs:
        emit_progress(f"Assigning {normalized_source_epsg}", 10)
        with rasterio.open(in_abs, "r+") as src:
            src.crs = manual_crs
        applied_epsg = normalized_source_epsg

    with rasterio.open(in_abs) as src:
        if not src.crs:
            raise RuntimeError("EPSG required: TIFF has no CRS. Enter EPSG manually (example EPSG:32644) and upload again.")
        bounds_wgs84_raw = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        bounds_wgs84 = [
            max(-180.0, float(bounds_wgs84_raw[0])),
            max(-85.05112878, float(bounds_wgs84_raw[1])),
            min(180.0, float(bounds_wgs84_raw[2])),
            min(85.05112878, float(bounds_wgs84_raw[3])),
        ]
        source_crs = str(src.crs)
        band_count = int(src.count)

    translate_source = in_abs
    temporary_source: Path | None = None

    if normalized_type in {"dtm", "dsm"}:
        emit_progress("Preparing fast elevation COG profile", 18)
        profile_name = "deflate"
        dst_profile = dict(cog_profiles.get(profile_name))
        dst_profile.update(
            {
                "compress": "LERC_ZSTD",
                "max_z_error": 0.001,
                "bigtiff": "IF_SAFER",
                "blockxsize": 512,
                "blockysize": 512,
                "num_threads": "ALL_CPUS",
                "OVERVIEWS": "AUTO",
            },
        )
    else:
        emit_progress("Preparing fast Ortho COG profile", 18)
        temporary_source = out_abs.parent / f"{out_abs.stem}.masked-source.tif"
        emit_progress("Removing ortho no-data padding", 24)
        translate_source = _build_ortho_masked_source(in_abs, temporary_source)
        profile_name = "jpeg"
        dst_profile = dict(cog_profiles.get(profile_name))
        dst_profile.update(
            {
                "compress": "JPEG",
                "bigtiff": "IF_SAFER",
                "blockxsize": 512,
                "blockysize": 512,
                "photometric": "YCbCr",
                "quality": 85,
                "num_threads": "ALL_CPUS",
                "OVERVIEWS": "AUTO",
            },
        )
    emit_progress("Converting GeoTIFF to COG", 35)
    started = time.time()
    try:
        try:
            cog_translate(
                str(translate_source),
                str(out_abs),
                dst_profile,
                in_memory=False,
                quiet=True,
            )
        except TypeError:
            cog_translate(
                str(translate_source),
                str(out_abs),
                dst_profile,
                in_memory=False,
                quiet=True,
            )
    finally:
        if temporary_source and temporary_source != in_abs:
            temporary_source.unlink(missing_ok=True)
    emit_progress("Calculating DEM display range", 86)
    rescale = calculate_percentile_rescale(out_abs) if normalized_type in {"dtm", "dsm"} else None
    elapsed = round(time.time() - started, 2)
    emit_progress("Finalizing COG", 99, eta_seconds=0)
    return {
        "engine": "rio-cogeo",
        "cog_path": str(out_abs),
        "bounds_wgs84": bounds_wgs84,
        "source_crs": source_crs,
        "manual_epsg": normalized_source_epsg,
        "applied_epsg": applied_epsg,
        "band_count": band_count,
        "dataset_type": normalized_type,
        "rescale": rescale,
        "elapsed_seconds": elapsed,
        "bytes_written": out_abs.stat().st_size if out_abs.is_file() else 0,
    }


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


def _build_dji_terra_colormap() -> dict[int, tuple[int, int, int, int]]:
    stops = [
        (0.00, (0, 0, 130)),
        (0.25, (0, 255, 255)),
        (0.50, (0, 255, 0)),
        (0.75, (255, 255, 0)),
        (1.00, (139, 0, 0)),
    ]
    color_map: dict[int, tuple[int, int, int, int]] = {}
    for index in range(256):
        position = index / 255
        for stop_index in range(len(stops) - 1):
            left_pos, left_color = stops[stop_index]
            right_pos, right_color = stops[stop_index + 1]
            if left_pos <= position <= right_pos:
                ratio = (position - left_pos) / (right_pos - left_pos)
                rgb = tuple(
                    int(round(left_color[channel] + ratio * (right_color[channel] - left_color[channel])))
                    for channel in range(3)
                )
                color_map[index] = (*rgb, 255)
                break
    return color_map

def _normalize_epsg_input(value: str | None) -> str:
    clean = (value or "").strip().upper().replace(" ", "")
    if not clean:
        return ""
    if clean.startswith("EPSG:"):
        clean = clean[5:]
    if not re.fullmatch(r"\d{4,6}", clean):
        raise HTTPException(status_code=400, detail="Invalid EPSG code. Use EPSG:32644 or 32644.")
    return f"EPSG:{clean}"

def _transparent_png_tile() -> bytes:
    output = io.BytesIO()
    Image.fromarray(np.zeros((1, 1, 4), dtype=np.uint8), mode="RGBA").save(output, format="PNG")
    return output.getvalue()

def _parse_rescale_pair(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None
    try:
        low_raw, high_raw = value.split(",", 1)
        low = float(low_raw)
        high = float(high_raw)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(low) and math.isfinite(high)) or low == high:
        return None
    return (min(low, high), max(low, high))

def _render_dji_terra_dem_png(tile_array, rescale: tuple[float, float]) -> bytes:
    band = tile_array[0]
    values = np.ma.filled(band, np.nan).astype("float64")
    mask = np.ma.getmaskarray(band) | ~np.isfinite(values)
    low, high = rescale

    normalized = np.clip((values - low) / max(high - low, 1e-9), 0, 1)
    color_indexes = np.nan_to_num(normalized * 255, nan=0).astype("uint8")
    lookup = np.array([DJI_TERRA_DEM_CMAP[index] for index in range(256)], dtype="uint8")
    rgba = lookup[color_indexes].astype("float64")

    valid_values = values[~mask]
    fill_value = float(np.nanmean(valid_values)) if valid_values.size else 0.0
    elevation = np.where(mask, fill_value, values)

    dy, dx = np.gradient(elevation * 3.0)
    slope = np.arctan(np.sqrt((dx * dx) + (dy * dy)))
    aspect = np.arctan2(dy, -dx)
    azimuth = np.deg2rad(315.0)
    altitude = np.deg2rad(45.0)
    hillshade = (
        (np.sin(altitude) * np.cos(slope))
        + (np.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect))
    )
    hillshade = np.clip(np.nan_to_num(hillshade, nan=0.0, posinf=1.0, neginf=0.0), 0, 1)

    detail_shade = 0.32 + (0.68 * hillshade)
    rgba[..., :3] = np.clip(rgba[..., :3] * detail_shade[..., np.newaxis], 0, 255)
    rgba[mask, 3] = 0

    output = io.BytesIO()
    Image.fromarray(rgba.astype("uint8"), mode="RGBA").save(output, format="PNG")
    return output.getvalue()

def _render_dji_terra_tile(cog_path: Path, z: int, x: int, y: int, rescale: tuple[float, float]) -> bytes:
    with Reader(str(cog_path)) as dataset:
        tile = dataset.tile(x, y, z, tilesize=256)
    return _render_dji_terra_dem_png(tile.array, rescale)

def _edge_connected_padding_mask(candidate: np.ndarray) -> np.ndarray:
    """Return only candidate pixels connected to the tile edge.

    Drone orthos often have white/black padding around the real footprint.
    Masking every white pixel hides valid bright imagery, so only remove
    padding that touches the tile boundary.
    """
    if candidate.ndim != 2 or not np.any(candidate):
        return np.zeros(candidate.shape, dtype=bool)
    height, width = candidate.shape
    visited = np.zeros(candidate.shape, dtype=bool)
    stack: list[tuple[int, int]] = []

    for col in range(width):
        if candidate[0, col]:
            stack.append((0, col))
        if height > 1 and candidate[height - 1, col]:
            stack.append((height - 1, col))
    for row in range(1, max(height - 1, 1)):
        if candidate[row, 0]:
            stack.append((row, 0))
        if width > 1 and candidate[row, width - 1]:
            stack.append((row, width - 1))

    while stack:
        row, col = stack.pop()
        if visited[row, col] or not candidate[row, col]:
            continue
        visited[row, col] = True
        if row > 0 and not visited[row - 1, col] and candidate[row - 1, col]:
            stack.append((row - 1, col))
        if row + 1 < height and not visited[row + 1, col] and candidate[row + 1, col]:
            stack.append((row + 1, col))
        if col > 0 and not visited[row, col - 1] and candidate[row, col - 1]:
            stack.append((row, col - 1))
        if col + 1 < width and not visited[row, col + 1] and candidate[row, col + 1]:
            stack.append((row, col + 1))

    return visited

def _render_ortho_cog_png(tile_array) -> bytes:
    data = np.ma.filled(tile_array, 0) if np.ma.isMaskedArray(tile_array) else np.asarray(tile_array)
    if data.shape[0] < 3:
        return TRANSPARENT_PNG_TILE

    rgb = np.moveaxis(data[:3], 0, -1).astype("float64")
    if rgb.max(initial=0) <= 1:
        rgb *= 255.0
    rgb = np.clip(rgb, 0, 255).astype("uint8")
    alpha = np.full(rgb.shape[:2], 255, dtype="uint8")

    if data.shape[0] >= 4:
        source_alpha = data[3].astype("float64")
        if source_alpha.max(initial=0) <= 1:
            source_alpha *= 255.0
        alpha = np.minimum(alpha, np.clip(source_alpha, 0, 255).astype("uint8"))

    if np.ma.isMaskedArray(tile_array):
        mask = np.any(np.ma.getmaskarray(tile_array[:3]), axis=0)
        alpha[mask] = 0

    black_background = np.all(rgb < 8, axis=2)
    band_min = rgb.min(axis=2)
    band_max = rgb.max(axis=2)
    band_range = band_max - band_min
    bright_background = ((band_min >= 210) & (band_range <= 55)) | ((band_min >= 190) & (band_range <= 28))
    near_white_background = (band_min >= 225) & (band_range <= 18)
    padding_mask = _edge_connected_padding_mask(black_background | bright_background | near_white_background)
    alpha[padding_mask] = 0

    if not np.any(alpha):
        return TRANSPARENT_PNG_TILE

    output = io.BytesIO()
    Image.fromarray(np.dstack([rgb, alpha]), mode="RGBA").save(output, format="PNG")
    return output.getvalue()

def _render_ortho_cog_tile(cog_path: Path, z: int, x: int, y: int) -> bytes:
    with Reader(str(cog_path)) as dataset:
        tile = dataset.tile(x, y, z, tilesize=256)
    return _render_ortho_cog_png(tile.array)

def _read_cog_bounds_wgs84(cog_path: Path) -> list[float]:
    import rasterio  # type: ignore
    from rasterio.warp import transform_bounds

    with rasterio.open(str(cog_path)) as src:
        if not src.crs:
            raise HTTPException(status_code=422, detail="Raster CRS is missing")
        bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
    clean_bounds = [
        max(-180.0, float(bounds[0])),
        max(-85.05112878, float(bounds[1])),
        min(180.0, float(bounds[2])),
        min(85.05112878, float(bounds[3])),
    ]
    if not all(math.isfinite(value) for value in clean_bounds):
        raise HTTPException(status_code=422, detail="Raster bounds could not be transformed")
    return clean_bounds

def _zoom_for_raster_resolution(ground_res_m: float, latitude: float) -> int:
    if not math.isfinite(ground_res_m) or ground_res_m <= 0:
        return min(TIFF_TILE_MAX_ZOOM_LIMIT, 18)
    lat_factor = max(math.cos(math.radians(latitude)), 0.15)
    for zoom in range(TIFF_TILE_MAX_ZOOM_LIMIT, -1, -1):
        meters_per_pixel = 156543.03392 * lat_factor / (2**zoom)
        if meters_per_pixel <= ground_res_m * 1.75:
            return zoom
    return TIFF_TILE_MAX_ZOOM_LIMIT

def _save_png_tile(rgba: np.ndarray, out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img = Image.fromarray(rgba, mode="RGBA")
    best = b""
    for level in (6, 8, 9):
        buffer = io.BytesIO()
        img.save(buffer, format="PNG", optimize=True, compress_level=level)
        data = buffer.getvalue()
        if not best or len(data) < len(best):
            best = data
    out_path.write_bytes(best)
    return len(best)

def _compact_tile_tasks(mercantile_module, bounds_wgs84: tuple[float, float, float, float], max_zoom: int):
    west, south, east, north = bounds_wgs84
    for zoom in range(0, max_zoom + 1):
        for tile in mercantile_module.tiles(west, south, east, north, [zoom]):
            yield zoom, tile.x, tile.y

def _choose_compact_zoom(
    mercantile_module,
    bounds_wgs84: tuple[float, float, float, float],
    desired_max_zoom: int,
    dataset_type: str,
) -> tuple[int, int]:
    avg_kb = 70 if dataset_type in {"dtm", "dsm"} else 110
    budget_tiles = max(1, int((TIFF_TILE_BUDGET_MB * 1024) / avg_kb))
    chosen_zoom = 0
    chosen_count = 1
    for zoom in range(0, desired_max_zoom + 1):
        count = 0
        for z in range(0, zoom + 1):
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
    samples: list[np.ndarray] = []
    windows = [
        (
            max(0, src.width // 4),
            max(0, src.height // 4),
            max(1, src.width // 2),
            max(1, src.height // 2),
        ),
        (0, 0, max(1, src.width // 3), max(1, src.height // 3)),
        (
            max(0, src.width - max(1, src.width // 3)),
            max(0, src.height - max(1, src.height // 3)),
            max(1, src.width // 3),
            max(1, src.height // 3),
        ),
    ]
    try:
        from rasterio.windows import Window
    except Exception:
        return None
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

def _read_compact_ortho_tile(src, bounds_3857: tuple[float, float, float, float], zoom: int) -> np.ndarray:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    west, south, east, north = bounds_3857
    transform = from_bounds(west, south, east, north, TIFF_TILE_SIZE, TIFF_TILE_SIZE)
    dst = np.zeros((3, TIFF_TILE_SIZE, TIFF_TILE_SIZE), dtype=np.float32)
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
    if band_count == 1:
        dst[1] = dst[0]
        dst[2] = dst[0]
    elif band_count == 2:
        dst[2] = dst[1]

    if max(float(np.nanmax(dst)), 0.0) > 255:
        dst = np.clip(dst / 256.0, 0, 255)
    rgb = np.clip(dst, 0, 255).astype(np.uint8)
    rgba = np.zeros((TIFF_TILE_SIZE, TIFF_TILE_SIZE, 4), dtype=np.uint8)
    rgba[:, :, :3] = np.moveaxis(rgb, 0, -1)
    is_black = np.all(rgb < 8, axis=0)
    band_min = rgb.min(axis=0)
    band_max = rgb.max(axis=0)
    is_white_pad = (band_min >= 248) & ((band_max - band_min) <= 12)
    rgba[~(is_black | is_white_pad), 3] = 255
    return rgba

def _read_compact_dem_tile(
    src,
    bounds_3857: tuple[float, float, float, float],
    vmin: float,
    vmax: float,
    zoom: int,
) -> np.ndarray:
    import rasterio
    from rasterio.crs import CRS
    from rasterio.enums import Resampling
    from rasterio.transform import from_bounds
    from rasterio.warp import reproject

    west, south, east, north = bounds_3857
    transform = from_bounds(west, south, east, north, TIFF_TILE_SIZE, TIFF_TILE_SIZE)
    dst = np.full((TIFF_TILE_SIZE, TIFF_TILE_SIZE), np.nan, dtype=np.float32)
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
    return _elevation_to_agisoft_rgba(dst, src.nodata, vmin, vmax, (abs(transform.a), abs(transform.e)))

def _run_compact_rasterio_tiler(
    input_tif: str,
    output_dir: str,
    project_id: str,
    dataset_name: str,
    dataset_type: str,
) -> None:
    try:
        import mercantile
        import rasterio
        from rasterio.warp import transform_bounds
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            "Compact TIFF tiler needs rasterio and mercantile installed. "
            "Run backend dependency install once."
        ) from exc

    normalized_type = _normalize_dataset_type(dataset_type, dataset_name)
    in_abs = Path(input_tif).resolve()
    out_abs = Path(output_dir).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if local_root not in out_abs.parents:
        raise RuntimeError("Refusing to write tiles outside Project_Data")
    if out_abs.exists():
        shutil.rmtree(out_abs)
    out_abs.mkdir(parents=True, exist_ok=True)

    with rasterio.open(in_abs) as src:
        if not src.crs:
            raise RuntimeError("TIFF has no CRS. Please export with EPSG/CRS before upload.")
        bounds_wgs84_raw = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
        bounds_wgs84 = (
            max(-180.0, bounds_wgs84_raw[0]),
            max(-85.05112878, bounds_wgs84_raw[1]),
            min(180.0, bounds_wgs84_raw[2]),
            min(85.05112878, bounds_wgs84_raw[3]),
        )
        center_lat = (bounds_wgs84[1] + bounds_wgs84[3]) / 2.0
        ground_res = min(abs(float(src.res[0])), abs(float(src.res[1])))
        desired_zoom = _zoom_for_raster_resolution(ground_res, center_lat)
        max_zoom, estimated_tiles = _choose_compact_zoom(
            mercantile,
            bounds_wgs84,
            desired_zoom,
            normalized_type,
        )
        dem_range = _sample_raster_percentiles(src, normalized_type)
        if normalized_type in {"dtm", "dsm"} and dem_range is None:
            raise RuntimeError("No valid elevation cells found in DEM TIFF.")

        meta = {
            "scheme": "xyz",
            "crs": "EPSG:3857",
            "source_crs": str(src.crs),
            "bounds_wgs84": list(bounds_wgs84),
            "zoom_min": 0,
            "zoom_max": max_zoom,
            "tile_size": TIFF_TILE_SIZE,
            "dataset_type": normalized_type,
            "dataset_name": dataset_name,
            "tile_budget_mb": TIFF_TILE_BUDGET_MB,
            "estimated_tile_count": estimated_tiles,
        }
        if dem_range:
            meta["elevation_vmin"], meta["elevation_vmax"] = dem_range

        bytes_written = 0
        tiles_written = 0
        started = time.time()
        for zoom, x, y in _compact_tile_tasks(mercantile, bounds_wgs84, max_zoom):
            tile_bounds = mercantile.xy_bounds(x, y, zoom)
            bounds_3857 = (tile_bounds.left, tile_bounds.bottom, tile_bounds.right, tile_bounds.top)
            if normalized_type in {"dtm", "dsm"}:
                rgba = _read_compact_dem_tile(src, bounds_3857, dem_range[0], dem_range[1], zoom)  # type: ignore[index]
            else:
                rgba = _read_compact_ortho_tile(src, bounds_3857, zoom)
            tile_path = out_abs / str(zoom) / str(x) / f"{y}.png"
            bytes_written += _save_png_tile(rgba, tile_path)
            tiles_written += 1

    budget_bytes = int(TIFF_TILE_BUDGET_MB * 1024 * 1024)
    while bytes_written > budget_bytes and max_zoom > 0:
        zoom_dir = out_abs / str(max_zoom)
        removed_bytes = sum(p.stat().st_size for p in zoom_dir.rglob("*.png")) if zoom_dir.is_dir() else 0
        if zoom_dir.is_dir():
            shutil.rmtree(zoom_dir)
        bytes_written = max(0, bytes_written - int(removed_bytes))
        max_zoom -= 1
        meta["zoom_max"] = max_zoom
        print(
            f"Tile output exceeded {TIFF_TILE_BUDGET_MB:.0f} MB; "
            f"trimmed highest zoom to 0-{max_zoom}."
        )
    (out_abs / "tileset.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")

    mb_written = bytes_written / (1024 * 1024)
    print(
        "Compact TIFF tiles ready: "
        f"project={project_id}, dataset={dataset_name}, type={normalized_type}, "
        f"zoom=0-{max_zoom}, tiles={tiles_written}, size={mb_written:.1f} MB, "
        f"seconds={time.time() - started:.1f}"
    )

def _run_gdal2tiles_subprocess(
    input_tif: str,
    output_dir: str,
    project_id: str,
    dataset_name: str,
    dataset_type: str = "",
) -> None:
    """Run gdal2tiles via QGIS OSGeo4W shell with an 8-bit fallback for DTM/DSM rasters."""
    _run_compact_rasterio_tiler(input_tif, output_dir, project_id, dataset_name, dataset_type)
    return

    in_abs = os.path.abspath(input_tif)
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)

    def run_osgeo(command_body: str) -> subprocess.CompletedProcess[str]:
        command = f'call "{OSGEO4W_BAT}" {command_body}'
        print(f"GDAL command: {command}")
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            executable=os.environ.get("COMSPEC", "cmd.exe"),
        )

    def has_usable_tiles() -> bool:
        png_count = sum(1 for _ in Path(out_abs).rglob("*.png"))
        has_zoom_dirs = any(child.is_dir() and child.name.isdigit() for child in Path(out_abs).iterdir())
        if has_zoom_dirs and png_count > 0:
            print(f"GDAL Success! Tiles generated at {out_abs}")
            print(f"Tile stats: zoom_dirs={has_zoom_dirs}, png_count={png_count}")
            return True
        print(f"GDAL output invalid for {dataset_name}: no usable XYZ tiles found")
        print(f"Output folder checked: {out_abs}")
        print(f"Tile stats: zoom_dirs={has_zoom_dirs}, png_count={png_count}")
        return False

    def make_padding_transparent() -> None:
        try:
            for tile in Path(out_abs).rglob("*.png"):
                img = Image.open(tile).convert("RGBA")
                data = np.array(img)
                rgb = data[:, :, :3]
                is_black = np.all(rgb < 8, axis=2)
                band_min = rgb.min(axis=2)
                band_max = rgb.max(axis=2)
                is_white_pad = (band_min >= 248) & ((band_max - band_min) <= 12)
                transparent = is_black | is_white_pad
                if np.any(transparent):
                    data[transparent, 3] = 0
                    Image.fromarray(data, mode="RGBA").save(tile, optimize=True)
        except Exception as exc:  # noqa: BLE001
            print(f"Tile transparency cleanup skipped: {exc}")

    print(f"Starting GDAL processing for {dataset_name} in project {project_id}...")
    result = run_osgeo(f'gdal2tiles --xyz -z 1-22 -w none "{in_abs}" "{out_abs}"')
    if result.returncode == 0:
        if has_usable_tiles():
            make_padding_transparent()
            return
        raise RuntimeError("gdal2tiles completed but produced no usable XYZ tiles.")

    msg = (result.stderr or result.stdout or "").strip()
    if "convert this file to 8-bit" in msg.lower():
        print("GDAL requested 8-bit input. Creating scaled visual VRT for tile generation only.")
        vrt_path = Path(out_abs).parent / f"{Path(out_abs).name}_visual_byte.vrt"
        translate = run_osgeo(f'gdal_translate -of VRT -ot Byte -scale "{in_abs}" "{vrt_path}"')
        if translate.returncode != 0:
            translate_msg = (translate.stderr or translate.stdout or "").strip()
            raise RuntimeError(translate_msg or f"gdal_translate failed for {dataset_name} ({project_id})")

        out_path = Path(out_abs).resolve()
        local_root = Path(LOCAL_DATA_PATH).resolve()
        if out_path.is_relative_to(local_root) and out_path.is_dir():
            shutil.rmtree(out_path)
            out_path.mkdir(parents=True, exist_ok=True)

        retry = run_osgeo(f'gdal2tiles --xyz -z 1-22 -w none "{vrt_path}" "{out_abs}"')
        try:
            vrt_path.unlink(missing_ok=True)
        except OSError:
            pass
        if retry.returncode == 0 and has_usable_tiles():
            make_padding_transparent()
            return
        retry_msg = (retry.stderr or retry.stdout or "").strip()
        raise RuntimeError(retry_msg or f"gdal2tiles failed after 8-bit scaling for {dataset_name} ({project_id})")

    print(f"GDAL FAILED with Error Code: {result.returncode}")
    print(f"ERROR LOG:\n{result.stderr}")
    print(f"GDAL OUTPUT LOG:\n{result.stdout}")
    raise RuntimeError(msg or f"gdal2tiles failed for {dataset_name} ({project_id})")

async def process_tif_to_tiles(
    input_tif: str,
    output_dir: str,
    project_id: str,
    dataset_name: str,
    dataset_type: str = "",
    progress_callback=None,
) -> None:
    await asyncio.to_thread(
        run_rasterio_tiler,
        input_tif,
        output_dir,
        project_id,
        dataset_name,
        dataset_type,
        LOCAL_DATA_PATH,
        TIFF_TILE_BUDGET_MB,
        TIFF_TILE_MIN_ZOOM_LIMIT,
        TIFF_TILE_MAX_ZOOM_LIMIT,
        TIFF_TILE_SIZE,
        progress_callback,
    )

async def process_dataset_background(
    project_id: str,
    dataset_id: str,
    input_tif: str,
    file_name: str | None,
    tile_output_dir: str,
    tile_folder: str,
    source_epsg: str = "",
) -> None:
    output_dir = Path(tile_output_dir).resolve()
    cog_path = output_dir / f"{tile_folder}.cog.tif"
    existing_status = _read_dataset_status(project_id, dataset_id) or {}
    if source_epsg and not existing_status.get("manual_epsg"):
        existing_status["manual_epsg"] = source_epsg
    common_status = {
        "dataset_id": dataset_id,
        "dataset_name": file_name or Path(input_tif).name,
        "tile_folder": tile_folder,
        "dataset_type": existing_status.get("dataset_type", _infer_dataset_type(file_name or Path(input_tif).name)),
        "month": existing_status.get("month", ""),
        "raw_rel_path": existing_status.get("raw_rel_path", ""),
    }
    _write_dataset_status(
        project_id,
        dataset_id,
        {
            **common_status,
            "status": "Converting COG",
            "stage": "Queued for COG conversion",
            "progress_percent": "5",
            "eta_seconds": "",
            "started_at": _now_iso(),
            "updated_at": _now_iso(),
        },
    )
    err_path = _dataset_dir(project_id, dataset_id) / ".conversion_error.txt"
    err_path.unlink(missing_ok=True)
    try:
        def update_progress(payload: dict[str, object]) -> None:
            progress_percent = str(payload.get("progress_percent", ""))
            stage = str(payload.get("stage") or "Processing raster")
            eta_seconds = str(payload.get("eta_seconds", ""))
            status_payload = {
                **common_status,
                "status": "Converting COG",
                "stage": stage,
                "progress_percent": progress_percent,
                "eta_seconds": eta_seconds,
                "updated_at": _now_iso(),
            }
            _write_dataset_status(project_id, dataset_id, status_payload)
            _upsert_processing_job(
                project_id,
                {
                    "job_id": dataset_id,
                    "kind": "dataset",
                    "file_name": file_name or Path(input_tif).name,
                    "status": "Processing",
                    "stage": stage,
                    "progress_percent": progress_percent,
                    "eta_seconds": eta_seconds,
                    "updated_at": _now_iso(),
                },
            )

        result = await asyncio.to_thread(
            convert_tif_to_cog,
            input_tif,
            str(cog_path),
            file_name or Path(input_tif).name,
            str(common_status.get("dataset_type", "")),
            LOCAL_DATA_PATH,
            update_progress,
            source_epsg or str(common_status.get("manual_epsg") or ""),
        )
        cog_abs = Path(str(result.get("cog_path") or cog_path)).resolve()
        cog_rel = cog_abs.relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        processed_size_bytes = cog_abs.stat().st_size if cog_abs.is_file() else calculate_folder_size(output_dir)
        processed_size = _format_size_bytes(processed_size_bytes)
        layer_type = _raster_layer_type(str(common_status.get("dataset_type", "")), file_name or Path(input_tif).name)
        rescale = result.get("rescale")
        rescale_min = ""
        rescale_max = ""
        if isinstance(rescale, dict):
            rescale_min = str(rescale.get("min") or "")
            rescale_max = str(rescale.get("max") or "")
        bounds_wgs84 = result.get("bounds_wgs84")
        bounds_text = json.dumps(bounds_wgs84) if isinstance(bounds_wgs84, list) else ""
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": file_name or Path(input_tif).name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{cog_rel}",
                "cog_path": str(cog_abs),
                "cog_rel_path": cog_rel,
                "rescale_min": rescale_min,
                "rescale_max": rescale_max,
                "bounds_wgs84": bounds_text,
            },
        )
        _invalidate_project_files_cache(project_id)
        _write_dataset_status(
            project_id,
            dataset_id,
            {
                **common_status,
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "layer_type": layer_type,
                "cog_path": str(cog_abs),
                "cog_rel_path": cog_rel,
                "tiles_rel_path": "",
                "bounds_wgs84": bounds_text,
                "rescale_min": rescale_min,
                "rescale_max": rescale_max,
            "source_crs": str(result.get("source_crs") or ""),
            "manual_epsg": str(result.get("manual_epsg") or source_epsg or common_status.get("manual_epsg") or ""),
            "applied_epsg": str(result.get("applied_epsg") or ""),
                "cog_engine": str(result.get("engine") or "rio-cogeo"),
                "processed_size_bytes": str(processed_size_bytes),
                "processed_size": processed_size,
                "stage": "Web-ready",
                "progress_percent": "100",
                "eta_seconds": "0",
            },
        )
    except Exception as exc:  # noqa: BLE001
        msg = str(exc) or "Tile generation failed."
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        _write_dataset_status(
            project_id,
            dataset_id,
            {
                **common_status,
                "status": "Failed",
                "error": msg[:8000],
                "updated_at": _now_iso(),
            },
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": file_name or Path(input_tif).name,
                "status": "Failed",
                "error": msg[:8000],
                "updated_at": _now_iso(),
            },
        )
        _invalidate_project_files_cache(project_id)

def _detect_input_srs(input_file: Path) -> str | None:
    """
    Best-effort LAS/LAZ CRS detection from file metadata.
    Returns an EPSG string like 'EPSG:32644' when available.
    """
    try:
        with laspy.open(str(input_file)) as reader:
            crs = reader.header.parse_crs()
        if crs is None:
            return None
        authority = crs.to_authority()
        if authority and authority[0] and authority[1]:
            return f"{authority[0]}:{authority[1]}"
    except (OSError, ValueError, laspy.errors.LaspyException):
        return None
    return None

def _agisoft_dtm_lut() -> np.ndarray:
    global _AGISOFT_DTM_LUT
    if _AGISOFT_DTM_LUT is not None:
        return _AGISOFT_DTM_LUT
    positions = _AGISOFT_DTM_STOPS[:, 0]
    colors = _AGISOFT_DTM_STOPS[:, 1:4]
    lut = np.zeros((256, 3), dtype=np.uint8)
    for idx in range(256):
        t = idx / 255.0
        stop_idx = int(np.searchsorted(positions, t, side="right") - 1)
        stop_idx = max(0, min(stop_idx, len(positions) - 2))
        t0, t1 = positions[stop_idx], positions[stop_idx + 1]
        frac = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
        rgb = colors[stop_idx] + frac * (colors[stop_idx + 1] - colors[stop_idx])
        lut[idx] = np.clip(rgb, 0, 255).astype(np.uint8)
    _AGISOFT_DTM_LUT = lut
    return lut

def _compute_tile_hillshade(
    elev: np.ndarray,
    valid: np.ndarray,
    res_x: float,
    res_y: float,
    azimuth: float = 315.0,
    altitude: float = 45.0,
) -> np.ndarray:
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
    az = np.radians(azimuth)
    alt = np.radians(altitude)
    shade = np.sin(alt) * np.sin(slope) + np.cos(alt) * np.cos(slope) * np.cos(az - aspect)
    return np.clip(((shade + 1.0) * 0.5).astype(np.float32), 0.0, 1.0)

def _elevation_to_agisoft_rgba(
    data: np.ndarray,
    nodata: float | None,
    vmin: float,
    vmax: float,
    pixel_size: tuple[float, float],
) -> np.ndarray:
    h, w = data.shape
    out = np.zeros((h, w, 4), dtype=np.uint8)
    valid = np.isfinite(data)
    if nodata is not None:
        valid &= data != nodata
    if not np.any(valid):
        return out

    span = max(vmax - vmin, 1e-6)
    norm = np.clip((data - vmin) / span, 0.0, 1.0)
    lut = _agisoft_dtm_lut()
    rgb = np.zeros((h, w, 3), dtype=np.float32)
    rgb[valid] = lut[(norm[valid] * 255.0).astype(np.uint8)].astype(np.float32)

    shade = _compute_tile_hillshade(data, valid, pixel_size[0], pixel_size[1])
    rgb[valid] *= (0.32 + 0.68 * shade[valid, np.newaxis])
    out[valid, :3] = np.clip(rgb[valid], 0, 255).astype(np.uint8)
    out[valid, 3] = 255
    return out

def _detect_epsg_from_file(file_path: Path) -> str | None:
    suffix = file_path.suffix.lower()
    try:
        if suffix in (".las", ".laz"):
            with laspy.open(str(file_path)) as reader:
                crs = reader.header.parse_crs()
            if crs:
                authority = crs.to_authority()
                if authority and authority[0] and authority[1]:
                    return f"{authority[0]}:{authority[1]}"
        if suffix in (".tif", ".tiff"):
            try:
                import rasterio  # type: ignore
            except Exception:
                return None
            with rasterio.open(str(file_path)) as src:
                crs = src.crs
            if crs:
                authority = crs.to_authority()
                if authority and authority[0] and authority[1]:
                    return f"{authority[0]}:{authority[1]}"
    except Exception:
        return None
    return None

def _tile_y_to_lat(y: int, z: int) -> float:
    n = 2.0 ** z
    rad = math.atan(math.sinh(math.pi * (1 - (2 * y) / n)))
    return math.degrees(rad)

def _xyz_bounds_from_tiles_dir(tiles_dir: Path) -> list[float] | None:
    zoom_dirs = sorted(
        [d for d in tiles_dir.iterdir() if d.is_dir() and d.name.isdigit()],
        key=lambda p: int(p.name),
        reverse=True,
    )
    if not zoom_dirs:
        return None

    for zdir in zoom_dirs:
        z = int(zdir.name)
        x_dirs = [d for d in zdir.iterdir() if d.is_dir() and d.name.isdigit()]
        if not x_dirs:
            continue
        x_values = sorted(int(d.name) for d in x_dirs)
        min_x = x_values[0]
        max_x = x_values[-1]

        min_y: int | None = None
        max_y: int | None = None
        for xdir in x_dirs:
            for png in xdir.glob("*.png"):
                stem = png.stem
                if stem.isdigit():
                    y = int(stem)
                    min_y = y if min_y is None else min(min_y, y)
                    max_y = y if max_y is None else max(max_y, y)
        if min_y is None or max_y is None:
            continue

        n = 2 ** z
        min_lon = (min_x / n) * 360.0 - 180.0
        max_lon = ((max_x + 1) / n) * 360.0 - 180.0
        max_lat = _tile_y_to_lat(min_y, z)
        min_lat = _tile_y_to_lat(max_y + 1, z)
        return [min_lon, min_lat, max_lon, max_lat]
    return None
