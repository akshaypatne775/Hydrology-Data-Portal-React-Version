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
from app.services.dataset_service import (_infer_dataset_type, _normalize_dataset_type, _raster_layer_type, _read_dataset_status, _write_dataset_status, _read_dataset_manifest, _write_dataset_manifest, _status_manifest_payload, _write_status_manifests, _sync_dataset_metadata_to_processing_job, _delete_dataset_artifacts, _deep_rename_dataset_artifacts)
from app.services.upload_service import (_upload_session_dir, _dataset_upload_session_dir, _ensure_disk_space_for_bytes, _merge_upload_chunks)
from app.services.analysis_service import (_sample_raster, _interpolate_profile_points, _profile_summary, _volume_for_raster, _dtm_volume_between, _circle_points, _pixel_area_m2)
from app.services.grid_export_service import (_csv_grid_generator, _dxf_grid_generator, _generate_grid_export_background, _csv_grid_rows, _dxf_grid_rows, _validate_grid_export_request, _grid_export_is_current)
from app.services.spatial_feature_service import (_ensure_spatial_layer, _insert_spatial_feature, _spatial_row_to_dict, _normalize_spatial_feature_geojson, _can_manage_spatial_feature)
from app.services.bulk_import_service import (_browse_server_folder, _bulk_scan_files, _admin_manual_bulk_import_background, _prepare_admin_manual_bulk_import, _queue_admin_manual_bulk_import)

from app.services.raster import *
from app.core.utils import *
from app.core.paths import *

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




DJI_TERRA_DEM_CMAP = _build_dji_terra_colormap()
rio_colormap.cmap = rio_colormap.cmap.register(
    {
        "agisoft_dem": DJI_TERRA_DEM_CMAP,
        "dji_terra_dem": DJI_TERRA_DEM_CMAP,
    },
    overwrite=True,
)

from titiler.core.factory import TilerFactory

from app.core.lifespan import app_lifespan

app = FastAPI(
    title="Droid Survair Cloud Portal API",
    description="Backend services for drone survey data and mapping.",
    version="0.1.0",
    lifespan=app_lifespan,
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




























TRANSPARENT_PNG_TILE = _transparent_png_tile()
ORTHO_RENDERER_VERSION = "edge-padding-v7"






























































































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












































































































































































HIDEABLE_USER_TABS = {"dashboard", "datasets", "map", "globe", "compare", "downloads"}




























































































































































































































































class LLatLng:
    def __init__(self, lat: float, lng: float) -> None:
        self.lat = math.radians(lat)
        self.lng = math.radians(lng)

    def distance_to(self, other: "LLatLng") -> float:
        dlat = other.lat - self.lat
        dlng = other.lng - self.lng
        a = math.sin(dlat / 2) ** 2 + math.cos(self.lat) * math.cos(other.lat) * math.sin(dlng / 2) ** 2
        return 6371008.8 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
























































































_CATALOG_FORCE_DISK_SCAN: set[str] = set()























