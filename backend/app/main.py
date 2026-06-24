import asyncio
import base64
import csv
import hashlib
import hmac
import math
import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import time
import zipfile
import io
import smtplib
from pathlib import Path
import sqlite3
import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from importlib.util import find_spec
from typing import Callable
from urllib.parse import quote, unquote
from urllib.parse import urlencode
from urllib.request import Request as UrlRequest, urlopen
from urllib.error import URLError
from email.message import EmailMessage

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, RedirectResponse, StreamingResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
import laspy
import numpy as np
from PIL import Image
from rio_tiler import colormap as rio_colormap
from rio_tiler.io import Reader

from app.core.database import configure_database, ensure_tables, get_db_connection
from app.services import catalog_service
from app.utils.spatial_import import (
    normalize_structure_type,
    parse_spatial_upload,
    style_for_structure,
)
from app.services.raster import convert_tif_to_cog, run_rasterio_tiler


# --- PHASE 5 EXTRACTED SERVICES ---
from app.core.security import (_hash_password, _verify_password, _sign_session_token, _unsign_session_token, _token_hash)
from app.core.middleware import (Debug404Middleware, ActivityTrackingMiddleware, ProtectedDataPathMiddleware)
from app.dependencies import (_require_user, _get_optional_user, _require_admin, verify_admin, _require_upload_user, _client_ip_for_limit, _enforce_rate_limit)
from app.services.auth_service import (_create_pending_user, _approval_url, _send_owner_sms, _send_email)
from app.services.project_service import (get_project_dirs, get_project_dataset_type_dirs, _delete_project_storage, _ensure_project_owner, _is_admin_user_id)
from app.services.dataset_service import (_infer_dataset_type, _normalize_dataset_type, _raster_layer_type, _read_dataset_status, _write_dataset_status, _read_dataset_manifest, _write_dataset_manifest, _status_manifest_payload, _write_status_manifests, _sync_dataset_metadata_to_processing_job, _delete_dataset_artifacts, admin_delete_dataset_by_name, _deep_rename_dataset_artifacts)
from app.services.upload_service import (_upload_session_dir, _dataset_upload_session_dir, _ensure_disk_space_for_bytes, _merge_upload_chunks)
from app.services.analysis_service import (_sample_raster, _interpolate_profile_points, _profile_summary, _volume_for_raster, _dtm_volume_between, _circle_points, _pixel_area_m2)
from app.services.grid_export_service import (_csv_grid_generator, _dxf_grid_generator, _generate_grid_export_background, _csv_grid_rows, _dxf_grid_rows, _validate_grid_export_request, _grid_export_is_current)
from app.services.spatial_feature_service import (_ensure_spatial_layer, _insert_spatial_feature, _spatial_row_to_dict, _normalize_spatial_feature_geojson, _can_manage_spatial_feature)
from app.services.bulk_import_service import (_browse_server_folder, _bulk_scan_files, _admin_manual_bulk_import_background, _prepare_admin_manual_bulk_import, _queue_admin_manual_bulk_import)
# --- POINT CLOUD SERVICES ---
from app.services.pointcloud.ept_service import _prepare_las_for_ept, _run_ept_converter_once, process_pointcloud_ept_job, _ept_error_needs_las_bbox_repair, _repair_las_bounding_box, _looks_like_lon_lat_bounds, _utm_epsg_for_lon_lat
from app.services.pointcloud.copc_service import _run_copc_converter_once, _process_copc_ept_compat_job, _copc_ept_compat_dir, _best_copc_asset, _copc_asset_in_dir
from app.services.pointcloud.pointcloud_jobs import process_pointcloud, process_pointcloud_background, process_contours_background
from app.services.pointcloud.pointcloud_slice import _run_pointcloud_slice_export, _rotation_matrix_xyz, _finite_vector, _point_record_value
from app.services.pointcloud.pdal_tools import _resolve_converter_executable, _pdal_has_driver

from app.utils.analysis_utils import sample_cross_section

# ---------------------------------------------------------------------------
# Configuration — all env vars, constants, and path helpers are in config.py
# ---------------------------------------------------------------------------
from app.core.config import (  # noqa: E402
    BASE_DIR,
    ADMIN_ALERT_PHONE,
    CATCHMENT_STATS,
    DATABASE_DIR,
    DIRECT_RASTER_UPLOAD_LIMIT_BYTES,
    DISK_HEADROOM_BYTES,
    ERROR_LOG_DIR,
    FRONTEND_ORIGINS,
    IMAGE_EXTENSIONS,
    LOCAL_DATA_PATH,
    LULC_ROWS,
    MERGE_COPY_BUFFER_BYTES,
    OSGEO4W_BAT,
    OWNER_APPROVAL_EMAIL,
    PDAL_EXE,
    POINTCLOUD_EPT_PROJECT_GEOGRAPHIC,
    POINTCLOUD_EPT_TARGET_EPSG,
    POINTCLOUD_SRS_IN,
    POINTCLOUD_SRS_OUT,
    PORTAL_VERSION,
    POTREE_NATIVE_COPC_ENABLED,
    PROJECT_FILES_CACHE_TTL_SECONDS,
    PUBLIC_PORTAL_URL,
    RATE_LIMIT_HEAVY_REQUESTS,
    RATE_LIMIT_WINDOW_SECONDS,
    SESSION_AUTH_CACHE_SECONDS,
    SESSION_COOKIE_NAME,
    SESSION_REFRESH_THRESHOLD_SECONDS,
    SESSION_RENEW_GRACE_SECONDS,
    SESSION_SECRET_FILE,
    SESSION_SIGNING_SECRET,
    SESSION_SIGNING_SECRET_RAW,
    SESSION_TTL_SECONDS,
    STREAM_STATS,
    TIFF_TILE_BUDGET_MB,
    TIFF_TILE_MAX_ZOOM_LIMIT,
    TIFF_TILE_MIN_ZOOM_LIMIT,
    TIFF_TILE_SIZE,
    UNTWINE_EXE,
    VIDEO_EXTENSIONS,
    _PROJECT_FILES_CACHE,
    _RATE_LIMIT_BUCKETS,
    _SESSION_USER_CACHE,
    _local_data_path_from_user_value,
    _rebase_project_data_path,
    _strip_file_scheme,
)

Path(LOCAL_DATA_PATH).mkdir(parents=True, exist_ok=True)
configure_database(DATABASE_DIR)


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


DJI_TERRA_DEM_CMAP = _build_dji_terra_colormap()
rio_colormap.cmap = rio_colormap.cmap.register(
    {
        "agisoft_dem": DJI_TERRA_DEM_CMAP,
        "dji_terra_dem": DJI_TERRA_DEM_CMAP,
    },
    overwrite=True,
)

from titiler.core.factory import TilerFactory

app = FastAPI(
    title="Droid Survair Cloud Portal API",
    description="Backend services for drone survey data and mapping.",
    version="0.1.0",
)
cog_tiler = TilerFactory()

# --- PHASE 6 ROUTERS ---
from app.routers import raster_tiles
from app.routers import pointclouds
from app.routers import projects
from app.routers import auth
from app.routers import admin_users
from app.routers import admin_projects
from app.routers import admin_import
from app.routers import media
from app.routers import issues
from app.routers import spatial
from app.routers import uploads
from app.routers import datasets
from app.routers import admin_catalog
from app.routers import analysis
from app.routers import jobs
from app.routers import proxy
from app.routers import files
from app.routers import system
app.include_router(raster_tiles.router)
app.include_router(pointclouds.router)
app.include_router(projects.router)
app.include_router(auth.router)
app.include_router(admin_users.router)
app.include_router(admin_projects.router)
app.include_router(admin_import.router)
app.include_router(media.router)
app.include_router(issues.router)
app.include_router(spatial.router)
app.include_router(uploads.router)
app.include_router(datasets.router)
app.include_router(admin_catalog.router)
app.include_router(analysis.router)
app.include_router(jobs.router)
app.include_router(proxy.router)
app.include_router(files.router)
app.include_router(system.router)
if find_spec("multipart") is None:
    print(
        "WARNING: python-multipart is not installed. "
        "Upload endpoints may fail with 422/validation errors.",
    )








app.add_middleware(ProtectedDataPathMiddleware)
app.add_middleware(ActivityTrackingMiddleware)
app.add_middleware(Debug404Middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Range", "Content-Length", "Content-Type"],
)
app.include_router(
    cog_tiler.router,
    prefix="/api/titiler",
    tags=["TiTiler"],
)




from app.models.admin import (  # noqa: E402
    AdminBulkDeleteItem,
    AdminBulkDeletePayload,
    AdminDatasetMetaPayload,
    AdminDatasetPathMetaPayload,
    AdminDatasetRenamePayload,
    AdminLocateFolderPayload,
    AdminManualBulkImportPayload,
    AdminManualBulkImportTask,
    AdminProjectPatchPayload,
    AdminUserApprovalPayload,
    AdminUserHiddenTabsPayload,
    AdminUserLocationRequiredPayload,
    AdminUserPasswordResetPayload,
    AdminUserRolePayload,
    AdminUserUploadAccessPayload,
)
from app.models.analysis import (
    CompareVolumePayload,
    CrossSectionPayload,
    ProfilePayload,
    VolumePayload,
)
from app.models.auth import AuthPayload
from app.models.datasets import (
    CompleteDatasetUploadPayload,
    CompleteUploadPayload,
    ContourGeneratePayload,
    CropMaskPayload,
    DatasetMetaPayload,
    DatasetOwnerPathMetaPayload,
    FileDeletePayload,
    ProcessDatasetOut,
)
from app.models.issues import Issue, IssuePayload
from app.models.misc import ClientErrorLogPayload
from app.models.pointclouds import (
    PointCloudProcessPayload,
    PointCloudSliceBoxPayload,
    PointCloudSliceExportPayload,
)
from app.models.projects import (
    CameraViewPayload,
    ProjectCreatePayload,
    ProjectOut,
    ProjectUpdatePayload,
)
from app.models.spatial import SpatialFeaturePatchPayload, SpatialFeaturePayload


def _safe_pointcloud_basename(filename: str) -> str:
    """Reject path traversal; only allow simple .las / .laz names."""
    base = os.path.basename(filename.strip())
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if "/" in base or "\\" in base or ".." in base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(base).suffix.lower()
    if suffix not in (".las", ".laz"):
        raise HTTPException(
            status_code=400, detail="Only .las or .laz files are supported",
        )
    return base


def _safe_tif_basename(filename: str) -> str:
    base = os.path.basename(filename.strip())
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if "/" in base or "\\" in base or ".." in base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(base).suffix.lower()
    if suffix not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff files are supported")
    return base


def _safe_dataset_upload_basename(filename: str) -> str:
    base = os.path.basename(filename.strip())
    if not base or base in (".", ".."):
        raise HTTPException(status_code=400, detail="Invalid filename")
    if "/" in base or "\\" in base or ".." in base:
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(base).suffix.lower()
    if suffix not in (".tif", ".tiff", ".las", ".laz", ".csv", ".zip", ".kml", ".geojson", ".dwg", ".pdf"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.las/.laz/.csv/.zip/.kml/.geojson/.dwg/.pdf files are supported")
    return base


def _normalize_epsg_input(value: str | None) -> str:
    clean = (value or "").strip().upper().replace(" ", "")
    if not clean:
        return ""
    if clean.startswith("EPSG:"):
        clean = clean[5:]
    if not re.fullmatch(r"\d{4,6}", clean):
        raise HTTPException(status_code=400, detail="Invalid EPSG code. Use EPSG:32644 or 32644.")
    return f"EPSG:{clean}"








def _normalize_month(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d{4}-\d{2}", raw):
        return raw
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return raw[:7]
    return raw[:40]


def get_dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    stack = [path]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total += entry.stat(follow_symlinks=False).st_size
                        elif entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                    except OSError:
                        continue
        except OSError:
            continue
    return total


def calculate_folder_size(path: Path) -> int:
    return get_dir_size(path)


def _format_size_bytes(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    gb = size_bytes / (1024 * 1024 * 1024)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 * 1024)
    return f"{mb:.0f} MB"


def _titiler_tile_url_template(
    base_url: str,
    cog_path: str,
    layer_type: str = "",
    rescale_min: str = "",
    rescale_max: str = "",
) -> str:
    params = {"url": cog_path.replace("\\", "/")}
    normalized_layer_type = layer_type.strip().lower().replace(" ", "")
    if normalized_layer_type in {"ortho", "orthomosaic"}:
        return (
            f"{base_url.rstrip('/')}/api/ortho-cog/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    if layer_type in {"DTM", "DSM"} and rescale_min and rescale_max:
        params["rescale"] = f"{rescale_min},{rescale_max}"
        return (
            f"{base_url.rstrip('/')}/api/dji-terra/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    return (
        f"{base_url.rstrip('/')}/api/titiler/tiles/WebMercatorQuad/"
        f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
    )


def _transparent_png_tile() -> bytes:
    output = io.BytesIO()
    Image.fromarray(np.zeros((1, 1, 4), dtype=np.uint8), mode="RGBA").save(output, format="PNG")
    return output.getvalue()


TRANSPARENT_PNG_TILE = _transparent_png_tile()
ORTHO_RENDERER_VERSION = "edge-padding-v7"


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


def _secure_local_cog_path(raw_url: str) -> Path:
    clean_source = _rebase_project_data_path(raw_url)
    if not clean_source:
        raise HTTPException(status_code=400, detail="Missing COG path.")
    target = Path(os.path.abspath(clean_source)).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if target != local_root and not target.is_relative_to(local_root):
        raise HTTPException(status_code=403, detail="COG path is outside project storage.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="COG file not found.")
    return target


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


































def _primary_copc_dir_for_ept_folder(ept_dir: Path) -> Path | None:
    if not ept_dir.name.endswith("__ept_viewer"):
        return None
    copc_dir = ept_dir.parent / ept_dir.name[: -len("__ept_viewer")]
    return copc_dir if _copc_asset_in_dir(copc_dir) is not None else None


def _should_skip_ept_listing_for_native_copc(ept_dir: Path) -> bool:
    if not POTREE_NATIVE_COPC_ENABLED:
        return False
    if ept_dir.name.endswith("__ept_viewer"):
        return True
    if _copc_asset_in_dir(ept_dir) is not None:
        return True
    primary = _primary_copc_dir_for_ept_folder(ept_dir)
    return primary is not None












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


def _conversion_cache_file() -> Path:
    return Path(LOCAL_DATA_PATH) / "pointclouds" / "_upload_cache.json"


def _read_conversion_cache() -> dict[str, str]:
    cache_path = _conversion_cache_file()
    if not cache_path.is_file():
        return {}
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _write_conversion_cache(data: dict[str, str]) -> None:
    cache_path = _conversion_cache_file()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass


_AGISOFT_DTM_STOPS = np.array(
    [
        [0.00, 0, 0, 130],
        [0.25, 0, 255, 255],
        [0.50, 0, 255, 0],
        [0.75, 255, 255, 0],
        [1.00, 139, 0, 0],
    ],
    dtype=np.float32,
)
_AGISOFT_DTM_LUT: np.ndarray | None = None


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


def _safe_project_id(project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", project_id or ""):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    return project_id




def _dataset_type_folder(dataset_type: str) -> str:
    normalized = _normalize_dataset_type(dataset_type, "")
    if normalized in {"ortho", "dtm", "dsm", "pointcloud", "csv", "3dmodel", "vector", "cad"}:
        return normalized
    return "other"




def _safe_tileset_id(tileset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", tileset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid tileset_id")
    return tileset_id


def _ept_dataset_name(name: str) -> str:
    stem = Path(name).stem if Path(name).suffix else name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    return _safe_tileset_id(cleaned[:120] or "pointcloud")


def _safe_ept_folder_name(name: str) -> str:
    cleaned = (name or "").strip()
    if (
        not cleaned
        or cleaned in {".", ".."}
        or "/" in cleaned
        or "\\" in cleaned
        or not re.fullmatch(r"[A-Za-z0-9._ -]{1,240}", cleaned)
    ):
        raise HTTPException(status_code=400, detail="Invalid EPT folder")
    return cleaned


def _project_processed_root(project_id: str) -> Path:
    return Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id) / "processed"


def _project_exports_root(project_id: str) -> Path:
    return Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id) / "exports"


def _project_pointcloud_root(project_id: str) -> Path:
    return _project_exports_root(project_id) / "pointclouds"


def _legacy_project_pointcloud_root(project_id: str) -> Path:
    return _project_processed_root(project_id) / "pointclouds"


def _ept_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _project_pointcloud_root(project_id) / _safe_ept_folder_name(dataset_name)


def _legacy_ept_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _project_processed_root(project_id) / _safe_ept_folder_name(dataset_name)


def _legacy_ept_pointcloud_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _legacy_project_pointcloud_root(project_id) / _safe_ept_folder_name(dataset_name)


def _ept_asset_quality(dataset_dir: Path) -> int:
    ept_json = dataset_dir / "ept.json"
    if not ept_json.is_file() or (dataset_dir / ".conversion_error.txt").is_file():
        return -1
    hierarchy_dir = dataset_dir / "ept-hierarchy"
    data_dir = dataset_dir / "ept-data"
    if not hierarchy_dir.is_dir() or not data_dir.is_dir():
        return -1
    try:
        hierarchy_count = sum(1 for _ in hierarchy_dir.glob("*.json"))
        data_count = sum(1 for p in data_dir.rglob("*") if p.is_file())
        ept_size = ept_json.stat().st_size
    except OSError:
        return -1
    if hierarchy_count < 1 or data_count < 1 or ept_size <= 0:
        return -1
    score = 100
    score += min(hierarchy_count, 5000) * 20
    score += min(data_count, 50000)
    score += min(ept_size // 1024, 50)
    return score


def _ept_asset_candidates(project_id: str, dataset_name: str) -> list[tuple[str, Path]]:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    return [
        (f"exports/pointclouds/{safe_dataset}/ept.json", _ept_dataset_dir(safe_project, safe_dataset)),
        (
            f"processed/pointclouds/{safe_dataset}/ept.json",
            _legacy_ept_pointcloud_dataset_dir(safe_project, safe_dataset),
        ),
        (f"processed/{safe_dataset}/ept.json", _legacy_ept_dataset_dir(safe_project, safe_dataset)),
    ]


def _best_ept_asset(project_id: str, dataset_name: str) -> tuple[str, Path] | None:
    best: tuple[int, int, str, Path] | None = None
    for index, (rel_path, dataset_dir) in enumerate(_ept_asset_candidates(project_id, dataset_name)):
        quality = _ept_asset_quality(dataset_dir)
        if quality < 0:
            continue
        # Prefer newer export output only when quality is equal; otherwise choose
        # the richer hierarchy because the Potree/EPT loader streams it more reliably.
        rank = (quality, -index, rel_path, dataset_dir)
        if best is None or rank > best:
            best = rank
    if best is None:
        return None
    return best[2], best[3]






def _ept_json_url(base_url: str, project_id: str, dataset_name: str) -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    best = _best_ept_asset(safe_project, safe_dataset)
    rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/ept.json"
    return (
        f"{base_url.rstrip('/')}/api/data/projects/{safe_project}/{quote(rel_path, safe='/')}"
    )


def _copc_url(base_url: str, project_id: str, dataset_name: str, asset_rel_path: str = "") -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    rel_path = asset_rel_path.replace("\\", "/").lstrip("/")
    if not rel_path:
        best = _best_copc_asset(safe_project, safe_dataset)
        rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/output.copc.laz"
    return f"{base_url.rstrip('/')}/api/data/projects/{safe_project}/{quote(rel_path, safe='/')}"


def _copc_viewer_url(
    base_url: str,
    project_id: str,
    dataset_name: str,
    display_name: str = "",
    asset_rel_path: str = "",
) -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    copc_path = _copc_url("", safe_project, safe_dataset, asset_rel_path)
    query = urlencode(
        {
            "copc": copc_path,
            "project": safe_project,
            "dataset": safe_dataset,
            "name": display_name or safe_dataset,
        }
    )
    return f"/droid-ept-viewer/index.html?{query}"


def _ept_viewer_url(base_url: str, project_id: str, dataset_name: str, display_name: str = "") -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    best = _best_ept_asset(safe_project, safe_dataset)
    rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/ept.json"
    ept_path = f"/api/data/projects/{safe_project}/{rel_path}"
    query = urlencode(
        {
            "ept": ept_path,
            "project": safe_project,
            "dataset": safe_dataset,
            "name": display_name or safe_dataset,
        }
    )
    return f"/droid-ept-viewer/index.html?{query}"


def _pointcloud_viewer_url(
    base_url: str,
    project_id: str,
    dataset_name: str,
    display_name: str = "",
    viewer_type: str = "copc",
) -> str:
    return _copc_viewer_url(base_url, project_id, dataset_name, display_name)


def _safe_dataset_id(dataset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", dataset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid dataset_id")
    return dataset_id


def _dataset_dir(project_id: str, dataset_id: str) -> Path:
    """Job metadata (.status.json) for a raster upload; tiles live under projects/.../processed/."""
    return Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs" / dataset_id


def _dataset_status_file(project_id: str, dataset_id: str) -> Path:
    return _dataset_dir(project_id, dataset_id) / ".status.json"


def _dataset_manifest_name() -> str:
    return ".droid_dataset.json"


def _manifest_target_for(path: Path) -> Path:
    return path / _dataset_manifest_name() if path.is_dir() else path.parent / _dataset_manifest_name()










def _processing_jobs_file() -> Path:
    return Path(LOCAL_DATA_PATH) / "processing_jobs.json"


def _read_processing_jobs() -> dict[str, list[dict[str, str]]]:
    jobs_path = _processing_jobs_file()
    if not jobs_path.is_file():
        return {}
    try:
        raw = jobs_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            normalized: dict[str, list[dict[str, str]]] = {}
            for project_id, jobs in data.items():
                if isinstance(jobs, list):
                    normalized[str(project_id)] = [
                        {str(k): str(v) for k, v in job.items()}
                        for job in jobs
                        if isinstance(job, dict)
                    ]
            return normalized
    except (OSError, json.JSONDecodeError):
        return {}
    return {}


def _invalidate_project_files_cache(project_id: str) -> None:
    _PROJECT_FILES_CACHE.pop(project_id, None)


def _get_cached_project_files(project_id: str) -> list[dict[str, str]] | None:
    entry = _PROJECT_FILES_CACHE.get(project_id)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > PROJECT_FILES_CACHE_TTL_SECONDS:
        _PROJECT_FILES_CACHE.pop(project_id, None)
        return None
    return data


def _set_cached_project_files(project_id: str, files: list[dict[str, str]]) -> None:
    _PROJECT_FILES_CACHE[project_id] = (time.time(), files)


def _fast_tile_dir_size(tile_root: Path) -> str:
    """Avoid walking thousands of XYZ PNG tiles just to populate a UI size label."""
    for marker_name in ("tilemapresource.xml", "doc.kml"):
        marker = tile_root / marker_name
        if marker.is_file():
            return str(max(marker.stat().st_size, 1))
    return "1"


def _looks_like_cesium_tileset_json(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    asset = data.get("asset")
    root = data.get("root")
    if not isinstance(asset, dict) or not isinstance(root, dict):
        return False
    has_tiles = any(key in root for key in ("children", "content", "contents"))
    return has_tiles and ("geometricError" in data or "geometricError" in root)


def _find_tileset_json(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    direct = folder / "tileset.json"
    if _looks_like_cesium_tileset_json(direct):
        return direct

    candidates: dict[str, Path] = {}
    for pattern in ("*.json", "*/*.json", "*/*/*.json"):
        for candidate in folder.glob(pattern):
            candidates[str(candidate.resolve())] = candidate

    def sort_key(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        priority = 0 if name == "tileset.json" else 1 if any(token in name for token in ("production", "scene", "root")) else 2
        return (priority, len(path.relative_to(folder).parts), name)

    for candidate in sorted(candidates.values(), key=sort_key):
        if _looks_like_cesium_tileset_json(candidate):
            return candidate
    return None


def _ensure_tileset_alias(tileset_path: Path) -> Path:
    alias = tileset_path.parent / "tileset.json"
    if tileset_path.name.lower() == "tileset.json":
        return tileset_path
    if not alias.exists():
        shutil.copyfile(tileset_path, alias)
    return alias


def _contains_pointcloud_viewer_asset(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for pattern in (
        "ept.json",
        "*/ept.json",
        "*/*/ept.json",
        "*.copc.laz",
        "*/*.copc.laz",
        "*/*/*.copc.laz",
    ):
        if any(folder.glob(pattern)):
            return True
    return False


def _project_copc_assets(project_id: str) -> list[Path]:
    safe_project_id = _safe_project_id(project_id)
    processed_root = _project_processed_root(safe_project_id)
    roots = (
        _project_pointcloud_root(safe_project_id),
        _legacy_project_pointcloud_root(safe_project_id),
        processed_root,
    )
    assets: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for asset in root.rglob("*.copc.laz"):
            if asset.is_file() and asset.stat().st_size > 0:
                assets[str(asset.resolve()).lower()] = asset.resolve()
    return sorted(assets.values(), key=lambda path: (path.stat().st_mtime, path.name.lower()), reverse=True)


def _is_3d_model_dataset(folder: Path) -> bool:
    if _contains_pointcloud_viewer_asset(folder):
        return False
    return _find_tileset_json(folder) is not None


def _candidate_processed_tile_dirs(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted([p for p in processed_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if _is_valid_tile_dataset(child):
            candidates.append(child)
            continue
        for nested in sorted([p for p in child.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            if _is_valid_tile_dataset(nested):
                candidates.append(nested)
    return candidates


def _candidate_processed_cog_files(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    files: dict[str, Path] = {}
    for pattern in ("*.cog.tif", "*.cog.tiff", "*_cog.tif", "*_cog.tiff"):
        for path in processed_root.rglob(pattern):
            if path.is_file():
                files[path.resolve().as_posix()] = path
    return sorted(files.values(), key=lambda p: p.name.lower())


def _candidate_processed_model_dirs(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted([p for p in processed_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if _contains_pointcloud_viewer_asset(child):
            continue
        tileset = _find_tileset_json(child)
        if tileset:
            candidates.append(_ensure_tileset_alias(tileset).parent)
            continue
        for nested in sorted([p for p in child.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            if _contains_pointcloud_viewer_asset(nested):
                continue
            tileset = _find_tileset_json(nested)
            if tileset:
                candidates.append(_ensure_tileset_alias(tileset).parent)
    return candidates


def _display_model_folder_name(model_root: Path, processed_root: Path) -> str:
    if model_root.name.lower() in {"scene", "data", "tiles"} and model_root.parent != processed_root:
        return model_root.parent.name
    return model_root.name


def _safe_extract_zip(zip_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    root = extract_root.resolve()
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for member in zip_ref.infolist():
                name = member.filename.replace("\\", "/")
                if not name or name.startswith("/") or name.startswith("../") or "/../" in name:
                    raise HTTPException(status_code=400, detail="ZIP contains unsafe paths")
                target = (extract_root / name).resolve()
                if not target.is_relative_to(root):
                    raise HTTPException(status_code=400, detail="ZIP contains unsafe paths")
            zip_ref.extractall(extract_root)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc


def _find_extracted_tileset_root(extract_root: Path) -> Path:
    tileset = _find_tileset_json(extract_root)
    if not tileset:
        raise HTTPException(
            status_code=400,
            detail="ZIP does not contain a Cesium root tileset JSON. Expected tileset.json or a root JSON such as Production_*.json.",
        )
    return _ensure_tileset_alias(tileset).parent


def _write_processing_jobs(data: dict[str, list[dict[str, str]]]) -> None:
    jobs_path = _processing_jobs_file()
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        jobs_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass


def _upsert_processing_job(project_id: str, job: dict[str, str]) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    existing = next((item for item in current if item.get("job_id") == job.get("job_id")), None)
    if isinstance(existing, dict):
        merged = dict(existing)
        merged.update(job)
        job = merged
    current = [item for item in current if item.get("job_id") != job.get("job_id")]
    current.insert(0, job)
    jobs[project_id] = current[:200]
    _write_processing_jobs(jobs)
    catalog_service.mirror_processing_job(project_id, job)
    status = str(job.get("status") or "").strip().lower()
    if status in {"processing", "uploading", "uploaded", "queued", "running", "pending", "converting cog"}:
        _invalidate_project_files_cache(project_id)


def _remove_project_processing_jobs(project_id: str) -> None:
    jobs = _read_processing_jobs()
    if project_id in jobs:
        del jobs[project_id]
        _write_processing_jobs(jobs)








def _pointcloud_slice_exports_root(project_id: str, dataset_id: str) -> Path:
    return _project_exports_root(project_id) / "pointcloud_slices" / _safe_ept_folder_name(dataset_id)


def _read_text_marker(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _pointcloud_raw_candidates(project_id: str, dataset_id: str) -> list[Path]:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_ept_folder_name(dataset_id)
    project_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id
    raw_root = project_root / "raw"
    candidates: list[Path] = []

    def add_candidate(path: Path | None) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved not in candidates and resolved.suffix.lower() in {".las", ".laz"}:
            candidates.append(resolved)

    status_ids = {safe_dataset_id}
    for job in _read_processing_jobs().get(safe_project_id, []):
        if not isinstance(job, dict):
            continue
        if str(job.get("job_id") or "") == safe_dataset_id:
            file_name = str(job.get("file_name") or "").strip()
            if file_name:
                add_candidate(raw_root / f"{safe_project_id}__{file_name}")
                add_candidate(raw_root / file_name)
            for key in ("dataset_id", "ept_dataset_id", "file_name"):
                value = str(job.get(key) or "").strip()
                if value:
                    status_ids.add(value)

    for status_id in list(status_ids):
        try:
            status = _read_dataset_status(safe_project_id, _safe_dataset_id(status_id))
        except HTTPException:
            status = None
        if status:
            raw_rel = str(status.get("raw_rel_path") or "").strip()
            if raw_rel:
                add_candidate(Path(LOCAL_DATA_PATH) / raw_rel)
            dataset_name = str(status.get("dataset_name") or "").strip()
            if dataset_name:
                add_candidate(raw_root / f"{safe_project_id}__{dataset_name}")
                add_candidate(raw_root / dataset_name)

    for dataset_dir in (
        _ept_dataset_dir(safe_project_id, safe_dataset_id),
        _legacy_ept_pointcloud_dataset_dir(safe_project_id, safe_dataset_id),
        _legacy_ept_dataset_dir(safe_project_id, safe_dataset_id),
    ):
        source_name = _read_text_marker(dataset_dir / ".source_name.txt")
        if source_name:
            add_candidate(raw_root / f"{safe_project_id}__{source_name}")
            add_candidate(raw_root / source_name)
        add_candidate(dataset_dir / "output.copc.laz")

    if raw_root.is_dir():
        normalized_target = _safe_export_stem(safe_dataset_id).lower()
        for source in raw_root.glob("*"):
            if source.suffix.lower() not in {".las", ".laz"}:
                continue
            stem = _safe_export_stem(source.stem).lower()
            if normalized_target in stem or stem in normalized_target:
                add_candidate(source)

    return candidates


def _resolve_pointcloud_slice_source(project_id: str, dataset_id: str) -> Path:
    project_root = (Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id)).resolve()
    for candidate in _pointcloud_raw_candidates(project_id, dataset_id):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if project_root not in resolved.parents:
            continue
        return resolved
    raise FileNotFoundError("No LAS/LAZ source found for this point cloud. Reprocess or keep the raw upload available for clipped CSV export.")










def _safe_tile_folder_name(tile_folder: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._ /-]{1,300}", tile_folder or "") or ".." in tile_folder:
        raise HTTPException(status_code=400, detail="Invalid tile_folder")
    return tile_folder.strip("/")


def _ring_score(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0
    score = 0.0
    closed = points + [points[0]]
    for idx in range(len(points)):
        lat_a, lng_a = closed[idx]
        lat_b, lng_b = closed[idx + 1]
        score += lng_a * lat_b - lng_b * lat_a
    return abs(score)


def _normalize_crop_points(points: list[list[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for pair in points:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        try:
            lat = float(pair[0])
            lng = float(pair[1])
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        normalized.append([lat, lng])
    if len(normalized) >= 2 and normalized[0] == normalized[-1]:
        normalized.pop()
    if len(normalized) < 3:
        raise HTTPException(status_code=400, detail="At least 3 valid points required")
    return normalized


def _extract_kml_points(kml_text: str) -> list[list[float]]:
    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid KML: {exc}") from exc
    candidates: list[list[list[float]]] = []
    for node in root.iter():
        if node.tag.endswith("coordinates") and node.text and node.text.strip():
            points: list[list[float]] = []
            for token in node.text.strip().split():
                parts = token.split(",")
                if len(parts) < 2:
                    continue
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                except ValueError:
                    continue
                points.append([lat, lon])
            if len(points) >= 3:
                try:
                    candidates.append(_normalize_crop_points(points))
                except HTTPException:
                    continue
    if not candidates:
        raise HTTPException(status_code=400, detail="KML coordinates not found")
    return max(candidates, key=_ring_score)


def _save_crop_mask(project_id: str, tile_folder: str, source: str, points: list[list[float]]) -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO dataset_crop_masks (project_id, tile_folder, source, points_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, tile_folder)
            DO UPDATE SET source=excluded.source, points_json=excluded.points_json, updated_at=excluded.updated_at
            """,
            (
                project_id,
                tile_folder,
                source,
                json.dumps(points, ensure_ascii=True),
                _now_iso(),
            ),
        )
        connection.commit()


def _get_crop_mask(project_id: str, tile_folder: str) -> dict[str, str] | None:
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT source, points_json, updated_at
            FROM dataset_crop_masks
            WHERE project_id = ? AND tile_folder = ?
            """,
            (project_id, tile_folder),
        ).fetchone()
    if not row:
        return None
    return {
        "source": str(row["source"]),
        "points_json": str(row["points_json"]),
        "updated_at": str(row["updated_at"]),
    }


def _safe_spatial_id(value: str, label: str = "id") -> str:
    clean = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", clean):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return clean












def _is_valid_tile_dataset(folder: Path) -> bool:
    """
    Accept either:
    - classical tiled-raster metadata (`tilemapresource.xml`), OR
    - plain XYZ output where only zoom folders + PNG tiles exist.
    """
    if not folder.is_dir():
        return False
    if (folder / "tilemapresource.xml").is_file():
        return True
    zoom_dirs = [d for d in folder.iterdir() if d.is_dir() and d.name.isdigit()]
    if not zoom_dirs:
        return False
    for zdir in zoom_dirs:
        if any(p.is_file() and p.suffix.lower() == ".png" for p in zdir.rglob("*.png")):
            return True
    return False


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


def _read_raster_manual_metadata(file_path: Path, dataset_type: str = "") -> dict[str, str]:
    suffix = file_path.suffix.lower()
    if suffix not in (".tif", ".tiff"):
        return {}
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform_bounds
    except Exception:
        return {}
    try:
        with rasterio.open(str(file_path)) as src:
            out: dict[str, str] = {}
            if src.crs:
                out["source_crs"] = str(src.crs)
                authority = src.crs.to_authority()
                if authority and authority[0] and authority[1]:
                    out["detected_epsg"] = f"{authority[0]}:{authority[1]}"
                bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
                clean_bounds = [
                    max(-180.0, float(bounds[0])),
                    max(-85.05112878, float(bounds[1])),
                    min(180.0, float(bounds[2])),
                    min(85.05112878, float(bounds[3])),
                ]
                if all(math.isfinite(value) for value in clean_bounds):
                    out["bounds_wgs84"] = json.dumps(clean_bounds)
            normalized_type = _normalize_dataset_type(dataset_type, file_path.name)
            rescale = _sample_raster_percentiles(src, normalized_type)
            if rescale:
                out["rescale_min"] = str(rescale[0])
                out["rescale_max"] = str(rescale[1])
            return out
    except Exception:
        return {}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_portal_error_log(area: str, message: str, **extra: object) -> None:
    try:
        ERROR_LOG_DIR.mkdir(parents=True, exist_ok=True)
        clean_extra = {
            key: value
            for key, value in extra.items()
            if value is not None and value != ""
        }
        record = {
            "timestamp": _now_iso(),
            "area": str(area or "portal")[:120],
            "message": str(message or "Unknown error")[:12000],
            **clean_extra,
        }
        log_path = ERROR_LOG_DIR / f"portal_errors_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass


HIDEABLE_USER_TABS = {"dashboard", "datasets", "map", "globe", "compare", "downloads"}


def _normalize_hidden_tabs(value: object) -> list[str]:
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            parsed = []
    else:
        parsed = value
    if not isinstance(parsed, list):
        return []
    clean: list[str] = []
    for item in parsed:
        tab_id = str(item).strip()
        if tab_id in HIDEABLE_USER_TABS and tab_id not in clean:
            clean.append(tab_id)
    return clean




















def _set_session_cookie(response: Response, raw_token: str) -> None:
    response.set_cookie(
        key=SESSION_COOKIE_NAME,
        value=_sign_session_token(raw_token),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
    )


def _clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE_NAME)


def _clear_session_auth_cache() -> None:
    _SESSION_USER_CACHE.clear()
























def _backfill_processed_sizes() -> None:
    projects_root = Path(LOCAL_DATA_PATH) / "projects"
    if not projects_root.is_dir():
        return
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        project_id = project_dir.name
        changed_any = False
        for st in _project_dataset_statuses(project_id):
            dataset_id = str(st.get("dataset_id") or "").strip()
            if not dataset_id or str(st.get("processed_size") or "").strip():
                continue
            rel = str(st.get("tiles_rel_path") or st.get("model_rel_path") or st.get("vector_rel_path") or "").strip()
            path = Path(LOCAL_DATA_PATH) / rel if rel else None
            if not path or not path.exists():
                tile_folder = str(st.get("tile_folder") or "").strip()
                if tile_folder:
                    _, processed_root = get_project_dirs(project_id)
                    path = processed_root / tile_folder
            if not path or not path.exists():
                continue
            size_bytes = calculate_folder_size(path)
            st["processed_size_bytes"] = str(size_bytes)
            st["processed_size"] = _format_size_bytes(size_bytes)
            _write_dataset_status(project_id, dataset_id, st)
            changed_any = True
        if changed_any:
            _invalidate_project_files_cache(project_id)


@app.on_event("startup")
def startup() -> None:
    ensure_tables()
    _backfill_processed_sizes()




































def _picker_filter_for_kind(kind: str) -> str:
    normalized = str(kind or "").strip().lower()
    if normalized == "las":
        return "LAS/LAZ (*.las;*.laz)|*.las;*.laz|All files (*.*)|*.*"
    if normalized == "ortho":
        return "Ortho GeoTIFF (*.tif;*.tiff)|*.tif;*.tiff|All files (*.*)|*.*"
    if normalized in {"dtm", "dsm"}:
        label = normalized.upper()
        return f"{label} GeoTIFF (*.tif;*.tiff)|*.tif;*.tiff|All files (*.*)|*.*"
    return "Supported files (*.las;*.laz;*.tif;*.tiff)|*.las;*.laz;*.tif;*.tiff|All files (*.*)|*.*"




































































































def _resolve_dataset_tiles_dir(project_id: str, dataset_name: str) -> Path | None:
    processed_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "processed"
    direct_candidates = [processed_root / dataset_name]
    for dtype in ("ortho", "dtm", "dsm", "other"):
        direct_candidates.append(processed_root / dtype / dataset_name)
    for direct in direct_candidates:
        if _is_valid_tile_dataset(direct):
            return direct
        if direct.is_dir():
            return direct

    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs"
    if not jobs_root.is_dir():
        return None

    target_variants = {
        dataset_name.strip(),
        f"{dataset_name.strip()}.tif",
        f"{dataset_name.strip()}.tiff",
    }
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        status_path = job_dir / ".status.json"
        if not status_path.is_file():
            continue
        try:
            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        name = str(loaded.get("dataset_name", "")).strip()
        tile_folder = str(loaded.get("tile_folder", "")).strip()
        if name in target_variants:
            tiles_rel_path = str(loaded.get("tiles_rel_path", "")).strip()
            candidates: list[Path] = []
            if tiles_rel_path:
                candidates.append(Path(LOCAL_DATA_PATH) / tiles_rel_path)
            if tile_folder:
                candidates.append(processed_root / tile_folder)
                for dtype in ("ortho", "dtm", "dsm", "other"):
                    candidates.append(processed_root / dtype / tile_folder)
            for resolved in candidates:
                if _is_valid_tile_dataset(resolved):
                    return resolved
                if resolved.is_dir():
                    return resolved
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


def _analysis_cache_dir(project_id: str) -> Path:
    path = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_analysis_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _file_fingerprint(path: Path) -> dict[str, str]:
    st = path.stat()
    return {
        "path": path.resolve().as_posix(),
        "size": str(st.st_size),
        "mtime_ns": str(st.st_mtime_ns),
    }


def _cache_path(project_id: str, kind: str, payload: object) -> Path:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return _analysis_cache_dir(project_id) / f"{kind}-{digest[:24]}.json"


def _read_cache(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None


def _write_cache(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _project_dataset_statuses(project_id: str) -> list[dict[str, str]]:
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs"
    if not jobs_root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for job_dir in sorted([p for p in jobs_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        st = _read_dataset_status(project_id, job_dir.name)
        if not st:
            continue
        rows.append({**st, "dataset_id": st.get("dataset_id") or job_dir.name})
    return rows


def _dataset_status_by_id(project_id: str, dataset_id: str) -> dict[str, str]:
    safe_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(project_id, safe_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset not found")
    st["dataset_id"] = st.get("dataset_id") or safe_id
    return st


def _dataset_source_path(project_id: str, dataset_id: str) -> Path:
    st = _dataset_status_by_id(project_id, dataset_id)
    raw_rel = (st.get("raw_rel_path") or "").strip()
    if raw_rel:
        path = (Path(LOCAL_DATA_PATH) / raw_rel).resolve()
        if path.is_file():
            return path
    tile_folder = (st.get("tile_folder") or "").strip()
    raw_dir, _ = get_project_dirs(project_id)
    for ext in (".tif", ".tiff", ".csv"):
        candidate = raw_dir / f"{tile_folder}{ext}"
        if tile_folder and candidate.is_file():
            return candidate.resolve()
    raise HTTPException(status_code=404, detail="Source file not found")


def _grid_coordinate_range(start: float, stop: float, step: float, descending: bool = False):
    value = float(start)
    stop_value = float(stop)
    step_value = abs(float(step))
    if descending:
        while value >= stop_value:
            yield value
            value -= step_value
    else:
        while value <= stop_value:
            yield value
            value += step_value


def _grid_sample_value(sample, nodata) -> float | None:
    value = sample[0]
    if np.ma.is_masked(value):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    if nodata is not None and np.isclose(value, float(nodata)):
        return None
    return value


def _safe_export_stem(value: str, fallback: str = "dataset") -> str:
    stem = Path(os.path.basename(value.strip() or fallback)).stem or fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned[:120] or fallback


def _grid_export_raster_path(project_id: str, dataset_id: str) -> tuple[Path, dict[str, str]]:
    st = _dataset_status_by_id(project_id, dataset_id)
    layer_type = st.get("layer_type") or _raster_layer_type(
        st.get("dataset_type", ""),
        st.get("dataset_name", dataset_id),
    )
    if layer_type not in {"DTM", "DSM"}:
        raise HTTPException(status_code=400, detail="Grid export is available only for DTM/DSM datasets.")

    local_root = Path(LOCAL_DATA_PATH).resolve()
    for key in ("cog_path", "raw_rel_path"):
        value = (st.get(key) or "").strip()
        if not value:
            continue
        path = Path(value).resolve() if key == "cog_path" else (local_root / value).resolve()
        if path.is_file() and (path == local_root or path.is_relative_to(local_root)):
            return path, st

    cog_rel = (st.get("cog_rel_path") or "").strip()
    if cog_rel:
        path = (local_root / cog_rel).resolve()
        if path.is_file() and path.is_relative_to(local_root):
            return path, st

    return _dataset_source_path(project_id, dataset_id), st












def _grid_export_output_path(
    project_id: str,
    dataset_id: str,
    output_name: str,
) -> Path:
    export_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "exports" / "grid" / dataset_id
    export_root.mkdir(parents=True, exist_ok=True)
    return export_root / os.path.basename(output_name)


def _write_grid_export_metadata(
    output_path: Path,
    payload: dict[str, str | int | float],
) -> None:
    metadata_path = output_path.with_suffix(f"{output_path.suffix}.json")
    try:
        metadata_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass




def _generate_grid_export_file(
    output_path: Path,
    dataset_path: Path,
    interval: float,
    export_format: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")
    generator = _csv_grid_generator(dataset_path, interval) if export_format == "csv" else _dxf_grid_generator(dataset_path, interval)
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
            for chunk in generator:
                if chunk:
                    handle.write(chunk)
        tmp_path.replace(output_path)
    finally:
        tmp_path.unlink(missing_ok=True)




def _require_rasterio():
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform as rio_transform  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=501,
            detail="Raster analysis requires rasterio. Install rasterio in backend environment.",
        ) from exc
    return rasterio, rio_transform














class LLatLng:
    def __init__(self, lat: float, lng: float) -> None:
        self.lat = math.radians(lat)
        self.lng = math.radians(lng)

    def distance_to(self, other: "LLatLng") -> float:
        dlat = other.lat - self.lat
        dlng = other.lng - self.lng
        a = math.sin(dlat / 2) ** 2 + math.cos(self.lat) * math.cos(other.lat) * math.sin(dlng / 2) ** 2
        return 6371008.8 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _read_volume_csv(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            month = str(row.get("month") or row.get("Month") or row.get("date") or "").strip()
            label = str(row.get("label") or row.get("Label") or month or path.stem).strip()
            def num(*keys: str) -> float:
                for key in keys:
                    val = row.get(key)
                    if val not in (None, ""):
                        try:
                            return float(str(val).replace(",", ""))
                        except ValueError:
                            continue
                return 0.0
            rows.append({
                "month": month,
                "label": label,
                "volume": num("volume", "Volume", "net", "Net"),
                "cut": num("cut", "Cut"),
                "fill": num("fill", "Fill"),
                "net": num("net", "Net", "volume", "Volume"),
                "area": num("area", "Area"),
                "source": "csv",
            })
    return rows














def _admin_dataset_status_by_key(project_id: str, dataset_key: str) -> tuple[str, dict[str, str]]:
    raw_key = dataset_key.replace("\\", "/").strip().strip("/")
    clean_key = os.path.basename(raw_key)
    if not clean_key or clean_key in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid dataset_name")
    decoded = clean_key
    normalized_decoded = _safe_export_stem(decoded).lower()
    for st in _project_dataset_statuses(project_id):
        dataset_id = str(st.get("dataset_id") or "")
        dataset_name = str(st.get("dataset_name") or "")
        tile_folder = str(st.get("tile_folder") or "")
        raw_rel = str(st.get("raw_rel_path") or "")
        candidates = {
            dataset_id,
            dataset_name,
            Path(dataset_name).stem,
            tile_folder,
            Path(tile_folder).name,
            Path(raw_rel).name,
            Path(raw_rel).stem,
        }
        normalized_candidates = {_safe_export_stem(candidate).lower() for candidate in candidates if candidate}
        if decoded in candidates or normalized_decoded in normalized_candidates:
            return dataset_id, st
    raise HTTPException(status_code=404, detail="Dataset not found")


def _remove_processing_job(project_id: str, dataset_id: str) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    jobs[project_id] = [item for item in current if str(item.get("job_id")) != dataset_id]
    _write_processing_jobs(jobs)
    catalog_service.remove_asset_db(project_id, dataset_id)


def _safe_remove_dataset_path(path: Path) -> int:
    local_root = Path(LOCAL_DATA_PATH).resolve()
    target = path.resolve()
    if target == local_root or not target.is_relative_to(local_root):
        raise HTTPException(status_code=400, detail="Invalid dataset target path")
    if not target.exists():
        return 0
    if target.is_dir():
        shutil.rmtree(target)
        return 1
    target.unlink(missing_ok=True)
    return 1


def _safe_rename_dataset_path(path: Path, display_name: str) -> Path:
    local_root = Path(LOCAL_DATA_PATH).resolve()
    target = path.resolve()
    if not target.exists() or target == local_root or not target.is_relative_to(local_root):
        return path
    clean_stem = _safe_export_stem(display_name).strip("-_ .")[:120] or "dataset"
    if target.is_file():
        prefix = ""
        name = target.name
        if "__" in name:
            prefix = name.split("__", 1)[0] + "__"
        next_path = target.with_name(f"{prefix}{clean_stem}{target.suffix}")
    else:
        next_path = target.with_name(clean_stem)
    if next_path.resolve() == target:
        return target
    if next_path.exists():
        next_path = target.with_name(f"{next_path.stem}-{secrets.token_hex(4)}{next_path.suffix}")
    target.rename(next_path)
    return next_path




def _dataset_status_matches_rel(project_id: str, st: dict[str, str], rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/").strip("/")
    candidates = [
        str(st.get("raw_rel_path") or ""),
        str(st.get("tiles_rel_path") or ""),
        str(st.get("tileset_rel_path") or ""),
        str(st.get("vector_rel_path") or ""),
        str(st.get("model_rel_path") or ""),
        str(st.get("cog_rel_path") or ""),
    ]
    tile_folder = str(st.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = get_project_dirs(project_id)
        candidates.append((processed_root / tile_folder).relative_to(Path(LOCAL_DATA_PATH)).as_posix())
        dtype = str(st.get("dataset_type") or "").strip()
        typed_root = processed_root / _dataset_type_folder(dtype) / tile_folder
        candidates.append(typed_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix())
    for candidate in candidates:
        clean = candidate.replace("\\", "/").strip("/")
        if not clean:
            continue
        if rel_path == clean or rel_path.startswith(f"{clean}/") or clean.startswith(f"{rel_path}/"):
            return True
    return False


def _find_dataset_status_for_rel(project_id: str, rel_path: str) -> tuple[str, dict[str, str]] | None:
    for st in _project_dataset_statuses(project_id):
        dataset_id = str(st.get("dataset_id") or "").strip()
        if dataset_id and _dataset_status_matches_rel(project_id, st, rel_path):
            return dataset_id, st
    return None




























def _dataset_extra_response_fields(st: dict[str, str]) -> dict[str, str]:
    cog_rel_path = str(st.get("cog_rel_path") or "")
    cog_path = str(st.get("cog_path") or "")
    if cog_rel_path:
        cog_path = str((Path(LOCAL_DATA_PATH) / cog_rel_path).resolve())
    elif cog_path:
        cog_path = _rebase_project_data_path(cog_path)
    return {
        "processed_size": str(st.get("processed_size") or ""),
        "upload_date": str(st.get("upload_date") or st.get("date") or st.get("created_at") or ""),
        "height_offset": str(st.get("height_offset") or ""),
        "stage": str(st.get("stage") or ""),
        "progress_percent": str(st.get("progress_percent") or ""),
        "eta_seconds": str(st.get("eta_seconds") or ""),
        "cog_path": cog_path,
        "cog_rel_path": cog_rel_path,
        "rescale_min": str(st.get("rescale_min") or ""),
        "rescale_max": str(st.get("rescale_max") or ""),
        "bounds_wgs84": str(st.get("bounds_wgs84") or ""),
        "source_crs": str(st.get("source_crs") or ""),
        "detected_epsg": str(st.get("detected_epsg") or ""),
        "manual_epsg": str(st.get("manual_epsg") or ""),
        "applied_epsg": str(st.get("applied_epsg") or ""),
    }


def _canonical_file_row(row: dict[str, str]) -> dict[str, str]:
    dataset_id = str(row.get("dataset_id") or "").strip()
    rel_path = str(row.get("rel_path") or row.get("raw_rel_path") or row.get("cog_rel_path") or "").strip()
    display_name = str(row.get("display_name") or row.get("name") or dataset_id or Path(rel_path).name).strip()
    viewer_url = str(row.get("viewer_url") or row.get("layer_url") or row.get("file_url") or "").strip()
    canonical_key = str(row.get("canonical_key") or dataset_id or rel_path or display_name).strip()
    row["display_name"] = display_name
    row["name"] = str(row.get("name") or display_name)
    row["viewer_url"] = viewer_url
    row["asset_status"] = str(row.get("asset_status") or row.get("status") or "").strip()
    row["canonical_key"] = canonical_key
    row["source_rel_path"] = str(row.get("source_rel_path") or row.get("raw_rel_path") or rel_path)
    return row


def _ensure_project_file_access(request: Request, project_id: str) -> dict[str, str | int]:
    user = _require_user(request)
    if str(user.get("role", "")).lower() != "admin":
        _ensure_project_owner(int(user["id"]), project_id)
    return user


def _safe_project_file_response_path(project_id: str, file_path: str) -> Path:
    safe_project_id = _safe_project_id(project_id)
    base_dir = (Path(LOCAL_DATA_PATH) / "projects" / safe_project_id).resolve()
    cleaned_path = file_path.replace("\\", "/").lstrip("/")
    if "\x00" in cleaned_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target_path = (base_dir / cleaned_path).resolve()
    base_abs = os.path.abspath(str(base_dir))
    target_abs = os.path.abspath(str(target_path))
    if target_abs != base_abs and not target_abs.startswith(base_abs + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if target_path.is_dir():
        copc_candidate = _copc_asset_in_dir(target_path)
        if copc_candidate is not None:
            target_path = copc_candidate
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target_path


def _copc_range_response(target_path: Path, request: Request) -> Response:
    file_size = target_path.stat().st_size
    range_header = request.headers.get("range")
    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
        "Content-Type": "application/octet-stream",
    }
    if not range_header:
        response = FileResponse(str(target_path), media_type="application/octet-stream")
        response.headers.update(common_headers)
        return response

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})

    start_raw, end_raw = match.groups()
    if start_raw == "" and end_raw == "":
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
    if start_raw == "":
        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1

    if start >= file_size or start < 0 or end < start:
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
    end = min(end, file_size - 1)
    content_length = end - start + 1

    def iter_file() -> bytes:
        with open(target_path, "rb") as handle:
            handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        **common_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
    }
    return StreamingResponse(iter_file(), status_code=206, headers=headers, media_type="application/octet-stream")


def _serve_project_data_file(project_id: str, file_path: str, request: Request) -> Response:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    target_path = _safe_project_file_response_path(safe_project_id, file_path)
    cleaned_request_path = file_path.replace("\\", "/").lower().lstrip("/")
    if cleaned_request_path.startswith("raw/") and target_path.suffix.lower() in {".las", ".laz"}:
        raise HTTPException(status_code=404, detail="Raw point cloud download is not available")
    if target_path.name.lower().endswith(".copc.laz"):
        return _copc_range_response(target_path, request)
    response = FileResponse(str(target_path))
    if cleaned_request_path.startswith("processed/"):
        response.headers["Cache-Control"] = "private, max-age=86400"
    return response


def _serve_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    _safe_project_id(project_id)
    _require_user(request)
    raise HTTPException(
        status_code=410,
        detail="Legacy point cloud file serving is disabled. Use the Droid EPT viewer endpoint.",
    )












def _secure_dataset_file(project_id: str, dataset_id: str, report_only: bool = False) -> tuple[Path, dict[str, str]]:
    safe_dataset_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(project_id, safe_dataset_id)
    if not st:
        if report_only:
            reports_dir = Path(LOCAL_DATA_PATH) / "reports" / project_id
            if reports_dir.is_dir():
                for report in reports_dir.rglob("*.pdf"):
                    report_id = re.sub(r"[^A-Za-z0-9._-]+", "-", report.stem).strip("-")[:180]
                    if report_id == safe_dataset_id:
                        return report.resolve(), {"dataset_name": report.name, "dataset_type": "reports"}
        raise HTTPException(status_code=404, detail="File not found")
    rel = str(st.get("report_rel_path") or st.get("raw_rel_path") or "").strip()
    if not rel:
        raise HTTPException(status_code=404, detail="File not found")
    if not report_only and str(st.get("dataset_type") or "").lower() == "pointcloud":
        raise HTTPException(status_code=404, detail="Raw point cloud download is not available. Open the processed 3D viewer instead.")
    if report_only and str(st.get("dataset_type") or "").lower() != "reports":
        raise HTTPException(status_code=404, detail="Report not found")
    if ".." in rel or rel.startswith("/") or rel.startswith("\\"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    path = (Path(LOCAL_DATA_PATH) / rel).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if local_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if report_only and path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="Report not found")
    return path, st








_CATALOG_FORCE_DISK_SCAN: set[str] = set()


def _dedupe_pointcloud_file_rows(files: list[dict[str, str]], project_id: str) -> list[dict[str, str]]:
    def canonical_pointcloud_key(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            text = unquote(text)
        except Exception:
            pass
        text = Path(text.replace("\\", "/")).name
        stem = Path(text).stem.lower()
        stem = stem.replace(project_id.lower(), "")
        stem = re.sub(r"^(?:ept|copc|pointcloud|point-cloud|pc)(?=[0-9._\-\s])[\W_]*", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[\W_]*(?:ept|copc|pointcloud|point-cloud|pc)$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[-_][a-f0-9]{8,}$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[^a-z0-9]+", "", stem)
        return stem

    def pointcloud_keys(*values: object) -> set[str]:
        keys: set[str] = set()
        for value in values:
            key = canonical_pointcloud_key(value)
            if key:
                keys.add(key)
            stem = Path(str(value or "")).stem.lower()
            if stem:
                keys.add(stem)
        return keys

    def _is_pointcloud_catalog_row(row: dict[str, str]) -> bool:
        signature = " ".join(
            str(row.get(key) or "").lower()
            for key in ("kind", "type", "layer_type", "dataset_type", "name", "viewer_type")
        )
        if "pointcloud" in signature or "point cloud" in signature:
            return True
        name = str(row.get("name") or row.get("display_name") or "").lower()
        rel = str(row.get("rel_path") or row.get("raw_rel_path") or row.get("source_rel_path") or "").lower()
        if name.endswith((".las", ".laz", ".copc.laz")) or rel.endswith((".las", ".laz", ".copc.laz")):
            return True
        viewer_url = str(row.get("viewer_url") or row.get("layer_url") or "").lower()
        if "/droid-ept-viewer/" in viewer_url or viewer_url.endswith("/ept.json") or "copc=" in viewer_url:
            return True
        return str(row.get("viewer_type") or "").lower() == "copc"

    def pointcloud_row_rank(row: dict[str, str]) -> int:
        viewer_url = str(row.get("viewer_url") or row.get("layer_url") or "").strip().lower()
        if str(row.get("viewer_type") or "").lower() == "copc":
            return 0
        if viewer_url and ("copc=" in viewer_url or viewer_url.endswith(".copc.laz")):
            return 0
        status = str(row.get("status") or "").strip().lower()
        if status in {"processing", "uploaded", "queued", "running"}:
            return 1
        name = str(row.get("name") or row.get("display_name") or "").lower()
        rel = str(row.get("rel_path") or row.get("raw_rel_path") or "").lower()
        if (name.endswith((".las", ".laz")) or rel.endswith((".las", ".laz"))) and not viewer_url:
            return 9
        return 2

    canonical_files: list[dict[str, str]] = []
    pointcloud_groups: list[dict[str, object]] = []
    ignored_identity_keys = {"ept", "copc", "pointcloud", "point-cloud", "pc", "output", "index", "las", "laz"}
    for index, file_row in enumerate(files):
        row = _canonical_file_row(file_row)
        if not _is_pointcloud_catalog_row(row):
            canonical_files.append(row)
            continue
        keys = {
            key
            for key in pointcloud_keys(
                row.get("canonical_key"),
                row.get("display_name"),
                row.get("name"),
                row.get("dataset_id"),
                row.get("source_rel_path"),
                row.get("raw_rel_path"),
                row.get("rel_path"),
            )
            if len(key) >= 3 and key not in ignored_identity_keys
        }
        matching = [group for group in pointcloud_groups if keys.intersection(group["keys"])]
        if not matching:
            pointcloud_groups.append({"keys": set(keys), "rows": [(index, row)]})
            continue
        primary = matching[0]
        primary["keys"].update(keys)
        primary["rows"].append((index, row))
        for extra in matching[1:]:
            primary["keys"].update(extra["keys"])
            primary["rows"].extend(extra["rows"])
            pointcloud_groups.remove(extra)

    for group in pointcloud_groups:
        ranked_rows = sorted(
            group["rows"],
            key=lambda item: (pointcloud_row_rank(item[1]), item[0]),
        )
        winner = dict(ranked_rows[0][1])
        for _, candidate in ranked_rows[1:]:
            for field, value in candidate.items():
                if not str(winner.get(field) or "").strip() and str(value or "").strip():
                    winner[field] = value
        if pointcloud_row_rank(winner) >= 9 and any(pointcloud_row_rank(row) <= 2 for _, row in ranked_rows):
            continue
        if str(winner.get("viewer_url") or "").strip():
            winner["status"] = "WEB-READY"
            winner["asset_status"] = "WEB-READY"
            winner["layer_type"] = "pointcloud"
            winner["dataset_type"] = "pointcloud"
        canonical_files.append(_canonical_file_row(winner))
    return canonical_files








def _purge_catalog_dataset(project_id: str, dataset_key: str) -> dict[str, int | str]:
    safe_project_id = _safe_project_id(project_id)
    clean_key = str(dataset_key or "").strip()
    if not clean_key:
        raise HTTPException(status_code=400, detail="Invalid dataset key")
    removed = 0
    if catalog_service.catalog_db_enabled():
        removed += catalog_service.delete_assets_by_key(
            safe_project_id,
            clean_key,
            local_data_path=LOCAL_DATA_PATH,
        )
    jobs = _read_processing_jobs()
    current = jobs.get(safe_project_id, [])
    normalized_key = _safe_export_stem(clean_key).lower()
    next_jobs: list[dict[str, str]] = []
    for job in current:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("job_id") or "")
        file_name = str(job.get("file_name") or "")
        job_stem = _safe_export_stem(Path(file_name).stem or file_name).lower()
        job_id_stem = _safe_export_stem(job_id).lower()
        if (
            clean_key in {job_id, file_name}
            or normalized_key in {job_stem, job_id_stem}
            or normalized_key in job_id.lower()
            or normalized_key in file_name.lower()
        ):
            catalog_service.remove_asset_db(safe_project_id, job_id)
            continue
        next_jobs.append(job)
    if len(next_jobs) != len(current):
        jobs[safe_project_id] = next_jobs
        _write_processing_jobs(jobs)

    candidate_names = {clean_key, Path(clean_key).stem, os.path.basename(clean_key)}
    for name in candidate_names:
        if not name:
            continue
        for resolver in (
            _ept_dataset_dir,
            _legacy_ept_pointcloud_dataset_dir,
            _legacy_ept_dataset_dir,
        ):
            candidate = resolver(safe_project_id, name)
            if candidate.exists():
                removed += _safe_remove_dataset_path(candidate)
            compat = candidate.with_name(f"{candidate.name}__ept_viewer")
            if compat.exists():
                removed += _safe_remove_dataset_path(compat)
        raw_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "raw"
        for raw_candidate in (
            raw_root / name,
            raw_root / f"{safe_project_id}__{name}",
            raw_root / f"{safe_project_id}__{Path(name).name}",
        ):
            if raw_candidate.exists():
                removed += _safe_remove_dataset_path(raw_candidate)

    try:
        dataset_id, st = _admin_dataset_status_by_key(safe_project_id, clean_key)
        removed += _delete_dataset_artifacts(safe_project_id, dataset_id, st)
    except HTTPException:
        pass

    _invalidate_project_files_cache(safe_project_id)
    if catalog_service.catalog_db_enabled():
        catalog_service.prune_missing_assets(safe_project_id, LOCAL_DATA_PATH)
        catalog_service.bump_revision(safe_project_id)
    return {"status": "success", "removed_paths": removed}














