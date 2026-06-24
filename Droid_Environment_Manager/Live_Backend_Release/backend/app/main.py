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
from datetime import datetime, timezone
from importlib.util import find_spec
from urllib.parse import quote
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
from fastapi.responses import FileResponse, RedirectResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
import laspy
import numpy as np
from PIL import Image
from pydantic import BaseModel
from rio_tiler import colormap as rio_colormap
from rio_tiler.io import Reader

from app.core.database import configure_database, ensure_tables, get_db_connection
from app.utils.spatial_import import (
    normalize_structure_type,
    parse_spatial_upload,
    style_for_structure,
)
from app.utils.raster_tiler import convert_tif_to_cog, run_rasterio_tiler

# Project_Data lives beside backend/ and frontend/ (repo root).
BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _load_local_env_file() -> None:
    env_path = BASE_DIR / "backend" / ".env"
    if not env_path.is_file():
        return
    try:
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    except OSError as exc:
        print(f"Could not load backend .env: {exc}")


_load_local_env_file()

_DEFAULT_PROJECT_DATA = BASE_DIR / "Project_Data"
LOCAL_DATA_PATH = os.getenv("LOCAL_DATA_PATH", str(_DEFAULT_PROJECT_DATA))

def _strip_file_scheme(value: str) -> str:
    clean = (value or "").strip()
    if clean.lower().startswith("file:///"):
        return clean[8:]
    if clean.lower().startswith("file://"):
        return clean[7:]
    return clean


def _rebase_project_data_path(value: str) -> str:
    clean = _strip_file_scheme(value)
    if not clean:
        return clean
    normalized = clean.replace("\\", "/")
    marker = f"/{Path(LOCAL_DATA_PATH).name.lower()}/"
    marker_index = normalized.lower().rfind(marker)
    if marker_index < 0:
        return clean
    rel = normalized[marker_index + len(marker):].lstrip("/")
    if not rel or ".." in rel.split("/"):
        return clean
    return str((Path(LOCAL_DATA_PATH) / rel).resolve())


def _local_data_path_from_user_value(value: str) -> Path:
    return Path(os.path.abspath(_rebase_project_data_path(value))).resolve()

# Map tiles, ortho, DEM, terrain quantized-mesh, videos, etc. are served only
# through authenticated routes below. Do not mount Project_Data as StaticFiles.
DATABASE_DIR = Path(LOCAL_DATA_PATH)

Path(LOCAL_DATA_PATH).mkdir(parents=True, exist_ok=True)
configure_database(DATABASE_DIR)

# Large uploads: headroom above merged file size (e.g. 14GB LAS + merge buffer).
DISK_HEADROOM_BYTES = int(os.getenv("UPLOAD_DISK_HEADROOM_MB", "512")) * 1024 * 1024
MERGE_COPY_BUFFER_BYTES = 8 * 1024 * 1024  # streaming merge, avoid loading whole file in RAM
POINTCLOUD_SRS_IN = os.getenv("POINTCLOUD_SRS_IN", "").strip()
POINTCLOUD_SRS_OUT = os.getenv("POINTCLOUD_SRS_OUT", "4978").strip()
OSGEO4W_BAT = os.getenv(
    "OSGEO4W_BAT",
    r"C:\Program Files\QGIS 3.44.8\OSGeo4W.bat",
).strip()
POTREE_CONVERTER_EXE = os.getenv(
    "POTREE_CONVERTER_EXE",
    r"C:\PotreeConverter\PotreeConverter.exe",
).strip()
PROJECT_FILES_CACHE_TTL_SECONDS = float(os.getenv("PROJECT_FILES_CACHE_TTL_SECONDS", "4"))
TIFF_TILE_BUDGET_MB = float(os.getenv("TIFF_TILE_BUDGET_MB", "100"))
TIFF_TILE_MIN_ZOOM_LIMIT = int(os.getenv("TIFF_TILE_MIN_ZOOM_LIMIT", "14"))
TIFF_TILE_MAX_ZOOM_LIMIT = int(os.getenv("TIFF_TILE_MAX_ZOOM_LIMIT", "19"))
TIFF_TILE_SIZE = int(os.getenv("TIFF_TILE_SIZE", "256"))
SESSION_COOKIE_NAME = "droid_cloud_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "604800"))
SESSION_SECRET_FILE = Path(LOCAL_DATA_PATH) / ".session_signing_secret"
OWNER_APPROVAL_EMAIL = os.getenv("OWNER_APPROVAL_EMAIL", "akshaydroid123@gmail.com").strip()
ADMIN_ALERT_PHONE = os.getenv("ADMIN_ALERT_PHONE", "+917057723981").strip()
PUBLIC_PORTAL_URL = os.getenv("PUBLIC_PORTAL_URL", "https://portal.droidminingsolutions.com").strip()
PORTAL_VERSION = os.getenv("PORTAL_VERSION", f"local-{int(time.time())}").strip()


def _load_persistent_session_secret() -> str:
    env_secret = os.getenv("SESSION_SIGNING_SECRET", "").strip()
    if env_secret:
        return env_secret
    try:
        if SESSION_SECRET_FILE.is_file():
            saved = SESSION_SECRET_FILE.read_text(encoding="utf-8").strip()
            if saved:
                return saved
        generated = secrets.token_urlsafe(48)
        SESSION_SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        SESSION_SECRET_FILE.write_text(generated, encoding="utf-8")
        print(
            "WARNING: SESSION_SIGNING_SECRET is not set. "
            "Using a generated local secret persisted on disk.",
        )
        return generated
    except OSError:
        fallback = secrets.token_urlsafe(48)
        print(
            "WARNING: SESSION_SIGNING_SECRET is not set and local secret file could not be written. "
            "Using ephemeral in-memory secret; sessions may reset on restart.",
        )
        return fallback


SESSION_SIGNING_SECRET_RAW = _load_persistent_session_secret()
SESSION_SIGNING_SECRET = SESSION_SIGNING_SECRET_RAW.encode("utf-8")
_PROJECT_FILES_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}
FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:3000",
    "https://portal.droidminingsolutions.com",
]
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_HEAVY_REQUESTS = 5
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}
DIRECT_RASTER_UPLOAD_LIMIT_BYTES = 1024 * 1024 * 1024


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
if find_spec("multipart") is None:
    print(
        "WARNING: python-multipart is not installed. "
        "Upload endpoints may fail with 422/validation errors.",
    )


class Debug404Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 404 and request.url.path.startswith("/data/"):
            rel = request.url.path.replace("/data/", "", 1).lstrip("/")
            expected_path = Path(LOCAL_DATA_PATH) / rel
            print(f"âŒ [DEBUG 404] Frontend requested: {request.url.path}")
            print(f"ðŸ” [DEBUG 404] Backend looked for file at: {expected_path.resolve()}")
            print(f"ðŸ“‚ [DEBUG 404] Does this file exist? {expected_path.exists()}")
        return response


class ActivityTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/api/auth/logout", "/api/version", "/health"}:
            return response
        try:
            user = _get_optional_user(request)
            forwarded_for = request.headers.get("x-forwarded-for", "")
            forwarded_ip = forwarded_for.split(",", 1)[0].strip()
            ip_address = forwarded_ip or (request.client.host if request.client else "unknown")
            device_label = request.headers.get("x-droid-device", "").strip()[:160]
            lat_raw = request.headers.get("x-droid-lat", "").strip()
            lng_raw = request.headers.get("x-droid-lng", "").strip()
            accuracy_raw = request.headers.get("x-droid-location-accuracy", "").strip()
            latitude = float(lat_raw) if lat_raw else None
            longitude = float(lng_raw) if lng_raw else None
            location_accuracy = float(accuracy_raw) if accuracy_raw else None
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO activity_logs (
                        user_id, ip_address, method, endpoint, device_label,
                        latitude, longitude, location_accuracy, accessed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(user["id"]) if user else None,
                        ip_address,
                        request.method,
                        request.url.path,
                        device_label,
                        latitude,
                        longitude,
                        location_accuracy,
                        _now_iso(),
                    ),
                )
                connection.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"Activity tracking failed: {exc}")
        return response


class ProtectedDataPathMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.replace("\\", "/").lower()
        if path.startswith("/api/titiler/") or path.startswith("/api/dji-terra/") or path.startswith("/api/ortho-cog/"):
            source_url = request.query_params.get("url")
            if source_url:
                try:
                    local_root = Path(LOCAL_DATA_PATH).resolve()
                    target = _local_data_path_from_user_value(source_url)
                    if target != local_root and not target.is_relative_to(local_root):
                        return Response(status_code=403)
                except Exception:  # noqa: BLE001
                    return Response(status_code=403)
        admin_only_upload = (
            path.startswith("/api/upload")
            or path.startswith("/api/complete-upload")
            or path.startswith("/api/complete-dataset-upload")
            or path in {"/api/process-dataset", "/api/process-pointcloud", "/api/dataset-metadata"}
            or "metadata-probe" in path
            or re.match(r"^/api/datasets/[^/]+/(sync|open-manual-folder)$", path) is not None
            or ("manual" in path and ("folder" in path or "sync" in path))
        )
        if admin_only_upload and request.method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            try:
                user = _require_user(request)
                if str(user.get("role", "")).lower() != "admin":
                    return Response(status_code=403)
            except HTTPException:
                return Response(status_code=403)
        if path.startswith("/data/") and ("/raw/" in path or path.endswith(".pdf")):
            # Raw assets and PDFs must be served through authenticated /api endpoints
            # so project paths are not exposed in the browser.
            try:
                _require_user(request)
            except HTTPException:
                return Response(status_code=404)
            return Response(status_code=404)
        return await call_next(request)


app.add_middleware(ProtectedDataPathMiddleware)
app.add_middleware(ActivityTrackingMiddleware)
app.add_middleware(Debug404Middleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(
    cog_tiler.router,
    prefix="/api/titiler",
    tags=["TiTiler"],
)

# Study metrics placeholders for report/demo API responses (PDF scope 964 Acres).
CATCHMENT_STATS: list[dict[str, str]] = [
    {"label": "Gross catchment", "value": "390.0", "unit": "ha"},
    {"label": "Net contributing", "value": "382.4", "unit": "ha"},
    {"label": "Delineation", "value": "D8 / filled DEM", "unit": ""},
    {"label": "Pour point", "value": "Outlet chainage 0", "unit": ""},
]

STREAM_STATS: list[dict[str, str]] = [
    {"label": "Main channel length", "value": "4.85", "unit": "km"},
    {"label": "Reach average slope", "value": "1.2", "unit": "%"},
    {"label": "Design n (placeholder)", "value": "0.035", "unit": ""},
]

LULC_ROWS: list[dict[str, str | int]] = [
    {"name": "Agriculture", "pct": 42, "color": "#2dd4bf"},
    {"name": "Forest / scrub", "pct": 28, "color": "#0d9488"},
    {"name": "Built-up / roads", "pct": 18, "color": "#5eead4"},
    {"name": "Water / wetland", "pct": 8, "color": "#67e8f9"},
    {"name": "Other / bare", "pct": 4, "color": "#94a3b8"},
]

IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}


class IssuePayload(BaseModel):
    lat: float
    lng: float
    title: str
    description: str
    status: str = "open"


class Issue(IssuePayload):
    id: int


class SpatialFeaturePayload(BaseModel):
    layer_id: str = ""
    layer_name: str = "Drawn Shapes"
    geojson: dict[str, object]
    plot_id: str = ""
    owner_name: str = ""
    structure_type: str = "Unassigned"
    source_type: str = "drawn"


class SpatialFeaturePatchPayload(BaseModel):
    geojson: dict[str, object] | None = None
    plot_id: str | None = None
    owner_name: str | None = None
    structure_type: str | None = None


class PointCloudProcessPayload(BaseModel):
    filename: str
    project_id: str = "default-project"


class AdminManualBulkImportTask(BaseModel):
    source_folder: str
    kind: str  # las | ortho | dtm | dsm


class AdminManualBulkImportPayload(BaseModel):
    project_id: str = ""
    tasks: list[AdminManualBulkImportTask]
    max_parallel: int = 2


class AdminLocateFolderPayload(BaseModel):
    initial_path: str = ""


class CompleteUploadPayload(BaseModel):
    filename: str
    totalChunks: int
    project_id: str = "default-project"


class CompleteDatasetUploadPayload(BaseModel):
    filename: str
    totalChunks: int
    project_id: str = "default-project"
    dataset_type: str = ""
    month: str = ""
    created_at: str = ""
    epsg: str = ""


class AuthPayload(BaseModel):
    email: str
    password: str


class ProjectCreatePayload(BaseModel):
    name: str
    location: str
    date: str
    status: str
    type: str


class ProjectUpdatePayload(BaseModel):
    name: str = ""


class ProjectOut(BaseModel):
    id: str
    name: str
    location: str
    date: str
    status: str
    type: str


class ProcessDatasetOut(BaseModel):
    status: str
    message: str
    project_id: str
    dataset_id: str
    dataset_name: str
    cog_path: str
    cog_tile_url_template: str


class FileDeletePayload(BaseModel):
    rel_path: str


class CropMaskPayload(BaseModel):
    points: list[list[float]]


class ProfilePayload(BaseModel):
    dataset_id: str
    points: list[list[float]]
    samples: int = 120
    corridor_width_m: float = 1.0


class VolumePayload(BaseModel):
    dataset_id: str
    points: list[list[float]] = []
    circle_center: list[float] = []
    circle_radius_m: float = 0.0
    base_elevation: float | None = None


class CompareVolumePayload(BaseModel):
    dataset_ids: list[str] = []


class ContourGeneratePayload(BaseModel):
    dataset_id: str = ""
    source_tif: str = ""
    interval: float = 5.0


class DatasetMetaPayload(BaseModel):
    dataset_id: str
    month: str = ""
    dataset_type: str = ""


class DatasetOwnerPathMetaPayload(BaseModel):
    height_offset: float | None = None


class AdminProjectPatchPayload(BaseModel):
    name: str | None = None
    location: str | None = None
    date: str | None = None
    status: str | None = None
    type: str | None = None


class AdminDatasetMetaPayload(BaseModel):
    dataset_id: str
    name: str | None = None
    date: str | None = None
    status: str | None = None
    dataset_type: str | None = None
    month: str | None = None
    height_offset: float | None = None


class AdminDatasetPathMetaPayload(BaseModel):
    name: str | None = None
    date: str | None = None
    status: str | None = None
    dataset_type: str | None = None
    month: str | None = None
    height_offset: float | None = None


class AdminUserApprovalPayload(BaseModel):
    role: str = "user"


class AdminUserRolePayload(BaseModel):
    role: str


class AdminUserHiddenTabsPayload(BaseModel):
    hidden_tabs: list[str] = []


class CameraViewPayload(BaseModel):
    name: str
    lat: float
    lng: float
    height: float
    heading: float
    pitch: float
    roll: float = 0.0


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


def _infer_dataset_type(name: str) -> str:
    lowered = name.lower()
    suffix = Path(lowered).suffix
    if "dtm" in lowered or "dem" in lowered:
        return "dtm"
    if "dsm" in lowered:
        return "dsm"
    if "ortho" in lowered:
        return "ortho"
    if suffix == ".csv":
        return "csv"
    if suffix == ".zip":
        return "3dmodel"
    if suffix in (".kml", ".geojson"):
        return "vector"
    if suffix == ".dwg":
        return "cad"
    if suffix == ".pdf":
        return "reports"
    if suffix in (".tif", ".tiff"):
        return "ortho"
    if suffix in (".las", ".laz"):
        return "pointcloud"
    return "dataset"


def _raster_layer_type(dataset_type: str, name: str = "") -> str:
    normalized = _normalize_dataset_type(dataset_type, name)
    if normalized == "dtm":
        return "DTM"
    if normalized == "dsm":
        return "DSM"
    if normalized == "ortho":
        return "Ortho"
    return "cog"


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
        "las": "pointcloud",
        "laz": "pointcloud",
        "csv": "csv",
        "vector": "vector",
        "kml": "vector",
        "geojson": "vector",
        "cad": "cad",
        "dwg": "cad",
        "pdf": "reports",
        "report": "reports",
        "reports": "reports",
    }
    return aliases.get(normalized) or _infer_dataset_type(fallback_name)


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


@app.get("/api/ortho-cog/bounds")
async def ortho_cog_bounds(request: Request, url: str) -> dict[str, list[float]]:
    _require_user(request)
    cog_path = _secure_local_cog_path(url)
    bounds = await run_in_threadpool(_read_cog_bounds_wgs84, cog_path)
    return {"bounds": bounds}


@app.get("/api/dji-terra/tiles/WebMercatorQuad/{z}/{x}/{y}@{scale}x")
async def dji_terra_dem_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    scale: int,
    url: str,
    rescale: str = "",
):
    _require_user(request)
    del scale
    parsed_rescale = _parse_rescale_pair(rescale)
    if parsed_rescale is None:
        raise HTTPException(status_code=422, detail="rescale=min,max is required for DJI Terra DEM tiles.")
    cog_path = _secure_local_cog_path(url)
    try:
        tile_bytes = await run_in_threadpool(_render_dji_terra_tile, cog_path, z, x, y, parsed_rescale)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        expected_empty_tile = any(
            text in message
            for text in (
                "outside bounds",
                "outside image bounds",
                "does not overlap",
                "empty",
                "no data",
                "nodata",
            )
        )
        headers = {"Cache-Control": "public, max-age=3600"}
        if not expected_empty_tile:
            headers["X-Tile-Error"] = str(exc)[:200]
        return Response(content=TRANSPARENT_PNG_TILE, media_type="image/png", headers=headers)

    return Response(
        content=tile_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/api/ortho-cog/tiles/WebMercatorQuad/{z}/{x}/{y}@{scale}x")
async def ortho_cog_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    scale: int,
    url: str,
):
    _require_user(request)
    del scale
    cog_path = _secure_local_cog_path(url)
    try:
        tile_bytes = await run_in_threadpool(_render_ortho_cog_tile, cog_path, z, x, y)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        expected_empty_tile = any(
            text in message
            for text in (
                "outside bounds",
                "outside image bounds",
                "does not overlap",
                "empty",
                "no data",
                "nodata",
            )
        )
        headers = {"Cache-Control": "public, max-age=3600", "X-Ortho-Renderer": ORTHO_RENDERER_VERSION}
        if not expected_empty_tile:
            headers["X-Tile-Error"] = str(exc)[:200]
        return Response(content=TRANSPARENT_PNG_TILE, media_type="image/png", headers=headers)

    return Response(
        content=tile_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600", "X-Ortho-Renderer": ORTHO_RENDERER_VERSION},
    )


def _upload_session_dir(filename: str, total_chunks: int, project_id: str) -> Path:
    """Stable temp folder for one logical upload (same as frontend chunk sequence)."""
    safe_name = _safe_pointcloud_basename(filename)
    digest = hashlib.sha256(
        f"{project_id}\0{safe_name}\0{total_chunks}".encode("utf-8"),
    ).hexdigest()
    return Path(LOCAL_DATA_PATH) / "uploads" / "chunks" / digest


def _dataset_upload_session_dir(filename: str, total_chunks: int, project_id: str) -> Path:
    """Stable temp folder for one large raster dataset upload."""
    safe_name = _safe_dataset_upload_basename(filename)
    digest = hashlib.sha256(
        f"dataset\0{project_id}\0{safe_name}\0{total_chunks}".encode("utf-8"),
    ).hexdigest()
    return Path(LOCAL_DATA_PATH) / "uploads" / "dataset_chunks" / digest


def _ensure_disk_space_for_bytes(path_on_volume: Path, required_extra: int) -> None:
    """Fail fast if volume cannot hold required_extra bytes (with headroom)."""
    path_on_volume.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(path_on_volume)
    if usage.free < required_extra + DISK_HEADROOM_BYTES:
        raise HTTPException(
            status_code=507,
            detail=(
                "Insufficient disk space for this upload. "
                f"Need at least {required_extra + DISK_HEADROOM_BYTES} bytes free "
                f"(including {DISK_HEADROOM_BYTES} bytes headroom)."
            ),
        )


def _normalize_tileset_into_output_root(output_dir: Path) -> None:
    """
    Ensure tileset.json lives at output_dir root so static URL
    /data/pointclouds/<project_id>/<tileset_id>/tileset.json resolves correctly.
    py3dtiles sometimes writes a nested folder under --out.
    """
    root_tileset = output_dir / "tileset.json"
    if root_tileset.is_file():
        return

    candidates = sorted(
        output_dir.rglob("tileset.json"),
        key=lambda p: (len(p.parts), str(p)),
    )
    if not candidates:
        return

    inner_tileset = candidates[0]
    if inner_tileset.parent.resolve() == output_dir.resolve():
        return

    parent = inner_tileset.parent
    for child in list(parent.iterdir()):
        dest = output_dir / child.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest, ignore_errors=True)
            else:
                dest.unlink(missing_ok=True)
        shutil.move(str(child), str(dest))

    try:
        if parent.is_dir() and parent.resolve() != output_dir.resolve():
            shutil.rmtree(parent, ignore_errors=True)
    except OSError:
        pass


async def process_pointcloud_background(
    final_path: Path,
    output_dir: Path,
    project_id: str | None = None,
    job_id: str | None = None,
    file_name: str | None = None,
) -> None:
    """
    Background conversion worker:
    py3dtiles convert <final_path> --out <output_dir>
    """
    if output_dir.is_dir():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    err_path = output_dir / ".conversion_error.txt"
    if err_path.exists():
        err_path.unlink(missing_ok=True)

    cmd = [
        "py3dtiles",
        "convert",
        str(final_path),
        "--out",
        str(output_dir),
    ]
    # Optional CRS reprojection for local/projected LAS sources.
    srs_in = POINTCLOUD_SRS_IN or _detect_input_srs(final_path) or ""
    if srs_in:
        cmd.extend(["--srs_in", srs_in])
    if POINTCLOUD_SRS_OUT:
        cmd.extend(["--srs_out", POINTCLOUD_SRS_OUT])

    try:
        subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        _normalize_tileset_into_output_root(output_dir)
        if project_id and job_id:
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name or final_path.name,
                    "status": "Completed",
                    "updated_at": _now_iso(),
                    "result_url": f"/data/pointclouds/{project_id}/{output_dir.name}/tileset.json",
                },
            )
            _invalidate_project_files_cache(project_id)
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr or exc.stdout or str(exc)
        print("py3dtiles conversion failed:", msg)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        if project_id and job_id:
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name or final_path.name,
                    "status": "Failed",
                    "error": msg[:8000],
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(project_id)
    except FileNotFoundError:
        msg = (
            "py3dtiles executable not found on PATH. "
            "Install py3dtiles in the backend environment and restart the API."
        )
        print(msg)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        if project_id and job_id:
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name or final_path.name,
                    "status": "Failed",
                    "error": msg[:8000],
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(project_id)


def process_pointcloud(input_las: str, output_dir: str, dataset_name: str) -> None:
    """
    Convert LAS/LAZ to a Potree web viewer.
    PotreeConverter.exe is expected at C:\\PotreeConverter\\ unless POTREE_CONVERTER_EXE is set.
    """
    safe_dataset_name = _potree_dataset_name(dataset_name)
    output_path = Path(output_dir)
    if output_path.is_dir():
        shutil.rmtree(output_path, ignore_errors=True)
    output_path.mkdir(parents=True, exist_ok=True)

    command = (
        f'"{POTREE_CONVERTER_EXE}" "{input_las}" '
        f'-o "{output_path}" --generate-page {safe_dataset_name}'
    )
    result = subprocess.run(command, capture_output=True, text=True, shell=True)
    if result.returncode != 0:
        message = result.stderr or result.stdout or f"PotreeConverter exited with code {result.returncode}"
        raise RuntimeError(message)

    html_path = output_path / f"{safe_dataset_name}.html"
    if not html_path.is_file():
        found_html = sorted(output_path.glob("*.html"), key=lambda p: p.name.lower())
        if found_html:
            shutil.copyfile(found_html[0], html_path)
    _brand_potree_viewer(output_path, safe_dataset_name)


def process_pointcloud_potree_job(
    input_las: str,
    output_dir: str,
    dataset_name: str,
    project_id: str,
    job_id: str,
    file_name: str,
    source_hash: str = "",
) -> None:
    output_path = Path(output_dir)
    err_path = output_path / ".conversion_error.txt"
    try:
        process_pointcloud(input_las, output_dir, dataset_name)
        (output_path / ".source_name.txt").write_text(file_name, encoding="utf-8")
        if source_hash:
            (output_path / ".source_hash.txt").write_text(source_hash, encoding="utf-8")
        _upsert_processing_job(
            project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud",
                "file_name": file_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/projects/{project_id}/processed/{dataset_name}/{dataset_name}.html",
            },
        )
    except Exception as exc:
        msg = str(exc)
        output_path.mkdir(parents=True, exist_ok=True)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        _upsert_processing_job(
            project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud",
                "file_name": file_name,
                "status": "Failed",
                "error": msg[:8000],
                "updated_at": _now_iso(),
            },
        )
    finally:
        _invalidate_project_files_cache(project_id)


def process_contours_background(
    project_id: str,
    dataset_id: str,
    input_tif: str,
    output_geojson: str,
    interval: float,
    dataset_name: str,
) -> None:
    out_path = Path(output_geojson)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f'call "{OSGEO4W_BAT}" gdal_contour -a elev -i {interval:g} '
        f'"{input_tif}" "{output_geojson}"'
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            executable=os.environ.get("COMSPEC", "cmd.exe"),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "").strip() or "gdal_contour failed")
        rel = out_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        _write_dataset_status(
            project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "tile_folder": "",
                "dataset_type": "vector",
                "layer_type": "Vector",
                "raw_rel_path": rel,
                "vector_rel_path": rel,
            },
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "vector",
                "file_name": dataset_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{rel}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "vector",
                "file_name": dataset_name,
                "status": "Failed",
                "error": str(exc)[:8000],
                "updated_at": _now_iso(),
            },
        )
    finally:
        _invalidate_project_files_cache(project_id)


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


def get_project_dirs(project_id: str) -> tuple[Path, Path]:
    """Per-project raw uploads and Python Rasterio XYZ output under Project_Data/projects."""
    safe = _safe_project_id(project_id)
    project_dir = Path(LOCAL_DATA_PATH) / "projects" / safe
    raw_dir = project_dir / "raw"
    processed_dir = project_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def _dataset_type_folder(dataset_type: str) -> str:
    normalized = _normalize_dataset_type(dataset_type, "")
    if normalized in {"ortho", "dtm", "dsm", "pointcloud", "csv", "3dmodel", "vector", "cad"}:
        return normalized
    return "other"


def get_project_dataset_type_dirs(project_id: str, dataset_type: str) -> tuple[Path, Path]:
    raw_root, processed_root = get_project_dirs(project_id)
    folder = _dataset_type_folder(dataset_type)
    raw_dir = raw_root / folder
    processed_dir = processed_root / folder
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def _safe_tileset_id(tileset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", tileset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid tileset_id")
    return tileset_id


def _potree_dataset_name(name: str) -> str:
    stem = Path(name).stem if Path(name).suffix else name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    return _safe_tileset_id(cleaned[:120] or "pointcloud")


def _potree_html_url(base_url: str, project_id: str, dataset_name: str) -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_tileset_id(dataset_name)
    return (
        f"{base_url.rstrip('/')}/data/projects/{safe_project}/processed/"
        f"{safe_dataset}/{safe_dataset}.html"
    )


def _brand_potree_viewer(output_path: Path, dataset_name: str) -> None:
    """Apply Droid workspace branding and quick tools to a generated point-cloud viewer."""
    safe_dataset_name = _potree_dataset_name(dataset_name)
    html_path = output_path / f"{safe_dataset_name}.html"
    if not html_path.is_file():
        html_files = sorted(output_path.glob("*.html"), key=lambda p: p.name.lower())
        if not html_files:
            return
        html_path = html_files[0]

    droid_style = """
<style id="droid-pointcloud-theme">
  :root { color-scheme: dark; }
  body { margin: 0; background: #06171b; font-family: Montserrat, Arial, sans-serif; }
  #potree_render_area { background: radial-gradient(circle at 20% 10%, #123f49 0%, #06171b 48%, #020708 100%) !important; }
  #potree_sidebar_container {
    background: linear-gradient(180deg, rgba(14,62,73,0.96), rgba(4,19,24,0.96)) !important;
    border-right: 1px solid rgba(148, 206, 214, 0.28) !important;
    box-shadow: 14px 0 34px rgba(0,0,0,0.34);
  }
  #sidebar_header { min-height: 56px; padding: 14px 18px 8px; box-sizing: border-box; }
  #sidebar_header::before {
    content: "Droid 3D Point Cloud System";
    display: block;
    color: #f8fafc;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  #potree_branding, #potree_languages, #menu_about, #menu_about + div {
    display: none !important;
  }
  #potree_menu h3, .accordion > h3 {
    background: rgba(255,255,255,0.08) !important;
    border: 1px solid rgba(148,206,214,0.18) !important;
    color: #e6fbff !important;
    border-radius: 8px !important;
    margin: 8px 10px 0 !important;
    font-family: Montserrat, Arial, sans-serif !important;
    letter-spacing: 0.04em;
  }
  #potree_menu h3 + div, .pv-menu-list {
    background: rgba(3,16,20,0.42) !important;
    color: #d7eef2 !important;
    font-family: Montserrat, Arial, sans-serif !important;
  }
  .divider > span {
    color: #8bd6df !important;
    background: rgba(14,62,73,0.95) !important;
  }
  .ui-slider .ui-slider-range { background: #14b8a6 !important; }
  .ui-slider .ui-slider-handle {
    background: #ccfbf1 !important;
    border: 2px solid #0e3e49 !important;
    border-radius: 999px !important;
  }
  .droid-pointcloud-toolbar {
    position: fixed;
    top: 16px;
    right: 16px;
    z-index: 100000;
    min-width: 270px;
    padding: 14px;
    border: 1px solid rgba(203,251,241,0.26);
    border-radius: 14px;
    background: linear-gradient(145deg, rgba(14,62,73,0.82), rgba(3,16,20,0.72));
    box-shadow: 0 14px 34px rgba(0,0,0,0.38);
    backdrop-filter: blur(12px);
    color: #f8fafc;
    font-family: Montserrat, Arial, sans-serif;
  }
  .droid-pointcloud-toolbar__title {
    margin: 0 0 10px;
    font-size: 13px;
    font-weight: 800;
    letter-spacing: 0.08em;
    text-transform: uppercase;
  }
  .droid-pointcloud-toolbar__actions {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 8px;
  }
  .droid-pointcloud-toolbar button {
    min-height: 34px;
    border: 1px solid rgba(203,251,241,0.3);
    border-radius: 9px;
    background: #0e3e49;
    color: #f8fafc;
    font: 800 12px Montserrat, Arial, sans-serif;
    cursor: pointer;
  }
  .droid-pointcloud-toolbar button:hover {
    background: #14b8a6;
    color: #06272d;
  }
  .droid-pointcloud-toolbar__hint {
    margin: 10px 0 0;
    color: #b8dbe0;
    font-size: 11px;
    line-height: 1.35;
  }
</style>
"""
    droid_toolbar = """
<div class="droid-pointcloud-toolbar" aria-label="Droid point cloud tools">
  <p class="droid-pointcloud-toolbar__title">Droid 3D Point Cloud System</p>
  <div class="droid-pointcloud-toolbar__actions">
    <button type="button" onclick="window.droidStartCrossSection && window.droidStartCrossSection()">Cross Section</button>
    <button type="button" onclick="window.droidStartClipBox && window.droidStartClipBox()">Clip Box</button>
    <button type="button" onclick="window.droidClearSections && window.droidClearSections()">Clear Tools</button>
    <button type="button" onclick="window.viewer && window.viewer.fitToScreen()">Fit View</button>
  </div>
  <p class="droid-pointcloud-toolbar__hint">Use Cross Section, then click across the cloud to draw a profile line.</p>
</div>
"""
    droid_script = """
<script id="droid-pointcloud-tools">
  window.droidStartCrossSection = function () {
    if (!window.viewer || !viewer.profileTool) return;
    viewer.profileTool.startInsertion();
  };
  window.droidStartClipBox = function () {
    if (!window.viewer || !viewer.volumeTool) return;
    const volume = viewer.volumeTool.startInsertion({ clip: true });
    if (window.Potree && Potree.ClipTask) {
      viewer.setClipTask(Potree.ClipTask.SHOW_INSIDE);
    }
    return volume;
  };
  window.droidClearSections = function () {
    if (!window.viewer || !viewer.scene) return;
    const profiles = Array.from(viewer.scene.profiles || []);
    profiles.forEach(profile => viewer.scene.removeProfile(profile));
    const volumes = Array.from(viewer.scene.volumes || []).filter(volume => volume.clip);
    volumes.forEach(volume => viewer.scene.removeVolume(volume));
  };
</script>
"""

    try:
        html = html_path.read_text(encoding="utf-8", errors="replace")
        html = re.sub(r"<title>.*?</title>", "<title>Droid 3D Point Cloud System</title>", html, flags=re.I | re.S)
        if "droid-pointcloud-theme" not in html:
            html = html.replace("</head>", f"{droid_style}\n</head>")
        if "droid-pointcloud-toolbar" not in html:
            html = html.replace("<body>", f"<body>\n{droid_toolbar}", 1)
        if "droid-pointcloud-tools" not in html:
            html = html.replace("</body>", f"{droid_script}\n</body>")
        html = html.replace('viewer.setDescription("");', 'viewer.setDescription("Droid 3D Point Cloud System");')
        html_path.write_text(html, encoding="utf-8")
    except OSError:
        pass

    sidebar_path = output_path / "libs" / "potree" / "sidebar.html"
    if sidebar_path.is_file():
        try:
            sidebar = sidebar_path.read_text(encoding="utf-8", errors="replace")
            sidebar = re.sub(
                r'<span id="potree_branding" class="potree_sidebar_brand">.*?</span>\s*<div id="potree_languages"[^>]*></div>',
                '<span id="potree_branding" class="potree_sidebar_brand">Droid 3D Point Cloud System</span>',
                sidebar,
                flags=re.I | re.S,
            )
            sidebar = re.sub(
                r'<h3 id="menu_about">.*?</h3>\s*<div>.*?</div>\s*(?=</div>\s*</div>)',
                "",
                sidebar,
                flags=re.I | re.S,
            )
            sidebar_path.write_text(sidebar, encoding="utf-8")
        except OSError:
            pass

    css_path = output_path / "libs" / "potree" / "potree.css"
    if css_path.is_file():
        try:
            css = css_path.read_text(encoding="utf-8", errors="replace")
            if "droid-pointcloud-css-overrides" not in css:
                css += "\n\n/* droid-pointcloud-css-overrides */\n" + """
:root {
  --color-0: rgba(6, 23, 27, 1);
  --color-1: rgba(102, 151, 160, 1);
  --color-2: rgba(14, 62, 73, 1);
  --color-3: rgba(20, 184, 166, 1);
  --color-4: rgba(204, 251, 241, 1);
  --bg-color: rgba(6, 23, 27, 1);
  --bg-color-2: rgba(14, 62, 73, 1);
  --bg-light-color: rgba(14, 62, 73, 0.86);
  --bg-dark-color: rgba(3, 16, 20, 1);
  --bg-hover-color: rgba(20, 184, 166, 0.25);
  --font-color: #d7eef2;
  --font-color-2: #f8fafc;
}
"""
                css_path.write_text(css, encoding="utf-8")
        except OSError:
            pass


def _safe_dataset_id(dataset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", dataset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid dataset_id")
    return dataset_id


def _dataset_dir(project_id: str, dataset_id: str) -> Path:
    """Job metadata (.status.json) for a raster upload; tiles live under projects/.../processed/."""
    return Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs" / dataset_id


def _dataset_status_file(project_id: str, dataset_id: str) -> Path:
    return _dataset_dir(project_id, dataset_id) / ".status.json"


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


def _is_3d_model_dataset(folder: Path) -> bool:
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
        tileset = _find_tileset_json(child)
        if tileset:
            candidates.append(_ensure_tileset_alias(tileset).parent)
            continue
        for nested in sorted([p for p in child.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
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
    current = [item for item in current if item.get("job_id") != job.get("job_id")]
    current.insert(0, job)
    jobs[project_id] = current[:200]
    _write_processing_jobs(jobs)


def _sync_dataset_metadata_to_processing_job(project_id: str, dataset_id: str, st: dict[str, str]) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    matched = False
    for item in current:
        if item.get("job_id") != dataset_id:
            continue
        matched = True
        item["file_name"] = str(st.get("dataset_name") or item.get("file_name") or dataset_id)
        item["status"] = str(st.get("status") or item.get("status") or "Completed")
        item["updated_at"] = str(st.get("updated_at") or _now_iso())
        for key in (
            "height_offset",
            "dataset_type",
            "month",
            "raw_rel_path",
            "tiles_rel_path",
            "tileset_rel_path",
            "cog_path",
            "cog_rel_path",
            "rescale_min",
            "rescale_max",
            "bounds_wgs84",
        ):
            if key in st:
                item[key] = str(st.get(key) or "")
        break
    if not matched:
        current.insert(
            0,
            {
                "job_id": dataset_id,
                "kind": str(st.get("dataset_type") or "dataset"),
                "file_name": str(st.get("dataset_name") or dataset_id),
                "status": str(st.get("status") or "Completed"),
                "updated_at": str(st.get("updated_at") or _now_iso()),
                "height_offset": str(st.get("height_offset") or ""),
                "dataset_type": str(st.get("dataset_type") or ""),
            },
        )
    jobs[project_id] = current[:200]
    _write_processing_jobs(jobs)


def _write_dataset_status(project_id: str, dataset_id: str, payload: dict[str, str]) -> None:
    status_path = _dataset_status_file(project_id, dataset_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        status_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass


def _read_dataset_status(project_id: str, dataset_id: str) -> dict[str, str] | None:
    status_path = _dataset_status_file(project_id, dataset_id)
    if not status_path.is_file():
        return None
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return None
    return None


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


def _normalize_spatial_feature_geojson(raw: dict[str, object]) -> tuple[dict[str, object], str]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Invalid GeoJSON")
    geo_type = str(raw.get("type") or "")
    if geo_type == "Feature":
        geometry = raw.get("geometry")
        if not isinstance(geometry, dict):
            raise HTTPException(status_code=400, detail="GeoJSON Feature geometry is required")
        feature = dict(raw)
        props = feature.get("properties")
        feature["properties"] = props if isinstance(props, dict) else {}
        geometry_type = str(geometry.get("type") or "")
    else:
        geometry = raw
        geometry_type = geo_type
        feature = {"type": "Feature", "properties": {}, "geometry": geometry}
    if geometry_type not in {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}:
        raise HTTPException(status_code=400, detail="Unsupported geometry type")
    return feature, geometry_type


def _spatial_row_to_dict(row: sqlite3.Row) -> dict[str, object]:
    try:
        geojson_data = json.loads(str(row["geojson"]))
    except (TypeError, json.JSONDecodeError):
        geojson_data = {"type": "Feature", "properties": {}, "geometry": None}
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "layer_id": str(row["layer_id"]),
        "geometry_type": str(row["geometry_type"]),
        "geojson": geojson_data,
        "plot_id": str(row["plot_id"] or ""),
        "owner_name": str(row["owner_name"] or ""),
        "structure_type": str(row["structure_type"] or "Unassigned"),
        "fill_color": str(row["fill_color"] or "#f59e0b"),
        "stroke_color": str(row["stroke_color"] or "#f59e0b"),
        "source_type": str(row["source_type"] or ""),
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }


def _ensure_spatial_layer(
    connection: sqlite3.Connection,
    project_id: str,
    user_id: int,
    layer_id: str,
    layer_name: str,
    source_type: str,
) -> str:
    if layer_id:
        safe_layer_id = _safe_spatial_id(layer_id, "layer_id")
        row = connection.execute(
            "SELECT id FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial layer not found")
        return safe_layer_id

    clean_name = (layer_name or "Drawn Shapes").strip()[:180] or "Drawn Shapes"
    existing = connection.execute(
        """
        SELECT id FROM spatial_layers
        WHERE project_id = ? AND name = ? AND source_type = ?
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (project_id, clean_name, source_type),
    ).fetchone()
    if existing:
        return str(existing["id"])

    new_layer_id = f"layer_{secrets.token_hex(8)}"
    now = _now_iso()
    connection.execute(
        """
        INSERT INTO spatial_layers (
            id, project_id, owner_user_id, name, source_type, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (new_layer_id, project_id, user_id, clean_name, source_type, now, now),
    )
    return new_layer_id


def _insert_spatial_feature(
    connection: sqlite3.Connection,
    project_id: str,
    user_id: int,
    layer_id: str,
    geojson_data: dict[str, object],
    plot_id: str,
    owner_name: str,
    structure_type: str,
    source_type: str,
) -> dict[str, object]:
    feature, geometry_type = _normalize_spatial_feature_geojson(geojson_data)
    clean_structure = normalize_structure_type(structure_type)
    colors = style_for_structure(clean_structure)
    now = _now_iso()
    feature_id = f"spatial_{secrets.token_hex(8)}"
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    properties.update(
        {
            "plotId": plot_id,
            "ownerName": owner_name,
            "structureType": clean_structure,
        },
    )
    feature["properties"] = properties
    connection.execute(
        """
        INSERT INTO spatial_features (
            id, project_id, layer_id, owner_user_id, geometry_type, geojson,
            plot_id, owner_name, structure_type, fill_color, stroke_color,
            source_type, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feature_id,
            project_id,
            layer_id,
            user_id,
            geometry_type,
            json.dumps(feature, ensure_ascii=True),
            plot_id[:120],
            owner_name[:180],
            clean_structure,
            colors["fill_color"],
            colors["stroke_color"],
            source_type,
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM spatial_features WHERE id = ?",
        (feature_id,),
    ).fetchone()
    return _spatial_row_to_dict(row)


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


HIDEABLE_USER_TABS = {"map", "globe", "compare", "downloads"}


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


def _hash_password(password: str) -> str:
    if len(password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return f"pbkdf2_sha256${salt.hex()}${digest.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    try:
        algo, salt_hex, digest_hex = stored.split("$", 2)
        if algo != "pbkdf2_sha256":
            return False
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, TypeError):
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(actual, expected)


def _sign_session_token(raw_token: str) -> str:
    sig = hmac.new(SESSION_SIGNING_SECRET, raw_token.encode("utf-8"), hashlib.sha256).digest()
    return f"{raw_token}.{base64.urlsafe_b64encode(sig).decode('utf-8').rstrip('=')}"


def _unsign_session_token(signed_token: str) -> str | None:
    try:
        raw, sig = signed_token.rsplit(".", 1)
    except ValueError:
        return None
    expected = _sign_session_token(raw).rsplit(".", 1)[1]
    if not hmac.compare_digest(sig, expected):
        return None
    return raw


def _token_hash(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _send_owner_sms(message: str) -> None:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    to_number = os.getenv("ADMIN_ALERT_PHONE", ADMIN_ALERT_PHONE).strip()
    if not (sid and token and from_number and to_number):
        print(f"[SMS pending configuration] {message}")
        return
    payload = urlencode({"From": from_number, "To": to_number, "Body": message}).encode("utf-8")
    req = UrlRequest(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=payload,
        method="POST",
    )
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urlopen(req, timeout=8) as response:
            response.read()
    except URLError as exc:
        print(f"SMS send failed: {exc}")


def _send_email(to_email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", username or OWNER_APPROVAL_EMAIL).strip()
    if not (host and from_email):
        print(f"[Email pending configuration] To: {to_email}\nSubject: {subject}\n{body}")
        return
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except OSError as exc:
        print(f"Email send failed: {exc}")


def _approval_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/approvals/approve?token={quote(token)}"


def _create_pending_user(email: str, password: str, requested_role: str, request: Request) -> dict[str, str]:
    normalized_email = email.strip().lower()
    if "@" not in normalized_email:
        raise HTTPException(status_code=400, detail="Invalid email")
    role = "admin" if requested_role == "admin" else "user"
    password_hash = _hash_password(password)
    created_at = _now_iso()
    approval_token = secrets.token_urlsafe(40)
    approval_hash = _token_hash(approval_token)
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    email, password_hash, created_at, role, approval_status,
                    requested_role, approval_token_hash
                )
                VALUES (?, ?, ?, 'user', 'pending', ?, ?)
                """,
                (normalized_email, password_hash, created_at, role, approval_hash),
            )
            connection.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc

    approve_link = _approval_url(request, approval_token)
    _send_email(
        OWNER_APPROVAL_EMAIL,
        f"Droid Cloud approval request: {normalized_email}",
        (
            f"New {role} access request for Droid Cloud.\n\n"
            f"Email: {normalized_email}\n"
            f"Requested role: {role}\n"
            f"Approve here: {approve_link}\n\n"
            "Only approve this request if you recognize the person."
        ),
    )
    _send_owner_sms(f"Droid Cloud approval request: {normalized_email} requested {role} access.")
    return {"status": "pending", "email": normalized_email, "requested_role": role}


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


def _require_user(request: Request) -> dict[str, object]:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed:
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = _unsign_session_token(signed)
    if not raw:
        raise HTTPException(status_code=401, detail="Invalid session token")

    now_ts = int(datetime.now(timezone.utc).timestamp())
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT u.id AS user_id, u.email, u.role, u.approval_status, u.can_access_catalog, u.hidden_tabs
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at >= ?
            """,
            (_token_hash(raw), now_ts),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    if str(row["approval_status"] or "pending").lower() != "approved":
        raise HTTPException(status_code=403, detail="Account approval is pending")
    return {
        "id": int(row["user_id"]),
        "email": str(row["email"]),
        "role": str(row["role"] or "user"),
        "can_access_catalog": bool(row["can_access_catalog"]),
        "hidden_tabs": _normalize_hidden_tabs(row["hidden_tabs"]),
        "approval_status": str(row["approval_status"] or "pending"),
    }


def _get_optional_user(request: Request) -> dict[str, object] | None:
    try:
        return _require_user(request)
    except HTTPException:
        return None


def _require_admin(request: Request) -> dict[str, object]:
    user = _require_user(request)
    if str(user.get("role", "")).lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user


def verify_admin(request: Request) -> dict[str, object]:
    return _require_admin(request)


def _client_ip_for_limit(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    forwarded_ip = forwarded_for.split(",", 1)[0].strip()
    return forwarded_ip or (request.client.host if request.client else "unknown")


def _enforce_rate_limit(
    request: Request,
    bucket_name: str,
    limit: int = RATE_LIMIT_HEAVY_REQUESTS,
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
) -> None:
    # Chunked uploads legitimately send many sequential requests. Authentication,
    # project access checks, disk limits, and completion processing still protect
    # the upload flow; the generic heavy-request limiter must not block chunks.
    if request.url.path in {"/api/upload-chunk", "/api/upload-dataset-chunk"}:
        return
    now = time.monotonic()
    cutoff = now - window_seconds
    ip_address = _client_ip_for_limit(request)
    key = f"{bucket_name}:{ip_address}"
    bucket = [stamp for stamp in _RATE_LIMIT_BUCKETS.get(key, []) if stamp >= cutoff]
    if len(bucket) >= limit:
        retry_after = max(1, int(window_seconds - (now - bucket[0])))
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket
    if len(_RATE_LIMIT_BUCKETS) > 10_000:
        for stale_key in list(_RATE_LIMIT_BUCKETS):
            _RATE_LIMIT_BUCKETS[stale_key] = [
                stamp for stamp in _RATE_LIMIT_BUCKETS[stale_key] if stamp >= cutoff
            ]
            if not _RATE_LIMIT_BUCKETS[stale_key]:
                _RATE_LIMIT_BUCKETS.pop(stale_key, None)


def _is_admin_user_id(user_id: int) -> bool:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return bool(row and str(row["role"]).lower() == "admin")


def _ensure_project_owner(user_id: int, project_id: str) -> None:
    if _is_admin_user_id(user_id):
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT id FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row:
            return
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM projects WHERE id = ? AND owner_user_id = ?",
            (project_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")


@app.get("/api/pointcloud-status/{project_id}")
def pointcloud_status(
    project_id: str, request: Request, tileset_id: str | None = None
) -> dict[str, bool | str]:
    """
    Poll conversion progress: Potree HTML appears when PotreeConverter finishes.
    Older py3dtiles tileset.json outputs are still recognized for compatibility.
    If conversion fails, .conversion_error.txt is written under the output folder.
    """
    user = _require_user(request)
    safe_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_id)
    base_url = str(request.base_url).rstrip("/")
    potree_root = Path(LOCAL_DATA_PATH) / "projects" / safe_id / "processed"
    legacy_root = Path(LOCAL_DATA_PATH) / "pointclouds" / safe_id

    candidates: list[Path] = []
    if tileset_id:
        safe_tileset_id = _safe_tileset_id(tileset_id)
        candidates.append(potree_root / safe_tileset_id)
        candidates.append(legacy_root / safe_tileset_id)
    else:
        if potree_root.is_dir():
            children = sorted(
                [p for p in potree_root.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(children)
        if (legacy_root / "tileset.json").is_file():
            candidates.append(legacy_root)
        if legacy_root.is_dir():
            children = sorted(
                [p for p in legacy_root.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(children)

    for candidate in candidates:
        potree_html = candidate / f"{candidate.name}.html"
        if not potree_html.is_file():
            html_candidates = sorted(candidate.glob("*.html"), key=lambda p: p.name.lower())
            potree_html = html_candidates[0] if html_candidates else potree_html
        tileset = candidate / "tileset.json"
        err_file = candidate / ".conversion_error.txt"
        if err_file.is_file():
            try:
                msg = err_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                msg = "Unknown conversion error"
            if potree_root in candidate.parents or candidate == potree_root:
                url = _potree_html_url(base_url, safe_id, candidate.name)
            else:
                suffix = f"/{candidate.name}" if candidate.resolve() != legacy_root.resolve() else ""
                url = f"{base_url}/data/pointclouds/{safe_id}{suffix}/tileset.json"
            return {
                "ready": False,
                "failed": True,
                "error": msg[:8000],
                "tileset_url": url,
            }
        if potree_html.is_file():
            rel = potree_html.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            return {
                "ready": True,
                "failed": False,
                "tileset_url": f"{base_url}/data/{rel}",
            }
        if tileset.is_file():
            suffix = (
                f"/{candidate.name}" if candidate.resolve() != legacy_root.resolve() else ""
            )
            return {
                "ready": True,
                "failed": False,
                "tileset_url": f"{base_url}/data/pointclouds/{safe_id}{suffix}/tileset.json",
            }

    pending_suffix = f"/{_safe_tileset_id(tileset_id)}" if tileset_id else ""
    return {
        "ready": False,
        "failed": False,
        "tileset_url": (
            _potree_html_url(base_url, safe_id, _safe_tileset_id(tileset_id))
            if tileset_id
            else f"{base_url}/data/projects/{safe_id}/processed{pending_suffix}/"
        ),
    }


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


@app.post("/api/auth/signup")
def auth_signup(payload: AuthPayload, request: Request) -> dict[str, str]:
    return _create_pending_user(payload.email, payload.password, "user", request)


@app.post("/api/auth/request-admin")
def auth_request_admin(payload: AuthPayload, request: Request) -> dict[str, str]:
    return _create_pending_user(payload.email, payload.password, "admin", request)


@app.post("/api/auth/login")
def auth_login(payload: AuthPayload, response: Response) -> dict[str, str]:
    ensure_tables()
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, password_hash, role, approval_status, can_access_catalog, hidden_tabs FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if not row or not _verify_password(payload.password, str(row["password_hash"])):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if str(row["approval_status"] or "pending").lower() != "approved":
        raise HTTPException(status_code=403, detail="Account approval is pending")

    raw_token = secrets.token_urlsafe(48)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    expires_at = now_ts + SESSION_TTL_SECONDS
    with get_db_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (int(row["id"]),))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(raw_token), int(row["id"]), expires_at, _now_iso()),
        )
        connection.commit()
    _set_session_cookie(response, raw_token)
    _send_owner_sms(f"Droid Cloud login: {email} logged in as {str(row['role'] or 'user')}.")
    return {"status": "success", "email": email}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    raw = _unsign_session_token(signed) if signed else None
    if raw:
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ?",
                (_token_hash(raw),),
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(raw),))
            if row:
                connection.execute(
                    """
                    INSERT INTO activity_logs (user_id, ip_address, method, endpoint, device_label, accessed_at)
                    VALUES (?, ?, 'LOGOUT', '/api/auth/logout', ?, ?)
                    """,
                    (
                        int(row["user_id"]),
                        request.client.host if request.client else "unknown",
                        request.headers.get("x-droid-device", "").strip()[:160],
                        _now_iso(),
                    ),
                )
            connection.commit()
    _clear_session_cookie(response)
    return {"status": "success"}


@app.get("/api/approvals/approve")
def approve_access_request(token: str) -> Response:
    token_hash = _token_hash(token)
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT id, email, requested_role
            FROM users
            WHERE approval_token_hash = ? AND approval_status = 'pending'
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return Response(
                "Approval link is invalid or this request was already handled.",
                media_type="text/plain",
                status_code=404,
            )
        role = "admin" if str(row["requested_role"]).lower() == "admin" else "user"
        approved_at = _now_iso()
        connection.execute(
            """
            UPDATE users
            SET approval_status = 'approved',
                role = ?,
                approved_at = ?,
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (role, approved_at, int(row["id"])),
        )
        connection.commit()
    user_email = str(row["email"])
    _send_email(
        user_email,
        "Droid Cloud access approved",
        (
            "Your Droid Cloud access has been approved.\n\n"
            f"Approved role: {role}\n"
            f"You can now login here: {PUBLIC_PORTAL_URL}\n\n"
            "You are approved for this role and can manage data according to your permissions."
        ),
    )
    _send_owner_sms(f"Droid Cloud approved: {user_email} is now {role}.")
    return Response(
        f"Approved {user_email} as {role}. The user has been notified.",
        media_type="text/plain",
    )


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, object]:
    ensure_tables()
    user = _require_user(request)
    return {
        "id": int(user["id"]),
        "email": str(user["email"]),
        "role": str(user.get("role", "user")),
        "can_access_catalog": bool(user.get("can_access_catalog", True)),
        "hidden_tabs": _normalize_hidden_tabs(user.get("hidden_tabs", [])),
        "approval_status": str(user.get("approval_status", "approved")),
    }


@app.get("/api/admin/users/activity")
def admin_user_activity(
    request: Request,
    admin_user: dict[str, object] = Depends(verify_admin),
) -> dict[str, list[dict[str, object]]]:
    now = datetime.now(timezone.utc)
    active_cutoff = now.timestamp() - 15 * 60
    with get_db_connection() as connection:
        users = connection.execute(
            """
            SELECT id, email, role, requested_role, approval_status, created_at, can_access_catalog, hidden_tabs
            FROM users
            ORDER BY created_at ASC
            """
        ).fetchall()
        activity_rows = connection.execute(
            """
            SELECT user_id, ip_address, method, endpoint, device_label,
                   latitude, longitude, location_accuracy, accessed_at
            FROM activity_logs
            WHERE user_id IS NOT NULL
            ORDER BY accessed_at DESC
            """
        ).fetchall()

    by_user: dict[int, list[sqlite3.Row]] = {}
    for row in activity_rows:
        by_user.setdefault(int(row["user_id"]), []).append(row)

    result: list[dict[str, object]] = []
    for user_row in users:
        user_id = int(user_row["id"])
        rows = by_user.get(user_id, [])
        latest = rows[0] if rows else None
        last_seen = str(latest["accessed_at"]) if latest else ""
        last_seen_ts = 0.0
        if last_seen:
            try:
                last_seen_ts = datetime.fromisoformat(last_seen).timestamp()
            except ValueError:
                last_seen_ts = 0.0
        result.append(
            {
                "user_id": user_id,
                "email": str(user_row["email"]),
                "role": str(user_row["role"] or "user"),
                "requested_role": str(user_row["requested_role"] or user_row["role"] or "user"),
                "can_access_catalog": bool(user_row["can_access_catalog"]),
                "hidden_tabs": _normalize_hidden_tabs(user_row["hidden_tabs"]),
                "approval_status": str(user_row["approval_status"] or "pending"),
                "status": (
                    "Offline"
                    if latest and str(latest["method"]).upper() == "LOGOUT"
                    else "Active" if last_seen_ts >= active_cutoff else "Offline"
                ),
                "current_ip": str(latest["ip_address"]) if latest else "",
                "device_label": str(latest["device_label"] or "") if latest else "",
                "location": (
                    f"{float(latest['latitude']):.5f}, {float(latest['longitude']):.5f}"
                    if latest and latest["latitude"] is not None and latest["longitude"] is not None
                    else ""
                ),
                "location_accuracy_m": (
                    int(float(latest["location_accuracy"]))
                    if latest and latest["location_accuracy"] is not None
                    else 0
                ),
                "unique_ip_count": len({str(row["ip_address"]) for row in rows}),
                "last_accessed_data": (
                    f"{latest['method']} {latest['endpoint']}" if latest else ""
                ),
                "last_seen_at": last_seen,
            },
        )
    return {"users": result}


@app.get("/api/admin/users/{user_id}/projects")
def admin_user_projects(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, list[ProjectOut]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, location, date, status, type
            FROM projects
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return {
        "projects": [
            ProjectOut(
                id=str(row["id"]),
                name=str(row["name"]),
                location=str(row["location"]),
                date=str(row["date"]),
                status=str(row["status"]),
                type=str(row["type"]),
            )
            for row in rows
        ],
    }


@app.post("/api/admin/users/{user_id}/approve")
def admin_approve_user(
    user_id: int,
    payload: AdminUserApprovalPayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    role = "admin" if payload.role.strip().lower() == "admin" else "user"
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET role = ?,
                requested_role = ?,
                approval_status = 'approved',
                approved_at = ?,
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (role, role, _now_iso(), user_id),
        )
        connection.commit()
    _send_email(
        str(row["email"]),
        "Droid Cloud access approved",
        (
            f"Your Droid Cloud {role} access has been approved.\n\n"
            f"You can now login here: {PUBLIC_PORTAL_URL}\n"
        ),
    )
    return {"status": "success"}


@app.patch("/api/admin/users/{user_id}/role")
def admin_assign_user_role(
    user_id: int,
    payload: AdminUserRolePayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    role = "admin" if payload.role.strip().lower() == "admin" else "user"
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET role = ?,
                requested_role = ?,
                approval_status = 'approved',
                approved_at = COALESCE(approved_at, ?)
            WHERE id = ?
            """,
            (role, role, _now_iso(), user_id),
        )
        connection.commit()
    _send_email(
        str(row["email"]),
        "Droid Cloud role updated",
        f"Your Droid Cloud role is now: {role}.\n\nLogin: {PUBLIC_PORTAL_URL}\n",
    )
    return {"status": "success", "role": role}


@app.patch("/api/admin/users/{user_id}/catalog-access")
def admin_set_user_catalog_access(
    user_id: int,
    payload: dict[str, bool],
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    enabled = bool(payload.get("enabled", True))
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET can_access_catalog = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        connection.commit()
    return {"status": "success", "user_id": user_id, "can_access_catalog": enabled}


@app.patch("/api/admin/users/{user_id}/hidden-tabs")
def admin_set_user_hidden_tabs(
    user_id: int,
    payload: AdminUserHiddenTabsPayload,
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    hidden_tabs = _normalize_hidden_tabs(payload.hidden_tabs)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET hidden_tabs = ? WHERE id = ?",
            (json.dumps(hidden_tabs), user_id),
        )
        connection.commit()
    return {"status": "success", "user_id": user_id, "hidden_tabs": hidden_tabs}


@app.post("/api/admin/users/{user_id}/disapprove")
def admin_disapprove_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET approval_status = 'rejected',
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (user_id,),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.commit()
    _send_email(
        str(row["email"]),
        "Droid Cloud access request update",
        "Your Droid Cloud access request was not approved. Contact the owner for more details.",
    )
    return {"status": "success"}


@app.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        try:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        except sqlite3.IntegrityError:
            connection.execute(
                """
                UPDATE users
                SET email = ?,
                    role = 'user',
                    requested_role = 'user',
                    approval_status = 'deleted',
                    approval_token_hash = NULL
                WHERE id = ?
                """,
                (f"deleted-user-{user_id}@local.invalid", user_id),
            )
        connection.commit()
    return {"status": "success"}


@app.delete("/api/admin/users/{user_id}/advanced")
def admin_advanced_delete_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str | int]:
    with get_db_connection() as connection:
        user_row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        project_rows = connection.execute(
            "SELECT id FROM projects WHERE owner_user_id = ?",
            (user_id,),
        ).fetchall()
        project_ids = [str(row["id"]) for row in project_rows]

    local_root = Path(LOCAL_DATA_PATH).resolve()
    for project_id in project_ids:
        safe_project_id = _safe_project_id(project_id)
        for target in (
            local_root / "projects" / safe_project_id,
            local_root / "datasets" / safe_project_id,
            local_root / "pointclouds" / safe_project_id,
        ):
            resolved = target.resolve()
            if resolved.exists() and local_root in resolved.parents:
                shutil.rmtree(resolved, ignore_errors=True)

    with get_db_connection() as connection:
        for project_id in project_ids:
            connection.execute("DELETE FROM camera_views WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM dataset_crop_masks WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            _invalidate_project_files_cache(project_id)
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM activity_logs WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()
    return {"status": "success", "deleted_projects": len(project_ids)}


@app.get("/api/admin/override/project/{project_id}")
def admin_get_project_override(
    project_id: str,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, object]:
    safe_project_id = _safe_project_id(project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT p.id, p.name, p.location, p.date, p.status, p.type,
                   u.id AS owner_user_id, u.email AS owner_email
            FROM projects p
            JOIN users u ON u.id = p.owner_user_id
            WHERE p.id = ?
            """,
            (safe_project_id,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return {
        "project": {
            "id": str(row["id"]),
            "name": str(row["name"]),
            "location": str(row["location"]),
            "date": str(row["date"]),
            "status": str(row["status"]),
            "type": str(row["type"]),
            "owner_user_id": int(row["owner_user_id"]),
            "owner_email": str(row["owner_email"]),
        },
    }


@app.patch("/api/admin/override/project/{project_id}")
def admin_patch_project_override(
    project_id: str,
    payload: AdminProjectPatchPayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, object]:
    safe_project_id = _safe_project_id(project_id)
    updates: dict[str, str] = {}
    for key in ("name", "location", "date", "status", "type"):
        value = getattr(payload, key)
        if value is not None:
            updates[key] = value.strip()
    if not updates:
        return admin_get_project_override(safe_project_id, request, admin_user)
    assignments = ", ".join(f"{key} = ?" for key in updates)
    values = [*updates.values(), safe_project_id]
    with get_db_connection() as connection:
        cursor = connection.execute(
            f"UPDATE projects SET {assignments} WHERE id = ?",
            values,
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Project not found")
    return admin_get_project_override(safe_project_id, request)


@app.get("/api/projects")
def get_projects(request: Request) -> dict[str, list[ProjectOut]]:
    user = _require_user(request)
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, location, date, status, type
            FROM projects
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            """,
            (int(user["id"]),),
        ).fetchall()
    projects = [
        ProjectOut(
            id=str(row["id"]),
            name=str(row["name"]),
            location=str(row["location"]),
            date=str(row["date"]),
            status=str(row["status"]),
            type=str(row["type"]),
        )
        for row in rows
    ]
    return {"projects": projects}


@app.post("/api/projects")
def create_project(payload: ProjectCreatePayload, request: Request) -> ProjectOut:
    user = _require_user(request)
    project_id = f"proj_{secrets.token_hex(8)}"
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO projects (id, owner_user_id, name, location, date, status, type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                int(user["id"]),
                payload.name.strip(),
                payload.location.strip(),
                payload.date.strip(),
                payload.status.strip(),
                payload.type.strip(),
                now,
            ),
        )
        connection.commit()
    return ProjectOut(
        id=project_id,
        name=payload.name.strip(),
        location=payload.location.strip(),
        date=payload.date.strip(),
        status=payload.status.strip(),
        type=payload.type.strip(),
    )


@app.patch("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdatePayload, request: Request) -> ProjectOut:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE projects SET name = ? WHERE id = ? AND owner_user_id = ?",
            (name, safe_project_id, int(user["id"])),
        )
        row = connection.execute(
            "SELECT id, name, location, date, status, type FROM projects WHERE id = ? AND owner_user_id = ?",
            (safe_project_id, int(user["id"])),
        ).fetchone()
        connection.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectOut(
        id=str(row["id"]),
        name=str(row["name"]),
        location=str(row["location"]),
        date=str(row["date"]),
        status=str(row["status"]),
        type=str(row["type"]),
    )


@app.get("/api/projects/{project_id}/camera-views")
def get_camera_views(project_id: str, request: Request) -> dict[str, list[dict[str, str | float]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, lat, lng, height, heading, pitch, roll, created_at, updated_at
            FROM camera_views
            WHERE project_id = ? AND owner_user_id = ?
            ORDER BY updated_at DESC
            """,
            (safe_project_id, int(user["id"])),
        ).fetchall()
    return {
        "views": [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
                "height": float(row["height"]),
                "heading": float(row["heading"]),
                "pitch": float(row["pitch"]),
                "roll": float(row["roll"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ],
    }


@app.post("/api/projects/{project_id}/camera-views")
def save_camera_view(project_id: str, payload: CameraViewPayload, request: Request) -> dict[str, str | float]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    name = payload.name.strip()[:80]
    if not name:
        raise HTTPException(status_code=400, detail="Camera view name is required")
    if not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid camera location")
    view_id = _safe_dataset_id(f"cam-{secrets.token_hex(8)}")
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO camera_views
                (id, project_id, owner_user_id, name, lat, lng, height, heading, pitch, roll, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                view_id,
                safe_project_id,
                int(user["id"]),
                name,
                float(payload.lat),
                float(payload.lng),
                float(payload.height),
                float(payload.heading),
                float(payload.pitch),
                float(payload.roll),
                now,
                now,
            ),
        )
        connection.commit()
    return {
        "id": view_id,
        "name": name,
        "lat": float(payload.lat),
        "lng": float(payload.lng),
        "height": float(payload.height),
        "heading": float(payload.heading),
        "pitch": float(payload.pitch),
        "roll": float(payload.roll),
        "created_at": now,
        "updated_at": now,
    }


@app.delete("/api/projects/{project_id}/camera-views/{view_id}")
def delete_camera_view(project_id: str, view_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_view_id = _safe_dataset_id(view_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM camera_views WHERE id = ? AND project_id = ? AND owner_user_id = ?",
            (safe_view_id, safe_project_id, int(user["id"])),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Camera view not found")
    return {"status": "success"}


@app.get("/api/project-stats")
def project_stats() -> dict[str, list]:
    return {
        "catchment_stats": CATCHMENT_STATS,
        "stream_stats": STREAM_STATS,
        "lulc_rows": LULC_ROWS,
    }


@app.get("/api/survair-stats")
def survair_stats() -> dict[str, list]:
    return {
        "catchment_stats": CATCHMENT_STATS,
        "stream_stats": STREAM_STATS,
        "lulc_rows": LULC_ROWS,
    }


@app.get("/api/media")
def media(request: Request) -> dict[str, list[dict[str, str]]]:
    media_dir = Path(LOCAL_DATA_PATH) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, str]] = []
    base_url = str(request.base_url).rstrip("/")
    for file_path in sorted(media_dir.iterdir(), key=lambda p: p.name.lower()):
        if not file_path.is_file():
            continue

        extension = file_path.suffix.lower()
        if extension in IMAGE_EXTENSIONS:
            media_type = "image"
        elif extension in VIDEO_EXTENSIONS:
            media_type = "video"
        else:
            continue

        files.append(
            {
                "filename": file_path.name,
                "type": media_type,
                "url": f"{base_url}/tiles/media/{file_path.name}",
            }
        )

    return {"media": files}


@app.get("/api/issues")
def get_issues() -> list[Issue]:
    with get_db_connection() as connection:
        rows = connection.execute(
            "SELECT id, lat, lng, title, description, status FROM issues ORDER BY id ASC"
        ).fetchall()

    return [
        Issue(
            id=row["id"],
            lat=row["lat"],
            lng=row["lng"],
            title=row["title"],
            description=row["description"],
            status=row["status"],
        )
        for row in rows
    ]


@app.post("/api/issues")
def create_issue(issue: IssuePayload) -> Issue:
    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO issues (lat, lng, title, description, status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (issue.lat, issue.lng, issue.title, issue.description, issue.status),
        )
        connection.commit()
        issue_id = cursor.lastrowid

    return Issue(
        id=issue_id,
        lat=issue.lat,
        lng=issue.lng,
        title=issue.title,
        description=issue.description,
        status=issue.status,
    )


@app.get("/api/projects/{project_id}/spatial-layers")
def get_spatial_layers(project_id: str, request: Request) -> dict[str, list[dict[str, object]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        layer_rows = connection.execute(
            """
            SELECT id, project_id, name, source_type, created_at, updated_at
            FROM spatial_layers
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (safe_project_id,),
        ).fetchall()
        feature_rows = connection.execute(
            """
            SELECT *
            FROM spatial_features
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (safe_project_id,),
        ).fetchall()

    features_by_layer: dict[str, list[dict[str, object]]] = {}
    for row in feature_rows:
        feature = _spatial_row_to_dict(row)
        features_by_layer.setdefault(str(feature["layer_id"]), []).append(feature)

    layers: list[dict[str, object]] = []
    for row in layer_rows:
        layer_id = str(row["id"])
        layers.append(
            {
                "id": layer_id,
                "project_id": str(row["project_id"]),
                "name": str(row["name"]),
                "source_type": str(row["source_type"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "features": features_by_layer.get(layer_id, []),
            },
        )
    return {"layers": layers}


@app.post("/api/projects/{project_id}/spatial-features")
def create_spatial_feature(
    project_id: str,
    payload: SpatialFeaturePayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    source_type = (payload.source_type or "drawn").strip()[:40] or "drawn"
    with get_db_connection() as connection:
        layer_id = _ensure_spatial_layer(
            connection,
            safe_project_id,
            int(user["id"]),
            payload.layer_id,
            payload.layer_name,
            source_type,
        )
        feature = _insert_spatial_feature(
            connection,
            safe_project_id,
            int(user["id"]),
            layer_id,
            payload.geojson,
            (payload.plot_id or "").strip(),
            (payload.owner_name or "").strip(),
            payload.structure_type,
            source_type,
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), layer_id),
        )
        connection.commit()
    return {"feature": feature}


@app.put("/api/projects/{project_id}/spatial-features/{feature_id}")
def update_spatial_feature(
    project_id: str,
    feature_id: str,
    payload: SpatialFeaturePatchPayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_feature_id = _safe_spatial_id(feature_id, "feature_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial feature not found")

        current = _spatial_row_to_dict(row)
        geojson_data = payload.geojson if payload.geojson is not None else current["geojson"]
        feature, geometry_type = _normalize_spatial_feature_geojson(geojson_data)
        plot_id = (payload.plot_id if payload.plot_id is not None else str(current["plot_id"])).strip()
        owner_name = (payload.owner_name if payload.owner_name is not None else str(current["owner_name"])).strip()
        structure_type = normalize_structure_type(
            payload.structure_type if payload.structure_type is not None else str(current["structure_type"]),
        )
        colors = style_for_structure(structure_type)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        properties.update(
            {
                "plotId": plot_id,
                "ownerName": owner_name,
                "structureType": structure_type,
            },
        )
        feature["properties"] = properties
        now = _now_iso()
        connection.execute(
            """
            UPDATE spatial_features
            SET geometry_type = ?, geojson = ?, plot_id = ?, owner_name = ?,
                structure_type = ?, fill_color = ?, stroke_color = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                geometry_type,
                json.dumps(feature, ensure_ascii=True),
                plot_id[:120],
                owner_name[:180],
                structure_type,
                colors["fill_color"],
                colors["stroke_color"],
                now,
                safe_feature_id,
                safe_project_id,
            ),
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (now, str(row["layer_id"])),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM spatial_features WHERE id = ?",
            (safe_feature_id,),
        ).fetchone()
    return {"feature": _spatial_row_to_dict(updated)}


@app.delete("/api/projects/{project_id}/spatial-features/{feature_id}")
def delete_spatial_feature(project_id: str, feature_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_feature_id = _safe_spatial_id(feature_id, "feature_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT layer_id FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial feature not found")
        connection.execute(
            "DELETE FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), str(row["layer_id"])),
        )
        connection.commit()
    return {"status": "success"}


@app.delete("/api/projects/{project_id}/spatial-layers/{layer_id}")
def delete_spatial_layer(project_id: str, layer_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_layer_id = _safe_spatial_id(layer_id, "layer_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial layer not found")
        connection.execute(
            "DELETE FROM spatial_features WHERE layer_id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        )
        connection.execute(
            "DELETE FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        )
        connection.commit()
    return {"status": "success"}


@app.post("/api/projects/{project_id}/spatial-import")
async def import_spatial_layer(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = os.path.basename((file.filename or "").strip())
    if not safe_name or safe_name in {".", ".."} or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".kml", ".xml", ".geojson", ".json", ".shp", ".zip"}:
        raise HTTPException(status_code=400, detail="Only .kml, .geojson, .shp, or zipped shapefiles are supported")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / safe_name
        try:
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to store import: {exc}") from exc
        finally:
            await file.close()

        text = None
        if suffix in {".kml", ".xml", ".geojson", ".json"}:
            text = tmp_path.read_text(encoding="utf-8", errors="ignore")
        features = parse_spatial_upload(tmp_path, suffix, text)

    source_type = "imported-shp" if suffix in {".shp", ".zip"} else "imported-kml" if suffix in {".kml", ".xml"} else "imported-geojson"
    layer_name = Path(safe_name).stem[:180] or "Imported Layer"
    with get_db_connection() as connection:
        layer_id = _ensure_spatial_layer(
            connection,
            safe_project_id,
            int(user["id"]),
            "",
            layer_name,
            source_type,
        )
        inserted = [
            _insert_spatial_feature(
                connection,
                safe_project_id,
                int(user["id"]),
                layer_id,
                feature,
                "",
                "",
                "Unassigned",
                source_type,
            )
            for feature in features
        ]
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), layer_id),
        )
        connection.commit()

    return {
        "layer": {
            "id": layer_id,
            "project_id": safe_project_id,
            "name": layer_name,
            "source_type": source_type,
            "features": inserted,
        },
        "imported_count": len(inserted),
    }


@app.post("/api/run-flood-engine")
async def run_flood_engine() -> dict[str, str]:
    await asyncio.sleep(3)

    flood_root = Path(LOCAL_DATA_PATH) / "flood"
    periods = ("1in25", "1in50", "1in100")

    for period in periods:
        tile_dir = flood_root / period / "0" / "0"
        os.makedirs(tile_dir, exist_ok=True)
        tile_image = Image.new("RGBA", (256, 256), (14, 62, 73, 110))
        tile_image.save(tile_dir / "0.png", format="PNG")

    return {
        "status": "success",
        "message": (
            "Flood simulation completed for 25, 50, and 100 year return periods."
        ),
    }


@app.post("/api/process-pointcloud")
async def process_pointcloud_request(payload: PointCloudProcessPayload, request: Request) -> dict[str, str]:
    user = verify_admin(request)
    _ensure_project_owner(int(user["id"]), _safe_project_id(payload.project_id))
    # Simulate running py3dtiles conversion:
    # input .las/.laz -> output directory containing tileset.json and .pnts files.
    await asyncio.sleep(5)

    output_dir = Path(LOCAL_DATA_PATH) / "pointclouds" / payload.project_id
    output_dir.mkdir(parents=True, exist_ok=True)

    # Mock output so frontend gets a resolvable tileset URL in development.
    tileset_path = output_dir / "tileset.json"
    if not tileset_path.exists():
        tileset_path.write_text('{"asset":{"version":"1.0"}}', encoding="utf-8")

    tileset_url = (
        f"{str(request.base_url).rstrip('/')}/data/pointclouds/"
        f"{payload.project_id}/tileset.json"
    )

    return {
        "status": "success",
        "message": f"Point cloud processed for {payload.filename}.",
        "tileset_url": tileset_url,
    }


@app.post("/api/upload-chunk")
async def upload_chunk(
    request: Request,
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    project_id: str = Form(...),
    chunkIndex: int = Form(...),
    totalChunks: int = Form(...),
) -> dict[str, str]:
    """
    Accept one binary chunk of a larger LAS/LAZ upload.
    Chunks are written to a temp folder under LOCAL_DATA_PATH/uploads/chunks/.
    """
    user = verify_admin(request)
    _enforce_rate_limit(request, "upload")
    safe_project = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project)
    safe_name = _safe_pointcloud_basename(filename)
    if totalChunks < 1 or totalChunks > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")
    if chunkIndex < 0 or chunkIndex >= totalChunks:
        raise HTTPException(status_code=400, detail="Invalid chunkIndex")

    session_dir = _upload_session_dir(safe_name, totalChunks, safe_project)
    session_dir.mkdir(parents=True, exist_ok=True)

    existing_size = sum(
        p.stat().st_size for p in session_dir.glob("*.part") if p.is_file()
    )
    # Worst case this chunk is up to ~10MB+ (frontend slice size); reserve generously.
    max_chunk_estimate = 12 * 1024 * 1024
    _ensure_disk_space_for_bytes(
        Path(LOCAL_DATA_PATH),
        existing_size + max_chunk_estimate,
    )

    part_path = session_dir / f"{chunkIndex:08d}.part"
    # Stream body to disk (do not load entire chunk into memory at once).
    def write_part() -> None:
        with open(part_path, "wb") as dest:
            shutil.copyfileobj(chunk.file, dest, length=MERGE_COPY_BUFFER_BYTES)

    await run_in_threadpool(write_part)
    await chunk.close()

    return {
        "status": "success",
        "message": f"Stored chunk {chunkIndex + 1}/{totalChunks} for {safe_name}",
        "chunkIndex": str(chunkIndex),
    }


@app.post("/api/complete-upload")
async def complete_upload(
    payload: CompleteUploadPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Merge chunk files in order into a single LAS/LAZ under projects/<id>/raw/.
    Uses streaming copy + per-chunk delete to limit peak disk and avoid RAM spikes.
    """
    user = verify_admin(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(payload.project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_pointcloud_basename(payload.filename)
    total = payload.totalChunks
    if total < 1 or total > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")

    session_dir = _upload_session_dir(safe_name, total, safe_project_id)
    if not session_dir.is_dir():
        raise HTTPException(
            status_code=400, detail="No chunks found for this upload session",
        )

    part_paths: list[Path] = []
    total_bytes = 0
    for i in range(total):
        part = session_dir / f"{i:08d}.part"
        if not part.is_file():
            raise HTTPException(
                status_code=400, detail=f"Missing chunk file for index {i}",
            )
        total_bytes += part.stat().st_size
        part_paths.append(part)

    raw_dir, _ = get_project_dirs(safe_project_id)
    out_path = raw_dir / f"{safe_project_id}__{safe_name}"

    # Large-file safety: ensure enough free space for merged output (+ headroom).
    _ensure_disk_space_for_bytes(raw_dir, total_bytes)

    file_digest = hashlib.sha256()
    try:
        with open(out_path, "wb") as out_f:
            for part in part_paths:
                with open(part, "rb") as in_f:
                    while True:
                        chunk_data = in_f.read(MERGE_COPY_BUFFER_BYTES)
                        if not chunk_data:
                            break
                        out_f.write(chunk_data)
                        file_digest.update(chunk_data)
                part.unlink(missing_ok=True)
    except OSError as exc:
        if out_path.exists():
            out_path.unlink(missing_ok=True)
        raise HTTPException(
            status_code=500, detail=f"Merge failed: {exc}",
        ) from exc

    try:
        session_dir.rmdir()
    except OSError:
        pass

    content_hash = file_digest.hexdigest()
    cache = _read_conversion_cache()
    user_cache_key = f"{int(user['id'])}:{safe_project_id}:{content_hash}"
    reused_tileset_id = cache.get(user_cache_key)
    if reused_tileset_id:
        reused_tileset_id = _safe_tileset_id(reused_tileset_id)
    else:
        stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "cloud"
        reused_tileset_id = f"{stem[:40]}-{content_hash[:12]}"
    potree_dataset_name = _potree_dataset_name(reused_tileset_id)
    output_dir = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "processed" / potree_dataset_name
    final_path = out_path
    hash_marker = output_dir / ".source_hash.txt"
    existing_hash = None
    try:
        if hash_marker.is_file():
            existing_hash = hash_marker.read_text(encoding="utf-8").strip()
    except OSError:
        existing_hash = None

    potree_html = output_dir / f"{potree_dataset_name}.html"
    if potree_html.is_file() and existing_hash == content_hash:
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": potree_dataset_name,
                "kind": "pointcloud",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/projects/{safe_project_id}/processed/{potree_dataset_name}/{potree_dataset_name}.html",
            },
        )
        return {
            "status": "success",
            "message": (
                f"Merged {total} chunks into {safe_name}. "
                "Found existing Droid point cloud viewer for same file content; reusing project viewer."
            ),
            "tileset_url": "PENDING",
            "project_id": safe_project_id,
            "target_tileset_url": _potree_html_url(str(request.base_url), safe_project_id, potree_dataset_name),
            "tileset_id": potree_dataset_name,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        hash_marker.write_text(content_hash, encoding="utf-8")
    except OSError:
        pass
    cache[user_cache_key] = reused_tileset_id
    _write_conversion_cache(cache)
    _upsert_processing_job(
        safe_project_id,
            {
                "job_id": potree_dataset_name,
                "kind": "pointcloud",
                "file_name": safe_name,
                "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_pointcloud_potree_job,
        str(final_path),
        str(output_dir),
        potree_dataset_name,
        safe_project_id,
        potree_dataset_name,
        safe_name,
        content_hash,
    )

    return {
        "status": "success",
        "message": "File merged. Droid 3D point cloud processing started in background.",
        "tileset_url": "PENDING",
        "project_id": safe_project_id,
        "target_tileset_url": _potree_html_url(str(request.base_url), safe_project_id, potree_dataset_name),
        "tileset_id": potree_dataset_name,
    }


@app.post("/api/upload-dataset-chunk")
async def upload_dataset_chunk(
    request: Request,
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    project_id: str = Form(...),
    chunkIndex: int = Form(...),
    totalChunks: int = Form(...),
) -> dict[str, str]:
    user = verify_admin(request)
    safe_project = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project)
    safe_name = _safe_dataset_upload_basename(filename)
    if Path(safe_name).suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Chunked dataset upload is available for .tif/.tiff raster files.")
    if totalChunks < 1 or totalChunks > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")
    if chunkIndex < 0 or chunkIndex >= totalChunks:
        raise HTTPException(status_code=400, detail="Invalid chunkIndex")

    session_dir = _dataset_upload_session_dir(safe_name, totalChunks, safe_project)
    session_dir.mkdir(parents=True, exist_ok=True)
    part_path = session_dir / f"{chunkIndex:08d}.part"
    def write_part() -> None:
        with open(part_path, "wb") as dest:
            shutil.copyfileobj(chunk.file, dest, length=MERGE_COPY_BUFFER_BYTES)

    await run_in_threadpool(write_part)
    await chunk.close()
    return {
        "status": "success",
        "message": f"Stored dataset chunk {chunkIndex + 1}/{totalChunks} for {safe_name}",
        "chunkIndex": str(chunkIndex),
    }


@app.post("/api/complete-dataset-upload", response_model=ProcessDatasetOut)
async def complete_dataset_upload(
    payload: CompleteDatasetUploadPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ProcessDatasetOut:
    user = verify_admin(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(payload.project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(payload.filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Chunked dataset upload is available for .tif/.tiff raster files.")
    total = payload.totalChunks
    if total < 1 or total > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")

    session_dir = _dataset_upload_session_dir(safe_name, total, safe_project_id)
    if not session_dir.is_dir():
        raise HTTPException(status_code=400, detail="No chunks found for this dataset upload session")

    part_paths: list[Path] = []
    total_bytes = 0
    for i in range(total):
        part = session_dir / f"{i:08d}.part"
        if not part.is_file():
            raise HTTPException(status_code=400, detail=f"Missing chunk file for index {i}")
        total_bytes += part.stat().st_size
        part_paths.append(part)

    normalized_type = _normalize_dataset_type(payload.dataset_type, safe_name)
    if normalized_type == "3dmodel":
        normalized_type = _infer_dataset_type(safe_name)
        if normalized_type == "3dmodel":
            normalized_type = "ortho"

    submitted_date = (payload.created_at or "").strip()
    submitted_month = (payload.month or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_month):
        submitted_date = submitted_date or submitted_month
        submitted_month = submitted_month[:7]
    ddmmyyyy_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", submitted_month)
    if ddmmyyyy_match:
        day, month_part, year = ddmmyyyy_match.groups()
        submitted_date = submitted_date or f"{year}-{int(month_part):02d}-{int(day):02d}"
        submitted_month = submitted_date[:7]
    normalized_month = _normalize_month(submitted_month)
    manual_epsg = _normalize_epsg_input(getattr(payload, "epsg", ""))

    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")
    raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
    meta_dir = _dataset_dir(safe_project_id, dataset_id)
    meta_dir.mkdir(parents=True, exist_ok=True)
    input_path = raw_dir / f"{tile_output_folder}{ext}"
    output_tile_dir = processed_dir / tile_output_folder
    output_tile_dir.mkdir(parents=True, exist_ok=True)

    cog_headroom = max(
        2 * 1024 * 1024 * 1024,
        min(total_bytes // 5, 20 * 1024 * 1024 * 1024),
    )
    _ensure_disk_space_for_bytes(raw_dir, cog_headroom)
    try:
        with open(input_path, "wb") as out_f:
            for part in part_paths:
                with open(part, "rb") as in_f:
                    shutil.copyfileobj(in_f, out_f, length=MERGE_COPY_BUFFER_BYTES)
                part.unlink(missing_ok=True)
    except OSError as exc:
        input_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"Dataset merge failed: {exc}") from exc

    try:
        session_dir.rmdir()
    except OSError:
        pass

    raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
    pending_cog_path = (output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()
    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Uploading",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": safe_name,
            "tile_folder": tile_output_folder,
            "dataset_type": normalized_type,
            "layer_type": _raster_layer_type(normalized_type, safe_name),
            "month": normalized_month,
            "created_at": submitted_date,
            "raw_rel_path": raw_rel,
            "processed_size_bytes": str(total_bytes),
            "processed_size": _format_size_bytes(total_bytes),
            "cog_path": str(pending_cog_path),
            "manual_epsg": manual_epsg,
            "applied_epsg": "",
        },
    )
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": dataset_id,
            "kind": "dataset",
            "file_name": safe_name,
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_dataset_background,
        safe_project_id,
        dataset_id,
        str(input_path),
        safe_name,
        str(output_tile_dir),
        tile_output_folder,
        manual_epsg,
    )
    return ProcessDatasetOut(
        status="success",
        message="Large dataset merged. COG conversion started in background.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=safe_name,
        cog_path=str(pending_cog_path),
        cog_tile_url_template=_titiler_tile_url_template(
            str(request.base_url),
            str(pending_cog_path),
            _raster_layer_type(normalized_type, safe_name),
        ),
    )


@app.post("/api/process-dataset", response_model=ProcessDatasetOut)
async def process_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    dataset_type: str = Form(""),
    month: str = Form(""),
    created_at: str = Form(""),
    epsg: str = Form(""),
) -> ProcessDatasetOut:
    user = verify_admin(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(file.filename or "")
    ext = Path(safe_name).suffix.lower()
    if ext not in (".tif", ".tiff", ".csv", ".zip", ".kml", ".geojson", ".dwg", ".pdf"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.csv/.zip/.kml/.geojson/.dwg/.pdf dataset files are supported")
    normalized_type = _normalize_dataset_type(dataset_type, safe_name)
    if ext in (".tif", ".tiff") and normalized_type == "3dmodel":
        normalized_type = _infer_dataset_type(safe_name)
        if normalized_type == "3dmodel":
            normalized_type = "ortho"
    if ext == ".zip" and normalized_type != "3dmodel":
        raise HTTPException(
            status_code=400,
            detail=(
                "ZIP uploads are supported only for 3D Model tilesets. "
                "Upload DTM, DSM, and Ortho datasets as .tif or .tiff files."
            ),
        )
    submitted_date = (created_at or "").strip()
    submitted_month = (month or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_month):
        submitted_date = submitted_date or submitted_month
        submitted_month = submitted_month[:7]
    ddmmyyyy_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", submitted_month)
    if ddmmyyyy_match:
        day, month_part, year = ddmmyyyy_match.groups()
        submitted_date = submitted_date or f"{year}-{int(month_part):02d}-{int(day):02d}"
        submitted_month = submitted_date[:7]
    try:
        normalized_month = _normalize_month(submitted_month)
    except HTTPException as exc:
        if exc.status_code == 400 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_month):
            submitted_date = submitted_date or submitted_month
            normalized_month = submitted_month[:7]
        else:
            raise

    manual_epsg = _normalize_epsg_input(locals().get("epsg", "")) if locals().get("ext", "") in (".tif", ".tiff") else ""

    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")

    raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
    meta_dir = _dataset_dir(safe_project_id, dataset_id)
    meta_dir.mkdir(parents=True, exist_ok=True)

    input_path = raw_dir / f"{tile_output_folder}{ext}"

    content_length = request.headers.get("content-length")
    expected_bytes = int(content_length) if content_length and content_length.isdigit() else 0
    if ext in (".tif", ".tiff") and expected_bytes > DIRECT_RASTER_UPLOAD_LIMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Large raster upload must use chunked upload. "
                "Refresh the portal and try again, or use Sync Manual Folders for very large local files."
            ),
        )
    _ensure_disk_space_for_bytes(raw_dir, max(expected_bytes * 2, 512 * 1024 * 1024))

    output_tile_dir = processed_dir / tile_output_folder
    if ext not in (".csv", ".pdf"):
        output_tile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(input_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store dataset: {exc}") from exc
    finally:
        await file.close()

    raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
    if ext == ".pdf":
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": "reports",
                "layer_type": "Reports",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "report_rel_path": raw_rel,
                "processed_size_bytes": str(input_path.stat().st_size),
                "processed_size": _format_size_bytes(input_path.stat().st_size),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "report",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{raw_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="PDF report uploaded and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{raw_rel}",
        )

    if ext in (".kml", ".geojson", ".dwg"):
        asset_type = "cad" if ext == ".dwg" else "vector"
        asset_root = processed_dir / tile_output_folder
        asset_root.mkdir(parents=True, exist_ok=True)
        asset_path = asset_root / safe_name
        try:
            shutil.copyfile(input_path, asset_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to prepare vector asset: {exc}") from exc
        asset_rel = asset_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        asset_size_bytes = calculate_folder_size(asset_root)
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": asset_type,
                "layer_type": "CAD" if asset_type == "cad" else "Vector",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "vector_rel_path": asset_rel,
                "processed_size_bytes": str(asset_size_bytes),
                "processed_size": _format_size_bytes(asset_size_bytes),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": asset_type,
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{asset_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="CAD asset saved." if asset_type == "cad" else "Vector layer uploaded and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{asset_rel}",
        )

    if ext == ".zip":
        print(f"Extracting 3D Tiles ZIP {safe_name}...")
        if output_tile_dir.exists():
            shutil.rmtree(output_tile_dir)
        output_tile_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(input_path, output_tile_dir)
        tileset_root = _find_extracted_tileset_root(output_tile_dir)
        tileset_rel = (tileset_root / "tileset.json").resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        model_rel = tileset_root.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        model_size_bytes = calculate_folder_size(tileset_root)
        tileset_url = f"{str(request.base_url).rstrip('/')}/data/{tileset_rel}"
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": Path(safe_name).stem,
                "tile_folder": tile_output_folder,
                "dataset_type": "3dmodel",
                "layer_type": "3DModel",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "tiles_rel_path": model_rel,
                "tileset_rel_path": tileset_rel,
                "processed_size_bytes": str(model_size_bytes),
                "processed_size": _format_size_bytes(model_size_bytes),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{tileset_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="3D model ZIP extracted and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=Path(safe_name).stem,
            cog_path="",
            cog_tile_url_template=tileset_url,
        )

    if ext == ".csv":
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": "csv",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "processed_size_bytes": str(input_path.stat().st_size),
                "processed_size": _format_size_bytes(input_path.stat().st_size),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{raw_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="CSV dataset uploaded and ready for comparison.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template="",
        )

    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Uploading",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": safe_name,
            "tile_folder": tile_output_folder,
            "dataset_type": normalized_type,
            "layer_type": _raster_layer_type(normalized_type, safe_name),
            "month": normalized_month,
            "created_at": submitted_date,
            "raw_rel_path": raw_rel,
            "cog_path": str((output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()),
        },
    )
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": dataset_id,
            "kind": "dataset",
            "file_name": safe_name,
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)

    background_tasks.add_task(
        process_dataset_background,
        safe_project_id,
        dataset_id,
        str(input_path),
        safe_name,
        str(output_tile_dir),
        tile_output_folder,
        manual_epsg,
    )

    pending_cog_path = (output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()
    tile_template = _titiler_tile_url_template(
        str(request.base_url),
        str(pending_cog_path),
        _raster_layer_type(normalized_type, safe_name),
    )
    return ProcessDatasetOut(
        status="success",
        message="Dataset uploaded. COG conversion started in background.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=safe_name,
        cog_path=str(pending_cog_path),
        cog_tile_url_template=tile_template,
    )


@app.post("/api/datasets/{project_id}/generate-contours", response_model=ProcessDatasetOut)
async def generate_contours(
    project_id: str,
    payload: ContourGeneratePayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ProcessDatasetOut:
    user = _require_user(request)
    _enforce_rate_limit(request, "generate-contours")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    if payload.interval <= 0:
        raise HTTPException(status_code=400, detail="Contour interval must be greater than 0")
    if payload.dataset_id:
        source_path = _dataset_source_path(safe_project_id, payload.dataset_id)
    else:
        raw_rel = payload.source_tif.replace("\\", "/").lstrip("/")
        if ".." in raw_rel:
            raise HTTPException(status_code=400, detail="Invalid source_tif")
        source_path = (Path(LOCAL_DATA_PATH) / raw_rel).resolve()
        local_root = Path(LOCAL_DATA_PATH).resolve()
        if local_root not in source_path.parents or not source_path.is_file():
            raise HTTPException(status_code=404, detail="Source DEM .tif not found")
    if source_path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Source must be a DEM .tif/.tiff")

    source_name = source_path.stem
    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{source_name}-contours-{payload.interval:g}m").strip("-")
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    _, processed_dir = get_project_dataset_type_dirs(safe_project_id, "vector")
    output_dir = processed_dir / dataset_id
    output_geojson = output_dir / f"{dataset_stem}.geojson"
    rel = output_geojson.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()

    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Processing",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": f"{source_name} contours",
            "tile_folder": "",
            "dataset_type": "vector",
            "layer_type": "Vector",
            "vector_rel_path": rel,
        },
    )
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": dataset_id,
            "kind": "vector",
            "file_name": f"{source_name} contours",
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_contours_background,
        safe_project_id,
        dataset_id,
        str(source_path),
        str(output_geojson),
        payload.interval,
        f"{source_name} contours",
    )
    return ProcessDatasetOut(
        status="success",
        message="Contour generation started.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=f"{source_name} contours",
        cog_path="",
        cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{rel}",
    )


@app.post("/api/upload-dataset")
async def upload_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    verify_admin(request)
    await process_dataset(request, background_tasks, file, project_id)
    return {"status": "processing"}


@app.post("/api/datasets/{project_id}/sync")
def sync_manual_datasets(project_id: str, request: Request) -> dict[str, str]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    _, processed_dir = get_project_dirs(safe_project_id)
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    tracked_folders: set[str] = set()
    tracked_dataset_by_key: dict[str, str] = {}
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        st = _read_dataset_status(safe_project_id, job_dir.name)
        if not st:
            continue
        folder = (st.get("tile_folder") or "").strip()
        if folder:
            tracked_folders.add(folder)
            tracked_dataset_by_key[folder] = job_dir.name
        rel = (st.get("tiles_rel_path") or "").strip()
        if rel:
            tracked_folders.add(rel)
            tracked_dataset_by_key[rel] = job_dir.name
        cog_rel = (st.get("cog_rel_path") or "").strip()
        if cog_rel:
            tracked_folders.add(cog_rel)
            tracked_dataset_by_key[cog_rel] = job_dir.name

    found_new = 0
    candidates: list[tuple[Path, str, str, str]] = [
        *[
            (
                item,
                _raster_layer_type(_infer_dataset_type(f"{item.parent.name} {item.name}"), item.name),
                _infer_dataset_type(f"{item.parent.name} {item.name}"),
                "cog",
            )
            for item in _candidate_processed_cog_files(processed_dir)
        ],
        *[(item, _raster_layer_type(_infer_dataset_type(item.name), item.name), _infer_dataset_type(item.name), "folder") for item in _candidate_processed_tile_dirs(processed_dir)],
        *[(item, "3DModel", "3dmodel", "folder") for item in _candidate_processed_model_dirs(processed_dir)],
    ]
    for item, layer_kind, dataset_type, asset_kind in candidates:
        rel_path = item.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        folder_name = _display_model_folder_name(item, processed_dir) if layer_kind == "3DModel" else item.name
        if asset_kind == "cog":
            folder_name = folder_name.replace(".cog.tiff", ".tiff").replace(".cog.tif", ".tif").replace("_cog.tiff", ".tiff").replace("_cog.tif", ".tif")
        if layer_kind != "3DModel":
            dataset_type = _normalize_dataset_type(dataset_type, folder_name)
            layer_kind = _raster_layer_type(dataset_type, folder_name)
        if folder_name in tracked_folders or rel_path in tracked_folders:
            existing_dataset_id = tracked_dataset_by_key.get(rel_path) or tracked_dataset_by_key.get(folder_name)
            if existing_dataset_id and asset_kind == "cog":
                existing_status = _read_dataset_status(safe_project_id, existing_dataset_id) or {}
                if not existing_status.get("bounds_wgs84") or not existing_status.get("source_crs"):
                    raster_metadata = _read_raster_manual_metadata(item, dataset_type)
                    if raster_metadata:
                        _write_dataset_status(
                            safe_project_id,
                            existing_dataset_id,
                            {
                                **existing_status,
                                **raster_metadata,
                                "updated_at": _now_iso(),
                            },
                        )
            continue

        dataset_id = _safe_dataset_id(
            f"manual-{re.sub(r'[^A-Za-z0-9._-]+', '-', folder_name)[:48]}-{secrets.token_hex(4)}",
        )
        raster_metadata = _read_raster_manual_metadata(item, dataset_type) if asset_kind == "cog" else {}
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": folder_name,
                "tile_folder": folder_name,
                "dataset_type": dataset_type,
                "layer_type": layer_kind,
                "month": "",
                "raw_rel_path": "",
                "tiles_rel_path": "" if asset_kind == "cog" else rel_path,
                "cog_path": str(item.resolve()) if asset_kind == "cog" else "",
                "cog_rel_path": rel_path if asset_kind == "cog" else "",
                **raster_metadata,
            },
        )
        result_url = f"/data/{rel_path}/tileset.json" if layer_kind == "3DModel" else f"/data/{rel_path}"
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": folder_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": result_url,
                "dataset_type": dataset_type,
                "layer_type": layer_kind,
                "tiles_rel_path": "" if asset_kind == "cog" else rel_path,
                "cog_path": str(item.resolve()) if asset_kind == "cog" else "",
                "cog_rel_path": rel_path if asset_kind == "cog" else "",
                **raster_metadata,
            },
        )
        tracked_folders.add(folder_name)
        tracked_folders.add(rel_path)
        found_new += 1

    _invalidate_project_files_cache(safe_project_id)
    return {
        "status": "success",
        "message": f"Found {found_new} manual datasets",
        "new_count": str(found_new),
    }


def _bulk_scan_files(source_dir: Path, kind: str) -> list[Path]:
    safe_kind = str(kind or "").strip().lower()
    if safe_kind == "las":
        exts = {".las", ".laz"}
    elif safe_kind in {"ortho", "dtm", "dsm"}:
        exts = {".tif", ".tiff"}
    else:
        return []
    files = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: p.name.lower())


async def _admin_manual_bulk_import_background(
    *,
    project_id: str,
    tasks: list[AdminManualBulkImportTask],
    max_parallel: int,
) -> None:
    safe_project_id = _safe_project_id(project_id)
    semaphore = asyncio.Semaphore(max(1, min(int(max_parallel or 2), 6)))

    async def run_one_pointcloud(source_file: Path) -> None:
        async with semaphore:
            raw_dir, _ = get_project_dirs(safe_project_id)
            safe_name = _safe_pointcloud_basename(source_file.name)
            dataset_id = _safe_dataset_id(f"{re.sub(r'[^A-Za-z0-9._-]+', '-', Path(safe_name).stem)[:40]}-{secrets.token_hex(6)}")
            raw_target = raw_dir / f"{safe_project_id}__{dataset_id}__{safe_name}"
            dataset_folder = _potree_dataset_name(dataset_id)
            output_dir = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "processed" / dataset_folder
            output_dir.mkdir(parents=True, exist_ok=True)

            try:
                shutil.copy2(source_file, raw_target)
            except OSError as exc:
                _upsert_processing_job(
                    safe_project_id,
                    {
                        "job_id": dataset_id,
                        "kind": "pointcloud",
                        "file_name": safe_name,
                        "status": "Failed",
                        "error": f"Copy failed: {exc}"[:8000],
                        "updated_at": _now_iso(),
                    },
                )
                _invalidate_project_files_cache(safe_project_id)
                return

            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "pointcloud",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Queued",
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)

            await asyncio.to_thread(
                process_pointcloud_potree_job,
                str(raw_target),
                str(output_dir),
                dataset_folder,
                safe_project_id,
                dataset_id,
                safe_name,
                "",
            )

    async def run_one_raster(source_file: Path, dataset_type: str) -> None:
        async with semaphore:
            normalized_type = _normalize_dataset_type(dataset_type, source_file.name)
            raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
            safe_name = _safe_dataset_upload_basename(source_file.name)
            ext = source_file.suffix.lower()
            dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
            dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
            tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")
            input_path = raw_dir / f"{tile_output_folder}{ext}"
            output_tile_dir = processed_dir / tile_output_folder
            output_tile_dir.mkdir(parents=True, exist_ok=True)

            try:
                shutil.copy2(source_file, input_path)
            except OSError as exc:
                _upsert_processing_job(
                    safe_project_id,
                    {
                        "job_id": dataset_id,
                        "kind": "dataset",
                        "file_name": safe_name,
                        "status": "Failed",
                        "error": f"Copy failed: {exc}"[:8000],
                        "updated_at": _now_iso(),
                    },
                )
                _invalidate_project_files_cache(safe_project_id)
                return

            raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
            pending_cog_path = (output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()
            _write_dataset_status(
                safe_project_id,
                dataset_id,
                {
                    "status": "Uploading",
                    "updated_at": _now_iso(),
                    "dataset_id": dataset_id,
                    "dataset_name": safe_name,
                    "tile_folder": tile_output_folder,
                    "dataset_type": normalized_type,
                    "layer_type": _raster_layer_type(normalized_type, safe_name),
                    "month": "",
                    "created_at": "",
                    "raw_rel_path": raw_rel,
                    "processed_size_bytes": str(input_path.stat().st_size),
                    "processed_size": _format_size_bytes(input_path.stat().st_size),
                    "cog_path": str(pending_cog_path),
                    "manual_epsg": "",
                    "applied_epsg": "",
                },
            )
            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "dataset",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Queued",
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)
            await process_dataset_background(
                safe_project_id,
                dataset_id,
                str(input_path),
                safe_name,
                str(output_tile_dir),
                tile_output_folder,
                "",
            )

    try:
        scheduled: list[asyncio.Task[None]] = []
        for task in tasks:
            source = Path(str(task.source_folder or "")).expanduser().resolve()
            if not source.is_dir():
                continue
            kind = str(task.kind or "").strip().lower()
            files = _bulk_scan_files(source, kind)
            if not files:
                continue
            if kind == "las":
                for file_path in files:
                    scheduled.append(asyncio.create_task(run_one_pointcloud(file_path)))
            elif kind in {"ortho", "dtm", "dsm"}:
                for file_path in files:
                    scheduled.append(asyncio.create_task(run_one_raster(file_path, kind)))
        if scheduled:
            await asyncio.gather(*scheduled)
    finally:
        _invalidate_project_files_cache(safe_project_id)


def _prepare_admin_manual_bulk_import(
    tasks: list[AdminManualBulkImportTask],
) -> tuple[list[AdminManualBulkImportTask], list[dict[str, object]], int]:
    cleaned: list[AdminManualBulkImportTask] = []
    preview: list[dict[str, object]] = []
    file_count = 0
    for item in tasks:
        kind = str(item.kind or "").strip().lower()
        if kind not in {"las", "ortho", "dtm", "dsm"}:
            continue
        folder = str(item.source_folder or "").strip().strip('"')
        if not folder:
            continue
        source = Path(folder).expanduser()
        if not source.is_dir():
            preview.append(
                {
                    "kind": kind,
                    "source_folder": folder,
                    "status": "missing",
                    "file_count": 0,
                    "message": "Folder not found on server",
                }
            )
            continue
        files = _bulk_scan_files(source.resolve(), kind)
        preview.append(
            {
                "kind": kind,
                "source_folder": str(source.resolve()),
                "status": "ready" if files else "empty",
                "file_count": len(files),
                "message": (
                    f"Found {len(files)} file(s)"
                    if files
                    else f"No matching {kind.upper()} files in folder"
                ),
            }
        )
        if not files:
            continue
        cleaned.append(AdminManualBulkImportTask(source_folder=str(source.resolve()), kind=kind))
        file_count += len(files)
    return cleaned, preview, file_count


def _queue_admin_manual_bulk_import(
    *,
    project_id: str,
    payload: AdminManualBulkImportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    cleaned, preview, file_count = _prepare_admin_manual_bulk_import(payload.tasks or [])
    if not cleaned:
        detail = "No importable files found. Check folder path and selected type."
        if preview:
            detail = "; ".join(
                f"{row['source_folder']} ({row['message']})" for row in preview if isinstance(row.get("message"), str)
            ) or detail
        raise HTTPException(status_code=400, detail=detail)

    background_tasks.add_task(
        _admin_manual_bulk_import_background,
        project_id=safe_project_id,
        tasks=cleaned,
        max_parallel=payload.max_parallel,
    )
    return {
        "status": "success",
        "message": f"Queued {file_count} file(s) from {len(cleaned)} folder task(s).",
        "project_id": safe_project_id,
        "task_count": len(cleaned),
        "file_count": file_count,
        "preview": preview,
    }


def _browse_server_folder(initial_path: str = "") -> str:
    if os.name != "nt":
        raise HTTPException(
            status_code=501,
            detail="Server folder picker works only when the backend runs on Windows.",
        )
    initial = str(initial_path or "").strip().strip('"')
    if initial:
        initial_path_obj = Path(initial).expanduser()
        if initial_path_obj.is_dir():
            initial = str(initial_path_obj.resolve())
        elif initial_path_obj.parent.is_dir():
            initial = str(initial_path_obj.parent.resolve())
        else:
            initial = ""
    ps_script = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$d = New-Object System.Windows.Forms.FolderBrowserDialog; "
        "$d.ShowNewFolderButton = $true; "
        "$d.Description = 'Select bulk import source folder on this server'; "
    )
    if initial:
        escaped = initial.replace("'", "''")
        ps_script += f"$d.SelectedPath = '{escaped}'; "
    ps_script += (
        "$r = $d.ShowDialog(); "
        "if ($r -eq [System.Windows.Forms.DialogResult]::OK) { Write-Output $d.SelectedPath }"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            capture_output=True,
            text=True,
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        raise HTTPException(status_code=408, detail="Folder picker timed out") from exc
    if proc.returncode != 0 and not (proc.stdout or "").strip():
        detail = (proc.stderr or proc.stdout or "Folder picker failed").strip()
        raise HTTPException(status_code=500, detail=detail[:800])
    folder = (proc.stdout or "").strip()
    if not folder:
        raise HTTPException(status_code=400, detail="No folder selected")
    resolved = Path(folder).expanduser().resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=400, detail="Selected folder is not accessible")
    return str(resolved)


@app.post("/api/admin/locate-folder")
async def admin_locate_folder(payload: AdminLocateFolderPayload, request: Request) -> dict[str, str]:
    verify_admin(request)
    folder_path = await run_in_threadpool(_browse_server_folder, payload.initial_path)
    return {"status": "success", "folder_path": folder_path}


@app.post("/api/admin/projects/{project_id}/manual-bulk-import")
async def admin_manual_bulk_import(
    project_id: str,
    payload: AdminManualBulkImportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    return _queue_admin_manual_bulk_import(
        project_id=project_id,
        payload=payload,
        request=request,
        background_tasks=background_tasks,
    )


@app.post("/api/admin/manual-bulk-import")
async def admin_manual_bulk_import_compat(
    payload: AdminManualBulkImportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    project_id = str(payload.project_id or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    return _queue_admin_manual_bulk_import(
        project_id=project_id,
        payload=payload,
        request=request,
        background_tasks=background_tasks,
    )


@app.post("/api/datasets/{project_id}/open-manual-folder")
def open_manual_dataset_folder(project_id: str, request: Request) -> dict[str, str]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    _, processed_dir = get_project_dirs(safe_project_id)
    folder = processed_dir.resolve()
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
    return {
        "status": "success",
        "message": "Manual tiles folder opened.",
        "folder_path": str(folder),
    }


@app.get("/api/datasets/{project_id}/{tile_folder:path}/crop-mask")
def get_crop_mask(project_id: str, tile_folder: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    record = _get_crop_mask(safe_project_id, safe_tile_folder)
    if not record:
        return {"status": "none", "points": []}
    try:
        points = json.loads(record["points_json"])
    except json.JSONDecodeError:
        points = []
    if not isinstance(points, list):
        points = []
    return {
        "status": "success",
        "source": record["source"],
        "updated_at": record["updated_at"],
        "points": points,
    }


@app.post("/api/datasets/{project_id}/{tile_folder:path}/crop-mask/kml")
async def save_crop_mask_from_kml(
    project_id: str,
    tile_folder: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    _, processed_dir = get_project_dirs(safe_project_id)
    if not (processed_dir / safe_tile_folder).is_dir():
        raise HTTPException(status_code=404, detail="Tile folder not found")
    try:
        raw = await file.read()
        text = raw.decode("utf-8", errors="replace")
    finally:
        await file.close()
    points = _extract_kml_points(text)
    _save_crop_mask(safe_project_id, safe_tile_folder, "kml", points)
    return {"status": "success", "source": "kml", "points": points}


@app.post("/api/datasets/{project_id}/{tile_folder:path}/crop-mask/draw")
def save_crop_mask_from_draw(
    project_id: str,
    tile_folder: str,
    payload: CropMaskPayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    _, processed_dir = get_project_dirs(safe_project_id)
    if not (processed_dir / safe_tile_folder).is_dir():
        raise HTTPException(status_code=404, detail="Tile folder not found")
    points = _normalize_crop_points(payload.points)
    _save_crop_mask(safe_project_id, safe_tile_folder, "draw", points)
    return {"status": "success", "source": "draw", "points": points}


@app.post("/api/dataset-metadata")
async def dataset_metadata(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    user = verify_admin(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(file.filename or "")
    probe_dir = Path(LOCAL_DATA_PATH) / "uploads" / "metadata_probe" / safe_project_id
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_path = probe_dir / f"{secrets.token_hex(8)}-{safe_name}"
    try:
        with open(probe_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    finally:
        await file.close()
    epsg = _detect_epsg_from_file(probe_path) or ""
    probe_path.unlink(missing_ok=True)
    return {"filename": safe_name, "epsg": epsg}


@app.get("/api/dataset-status/{project_id}/{dataset_id}")
def dataset_status(project_id: str, dataset_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    status = _read_dataset_status(safe_project_id, safe_dataset_id)
    if not status:
        for job in _read_processing_jobs().get(safe_project_id, []):
            if isinstance(job, dict) and str(job.get("job_id") or "") == safe_dataset_id:
                return {
                    "status": str(job.get("status") or "Processing"),
                    "dataset_id": safe_dataset_id,
                    "dataset_name": str(job.get("file_name") or safe_dataset_id),
                    "stage": str(job.get("stage") or "Waiting for processor"),
                    "progress_percent": str(job.get("progress_percent") or "45"),
                    "eta_seconds": str(job.get("eta_seconds") or ""),
                    "updated_at": str(job.get("updated_at") or _now_iso()),
                }
        return {
            "status": "Processing",
            "dataset_id": safe_dataset_id,
            "dataset_name": safe_dataset_id,
            "stage": "Waiting for processor",
            "progress_percent": "45",
            "eta_seconds": "",
            "updated_at": _now_iso(),
        }

    base = str(request.base_url).rstrip("/")
    tiles_rel = status.get("tiles_rel_path", "").strip()
    if str(status.get("dataset_type") or "").lower() in ("3dmodel", "3dtiles"):
        tileset_rel = status.get("tileset_rel_path", "").strip() or f"{tiles_rel.rstrip('/')}/tileset.json"
        status["cog_tile_url_template"] = f"{base}/data/{tileset_rel}"
        status["layer_type"] = "3DModel"
    elif tiles_rel:
        status["cog_tile_url_template"] = f"{base}/data/{tiles_rel}/{{z}}/{{x}}/{{y}}.png"
    else:
        cog_path = status.get("cog_path", "")
        if not cog_path and status.get("cog_rel_path", ""):
            cog_path = str((Path(LOCAL_DATA_PATH) / status.get("cog_rel_path", "")).resolve())
        if cog_path:
            status["cog_tile_url_template"] = _titiler_tile_url_template(
                base,
                cog_path,
                str(status.get("layer_type") or _raster_layer_type(str(status.get("dataset_type") or ""), str(status.get("dataset_name") or ""))),
                str(status.get("rescale_min") or ""),
                str(status.get("rescale_max") or ""),
            )
    return status


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


def _validate_grid_export_request(dataset_path: Path, interval: float) -> tuple[int, int, int]:
    if not math.isfinite(interval) or interval <= 0:
        raise HTTPException(status_code=400, detail="Grid interval must be greater than zero.")
    rasterio, _ = _require_rasterio()
    with rasterio.open(str(dataset_path)) as dataset:
        if dataset.count < 1:
            raise HTTPException(status_code=400, detail="Raster has no elevation band.")
        if dataset.crs and dataset.crs.is_geographic:
            raise HTTPException(
                status_code=400,
                detail="Grid export needs a projected CRS in meters. Please use the projected DTM/DSM.",
            )
        width = abs(float(dataset.bounds.right) - float(dataset.bounds.left))
        height = abs(float(dataset.bounds.top) - float(dataset.bounds.bottom))
    x_count = int(math.floor(width / interval)) + 1
    y_count = int(math.floor(height / interval)) + 1
    point_count = max(0, x_count) * max(0, y_count)
    return x_count, y_count, point_count


def _csv_grid_generator(dataset_path: Path, interval: float):
    rasterio, _ = _require_rasterio()
    with rasterio.open(str(dataset_path)) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["X", "Y", "Z"])
        yield output.getvalue()

        for y in _grid_coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch: list[tuple[float, float]] = []
            for x in _grid_coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))
                if len(batch) >= batch_size:
                    yield _csv_grid_rows(dataset, nodata, batch)
                    batch = []
            if batch:
                yield _csv_grid_rows(dataset, nodata, batch)


def _csv_grid_rows(dataset, nodata, batch: list[tuple[float, float]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
        z = _grid_sample_value(sample, nodata)
        if z is not None:
            writer.writerow([coord[0], coord[1], z])
    return output.getvalue()


def _dxf_grid_generator(dataset_path: Path, interval: float):
    rasterio, _ = _require_rasterio()
    yield "0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
    with rasterio.open(str(dataset_path)) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000
        for y in _grid_coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch: list[tuple[float, float]] = []
            for x in _grid_coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))
                if len(batch) >= batch_size:
                    yield _dxf_grid_rows(dataset, nodata, batch)
                    batch = []
            if batch:
                yield _dxf_grid_rows(dataset, nodata, batch)
    yield "0\nENDSEC\n0\nEOF\n"


def _dxf_grid_rows(dataset, nodata, batch: list[tuple[float, float]]) -> str:
    parts: list[str] = []
    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
        z = _grid_sample_value(sample, nodata)
        if z is not None:
            parts.append(f"0\nPOINT\n8\nDROID_GRID\n10\n{coord[0]}\n20\n{coord[1]}\n30\n{z}\n")
    return "".join(parts)


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


def _grid_export_is_current(output_path: Path, dataset_path: Path, interval: float, export_format: str) -> bool:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        return False
    metadata_path = output_path.with_suffix(f"{output_path.suffix}.json")
    if not metadata_path.is_file():
        return output_path.stat().st_mtime_ns >= dataset_path.stat().st_mtime_ns
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return (
        str(metadata.get("source_path") or "") == str(dataset_path.resolve())
        and str(metadata.get("source_mtime_ns") or "") == str(dataset_path.stat().st_mtime_ns)
        and str(metadata.get("format") or "").lower() == export_format
        and math.isclose(float(metadata.get("interval") or 0), float(interval), rel_tol=0.0, abs_tol=1e-9)
    )


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


@app.get("/api/datasets/{project_id}/{dataset_id}/grid-export")
def export_dataset_grid(
    project_id: str,
    dataset_id: str,
    request: Request,
    interval: float = 2.0,
    format: str = "csv",
):
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    export_format = format.lower().strip()
    if export_format not in {"csv", "dxf"}:
        raise HTTPException(status_code=400, detail="Grid export format must be csv or dxf.")

    dataset_path, st = _grid_export_raster_path(safe_project_id, safe_dataset_id)
    _, _, point_count = _validate_grid_export_request(dataset_path, interval)
    display_name = Path(st.get("dataset_name") or dataset_path.stem).stem
    safe_name = _safe_export_stem(display_name, safe_dataset_id)
    interval_token = str(interval).replace(".", "p")
    output_name = f"{safe_name}_grid_{interval_token}m.{export_format}"
    output_path = _grid_export_output_path(safe_project_id, safe_dataset_id, output_name)
    if not _grid_export_is_current(output_path, dataset_path, interval, export_format):
        _generate_grid_export_file(output_path, dataset_path, interval, export_format)
        _write_grid_export_metadata(
            output_path,
            {
                "name": output_name,
                "kind": "Generated Grid Export",
                "dataset_id": safe_dataset_id,
                "dataset_name": str(st.get("dataset_name") or display_name),
                "dataset_type": str(st.get("dataset_type") or ""),
                "source_path": str(dataset_path.resolve()),
                "source_mtime_ns": str(dataset_path.stat().st_mtime_ns),
                "interval": float(interval),
                "format": export_format,
                "point_count": point_count,
                "created_at": _now_iso(),
            },
        )
        _invalidate_project_files_cache(safe_project_id)

    return FileResponse(
        str(output_path),
        media_type="text/csv" if export_format == "csv" else "application/dxf",
        filename=output_name,
        content_disposition_type="attachment",
    )


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


def _sample_raster(dataset_path: Path, lat: float, lng: float) -> float:
    rasterio, rio_transform = _require_rasterio()
    with rasterio.open(str(dataset_path)) as src:
        xs, ys = rio_transform("EPSG:4326", src.crs, [lng], [lat]) if src.crs else ([lng], [lat])
        row, col = src.index(xs[0], ys[0])
        if row < 0 or col < 0 or row >= src.height or col >= src.width:
            raise HTTPException(status_code=400, detail="Point is outside raster bounds")
        value = next(src.sample([(xs[0], ys[0])], masked=True))[0]
        if getattr(value, "mask", False):
            raise HTTPException(status_code=404, detail="No elevation value at this point")
        if value is None or not math.isfinite(float(value)):
            raise HTTPException(status_code=404, detail="No elevation value at this point")
        return float(value)


def _interpolate_profile_points(points: list[list[float]], samples: int) -> list[dict[str, float]]:
    clean = _normalize_crop_points(points) if len(points) >= 3 else []
    if not clean:
        clean = []
        for pair in points:
            if len(pair) >= 2:
                clean.append([float(pair[0]), float(pair[1])])
    if len(clean) < 2:
        raise HTTPException(status_code=400, detail="At least 2 profile points required")
    segment_lengths: list[float] = []
    total = 0.0
    for idx in range(1, len(clean)):
        a = LLatLng(clean[idx - 1][0], clean[idx - 1][1])
        b = LLatLng(clean[idx][0], clean[idx][1])
        dist = a.distance_to(b)
        segment_lengths.append(dist)
        total += dist
    count = max(2, min(int(samples or 120), 500))
    targets = [total * i / (count - 1) for i in range(count)]
    out: list[dict[str, float]] = []
    seg_start_dist = 0.0
    seg_idx = 0
    for target in targets:
        while seg_idx < len(segment_lengths) - 1 and target > seg_start_dist + segment_lengths[seg_idx]:
            seg_start_dist += segment_lengths[seg_idx]
            seg_idx += 1
        seg_len = segment_lengths[seg_idx] or 1.0
        t = (target - seg_start_dist) / seg_len
        lat_a, lng_a = clean[seg_idx]
        lat_b, lng_b = clean[seg_idx + 1]
        out.append({
            "lat": lat_a + (lat_b - lat_a) * t,
            "lng": lng_a + (lng_b - lng_a) * t,
            "distance_m": target,
        })
    return out


def _profile_summary(values: list[dict[str, object]], corridor_width_m: float) -> dict[str, float | None]:
    valid = [
        {
            "distance_m": float(row["distance_m"]),
            "elevation": float(row["elevation"]),
        }
        for row in values
        if row.get("elevation") is not None
    ]
    if not valid:
        return {
            "length_m": None,
            "min_elevation": None,
            "max_elevation": None,
            "avg_elevation": None,
            "start_elevation": None,
            "end_elevation": None,
            "elevation_change": None,
            "elevation_gain": None,
            "elevation_loss": None,
            "volume_above_min_m3": None,
            "corridor_width_m": max(0.1, min(float(corridor_width_m or 1.0), 1000.0)),
        }

    elevations = [row["elevation"] for row in valid]
    min_elev = min(elevations)
    gain = 0.0
    loss = 0.0
    volume_above_min = 0.0
    width = max(0.1, min(float(corridor_width_m or 1.0), 1000.0))
    for prev, curr in zip(valid, valid[1:]):
        diff = curr["elevation"] - prev["elevation"]
        if diff > 0:
            gain += diff
        else:
            loss += abs(diff)
        segment_len = max(0.0, curr["distance_m"] - prev["distance_m"])
        avg_height_above_min = ((prev["elevation"] - min_elev) + (curr["elevation"] - min_elev)) / 2
        volume_above_min += segment_len * width * avg_height_above_min

    return {
        "length_m": max(row["distance_m"] for row in valid),
        "min_elevation": min_elev,
        "max_elevation": max(elevations),
        "avg_elevation": sum(elevations) / len(elevations),
        "start_elevation": valid[0]["elevation"],
        "end_elevation": valid[-1]["elevation"],
        "elevation_change": valid[-1]["elevation"] - valid[0]["elevation"],
        "elevation_gain": gain,
        "elevation_loss": loss,
        "volume_above_min_m3": volume_above_min,
        "corridor_width_m": width,
    }


def _circle_points(center: list[float], radius_m: float, segments: int = 96) -> list[list[float]]:
    if len(center) < 2:
        raise HTTPException(status_code=400, detail="Circle center is required")
    lat = float(center[0])
    lng = float(center[1])
    radius = max(0.1, float(radius_m))
    lat_rad = math.radians(lat)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lng = max(1.0, 111320.0 * math.cos(lat_rad))
    points: list[list[float]] = []
    for idx in range(max(16, segments)):
        angle = (2 * math.pi * idx) / max(16, segments)
        points.append([
            lat + (math.sin(angle) * radius) / meters_per_deg_lat,
            lng + (math.cos(angle) * radius) / meters_per_deg_lng,
        ])
    return points


def _pixel_area_m2(src) -> float:
    if src.crs and getattr(src.crs, "is_geographic", False):
        center_lat = (src.bounds.top + src.bounds.bottom) / 2
        meters_per_deg_lng = 111320.0 * math.cos(math.radians(center_lat))
        return abs(src.transform.a * meters_per_deg_lng * src.transform.e * 111320.0)
    return abs(src.transform.a * src.transform.e)


def _volume_for_raster(path: Path, points: list[list[float]], base_elevation: float | None) -> dict[str, object]:
    rasterio, rio_transform = _require_rasterio()
    from rasterio.features import geometry_mask  # type: ignore
    import numpy as np  # type: ignore

    with rasterio.open(str(path)) as src:
        arr = src.read(1, masked=True).astype("float64")
        valid = ~np.ma.getmaskarray(arr)
        scope = "overall"
        if points:
            clean = _normalize_crop_points(points)
            lngs = [p[1] for p in clean]
            lats = [p[0] for p in clean]
            xs, ys = rio_transform("EPSG:4326", src.crs, lngs, lats) if src.crs else (lngs, lats)
            geom = {"type": "Polygon", "coordinates": [[list(pair) for pair in zip(xs, ys)]]}
            inside = geometry_mask([geom], out_shape=(src.height, src.width), transform=src.transform, invert=True)
            valid = valid & inside
            scope = "selection"
        if not np.any(valid):
            raise HTTPException(status_code=404, detail="No valid DTM cells found for volume")

        values = np.asarray(arr.filled(np.nan))[valid]
        values = values[np.isfinite(values)]
        if values.size == 0:
            raise HTTPException(status_code=404, detail="No valid DTM elevation values found for volume")

        base = float(base_elevation) if base_elevation is not None else float(np.min(values))
        pixel_area = _pixel_area_m2(src)
        heights = values - base
        fill = float(np.sum(np.where(heights > 0, heights, 0)) * pixel_area)
        cut = float(np.sum(np.where(heights < 0, -heights, 0)) * pixel_area)
        net = fill - cut
        area_m2 = float(values.size * pixel_area)
        min_elev = float(np.min(values))
        max_elev = float(np.max(values))
        avg_elev = float(np.mean(values))
        bins = []
        if max_elev > min_elev:
            hist, edges = np.histogram(values, bins=min(12, max(3, int(math.sqrt(values.size) // 2))))
            for idx, count in enumerate(hist):
                low = float(edges[idx])
                high = float(edges[idx + 1])
                mid_height = max(((low + high) / 2) - base, 0)
                bins.append({
                    "label": f"{low:.2f}-{high:.2f} m",
                    "volume": float(count * pixel_area * mid_height),
                })
    return {
        "scope": scope,
        "base_elevation": base,
        "min_elevation": min_elev,
        "max_elevation": max_elev,
        "avg_elevation": avg_elev,
        "area_m2": area_m2,
        "fill_volume_m3": fill,
        "cut_volume_m3": cut,
        "net_volume_m3": net,
        "cell_count": int(values.size),
        "bins": bins,
        "unit": "m3",
    }


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


def _dtm_volume_between(project_id: str, prev_id: str, next_id: str) -> dict[str, object]:
    rasterio, _ = _require_rasterio()
    import numpy as np  # type: ignore
    prev_path = _dataset_source_path(project_id, prev_id)
    next_path = _dataset_source_path(project_id, next_id)
    cache_payload = {
        "kind": "dtm_volume",
        "prev": _file_fingerprint(prev_path),
        "next": _file_fingerprint(next_path),
    }
    cache = _cache_path(project_id, "volume", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    with rasterio.open(str(prev_path)) as a, rasterio.open(str(next_path)) as b:
        if a.width != b.width or a.height != b.height or a.transform != b.transform:
            raise HTTPException(status_code=400, detail="DTM rasters must have matching grid for volume fallback")
        arr_a = a.read(1, masked=True).astype("float64")
        arr_b = b.read(1, masked=True).astype("float64")
        diff = arr_b - arr_a
        valid = ~diff.mask if hasattr(diff, "mask") else np.isfinite(diff)
        pixel_area = abs(a.transform.a * a.transform.e)
        cut = float(np.sum(np.where(diff < 0, -diff, 0)[valid]) * pixel_area)
        fill = float(np.sum(np.where(diff > 0, diff, 0)[valid]) * pixel_area)
        net = fill - cut
        area = float(np.sum(valid) * pixel_area)
    result: dict[str, object] = {
        "month": "",
        "label": f"{prev_id} to {next_id}",
        "volume": net,
        "cut": cut,
        "fill": fill,
        "net": net,
        "area": area,
        "source": "dtm",
    }
    _write_cache(cache, result)
    return result


@app.get("/api/datasets/{project_id}/{dataset_name}/bounds")
def get_dataset_bounds(project_id: str, dataset_name: str, request: Request) -> dict[str, list[float] | None]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_name = dataset_name.strip()
    if not safe_dataset_name or "/" in safe_dataset_name or "\\" in safe_dataset_name or ".." in safe_dataset_name:
        raise HTTPException(status_code=400, detail="Invalid dataset_name")
    for st in _project_dataset_statuses(safe_project_id):
        dataset_id = str(st.get("dataset_id") or "")
        status_name = str(st.get("dataset_name") or "")
        tile_folder = str(st.get("tile_folder") or "")
        cog_rel_path = str(st.get("cog_rel_path") or "")
        candidates = {
            dataset_id,
            status_name,
            Path(status_name).stem,
            tile_folder,
            Path(cog_rel_path).stem,
        }
        if safe_dataset_name in candidates:
            bounds_text = str(st.get("bounds_wgs84") or "")
            if bounds_text:
                try:
                    bounds = json.loads(bounds_text)
                    if isinstance(bounds, list) and len(bounds) == 4:
                        return {"bounds": [float(value) for value in bounds]}
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            cog_path_text = str(st.get("cog_path") or "")
            if not cog_path_text and cog_rel_path:
                cog_path_text = str((Path(LOCAL_DATA_PATH) / cog_rel_path).resolve())
            if cog_path_text:
                try:
                    import rasterio
                    from rasterio.warp import transform_bounds

                    with rasterio.open(Path(cog_path_text).resolve()) as src:
                        if src.crs:
                            minx, miny, maxx, maxy = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
                            return {"bounds": [float(minx), float(miny), float(maxx), float(maxy)]}
                except Exception as exc:  # noqa: BLE001
                    print(f"Error reading COG bounds: {exc}")
    tiles_dir = _resolve_dataset_tiles_dir(safe_project_id, safe_dataset_name)
    if not tiles_dir:
        return {"bounds": None}
    xml_path = tiles_dir / "tilemapresource.xml"
    if xml_path.exists():
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            bbox = root.find(".//BoundingBox")
            if bbox is None:
                for node in root.iter():
                    if node.tag.endswith("BoundingBox"):
                        bbox = node
                        break
            if bbox is not None:
                minx = bbox.get("minx")
                miny = bbox.get("miny")
                maxx = bbox.get("maxx")
                maxy = bbox.get("maxy")
                if minx and miny and maxx and maxy:
                    return {"bounds": [float(minx), float(miny), float(maxx), float(maxy)]}
        except Exception as exc:  # noqa: BLE001
            print(f"Error reading bounds XML: {exc}")

    # Manual QGIS exports may not include tilemapresource.xml; derive from XYZ indices.
    xyz_bounds = _xyz_bounds_from_tiles_dir(tiles_dir)
    if xyz_bounds:
        return {"bounds": xyz_bounds}
    return {"bounds": None}


@app.post("/api/datasets/{project_id}/metadata")
def update_dataset_metadata(project_id: str, payload: DatasetMetaPayload, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    dataset_id = _safe_dataset_id(payload.dataset_id)
    st = _read_dataset_status(safe_project_id, dataset_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset status not found")
    st["month"] = _normalize_month(payload.month)
    if payload.dataset_type.strip():
        st["dataset_type"] = _normalize_dataset_type(payload.dataset_type, st.get("dataset_name", ""))
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.put("/api/datasets/{project_id}/{dataset_id}/metadata")
def update_dataset_owner_metadata(
    project_id: str,
    dataset_id: str,
    payload: DatasetOwnerPathMetaPayload,
    request: Request,
) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    if str(user.get("role", "")).lower() != "admin":
        _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(safe_project_id, safe_dataset_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset status not found")
    if payload.height_offset is not None:
        st["height_offset"] = f"{float(payload.height_offset):.3f}".rstrip("0").rstrip(".")
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, safe_dataset_id, st)
    _sync_dataset_metadata_to_processing_job(safe_project_id, safe_dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.patch("/api/admin/datasets/{project_id}/metadata")
def admin_update_dataset_metadata(
    project_id: str,
    payload: AdminDatasetMetaPayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    safe_project_id = _safe_project_id(project_id)
    dataset_id = _safe_dataset_id(payload.dataset_id)
    st = _read_dataset_status(safe_project_id, dataset_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset status not found")
    if payload.name is not None and payload.name.strip():
        st["dataset_name"] = payload.name.strip()
    if payload.month is not None:
        st["month"] = _normalize_month(payload.month)
    if payload.date is not None:
        st["upload_date"] = payload.date.strip()[:40]
        st["date"] = payload.date.strip()[:40]
    if payload.status is not None and payload.status.strip():
        st["status"] = payload.status.strip()
    if payload.dataset_type is not None and payload.dataset_type.strip():
        st["dataset_type"] = _normalize_dataset_type(
            payload.dataset_type,
            st.get("dataset_name", ""),
        )
    if payload.height_offset is not None:
        st["height_offset"] = f"{float(payload.height_offset):.3f}".rstrip("0").rstrip(".")
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, dataset_id, st)
    _sync_dataset_metadata_to_processing_job(safe_project_id, dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


def _admin_dataset_status_by_key(project_id: str, dataset_key: str) -> tuple[str, dict[str, str]]:
    clean_key = os.path.basename(dataset_key.replace("\\", "/").strip().strip("/"))
    if not clean_key or clean_key in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid dataset_name")
    decoded = clean_key
    for st in _project_dataset_statuses(project_id):
        dataset_id = str(st.get("dataset_id") or "")
        dataset_name = str(st.get("dataset_name") or "")
        tile_folder = str(st.get("tile_folder") or "")
        candidates = {
            dataset_id,
            dataset_name,
            Path(dataset_name).stem,
            tile_folder,
            Path(tile_folder).name,
        }
        if decoded in candidates:
            return dataset_id, st
    raise HTTPException(status_code=404, detail="Dataset not found")


def _remove_processing_job(project_id: str, dataset_id: str) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    jobs[project_id] = [item for item in current if str(item.get("job_id")) != dataset_id]
    _write_processing_jobs(jobs)


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


def _delete_dataset_artifacts(project_id: str, dataset_id: str, st: dict[str, str]) -> int:
    removed = 0
    for key in ("raw_rel_path", "tiles_rel_path", "tileset_rel_path", "vector_rel_path", "model_rel_path", "cog_rel_path"):
        rel = str(st.get(key) or "").strip().replace("\\", "/").lstrip("/")
        if rel and ".." not in rel:
            removed += _safe_remove_dataset_path(Path(LOCAL_DATA_PATH) / rel)
    tile_folder = str(st.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = get_project_dirs(project_id)
        for candidate in (
            processed_root / tile_folder,
            processed_root / _dataset_type_folder(str(st.get("dataset_type") or "")) / tile_folder,
        ):
            if candidate.exists():
                removed += _safe_remove_dataset_path(candidate)
    _safe_remove_dataset_path(_dataset_dir(project_id, dataset_id))
    _remove_processing_job(project_id, dataset_id)
    _invalidate_project_files_cache(project_id)
    return removed


@app.put("/api/admin/projects/{project_id}/datasets/{dataset_name:path}")
def admin_update_dataset_metadata_by_name(
    project_id: str,
    dataset_name: str,
    payload: AdminDatasetPathMetaPayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_name = os.path.basename(dataset_name.replace("\\", "/").strip().strip("/"))
    dataset_id, st = _admin_dataset_status_by_key(safe_project_id, safe_dataset_name)
    if payload.name is not None and payload.name.strip():
        st["dataset_name"] = payload.name.strip()
    if payload.month is not None:
        st["month"] = _normalize_month(payload.month)
    if payload.date is not None:
        st["upload_date"] = payload.date.strip()[:40]
        st["date"] = payload.date.strip()[:40]
    if payload.status is not None and payload.status.strip():
        st["status"] = payload.status.strip()
    if payload.dataset_type is not None and payload.dataset_type.strip():
        st["dataset_type"] = _normalize_dataset_type(payload.dataset_type, st.get("dataset_name", ""))
    if payload.height_offset is not None:
        st["height_offset"] = f"{float(payload.height_offset):.3f}".rstrip("0").rstrip(".")
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, dataset_id, st)
    _sync_dataset_metadata_to_processing_job(safe_project_id, dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success", "dataset_id": dataset_id}


@app.delete("/api/admin/projects/{project_id}/datasets/{dataset_name:path}")
def admin_delete_dataset_by_name(
    project_id: str,
    dataset_name: str,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str | int]:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_name = os.path.basename(dataset_name.replace("\\", "/").strip().strip("/"))
    dataset_id, st = _admin_dataset_status_by_key(safe_project_id, safe_dataset_name)
    removed = _delete_dataset_artifacts(safe_project_id, dataset_id, st)
    return {"status": "success", "dataset_id": dataset_id, "removed_paths": removed}


@app.get("/api/analysis/{project_id}/elevation")
def analysis_elevation(
    project_id: str,
    dataset_id: str,
    lat: float,
    lng: float,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Elevation requires a DTM/DSM TIFF source")
    fp = _file_fingerprint(path)
    cache_payload = {"kind": "elevation", "source": fp, "lat": round(lat, 8), "lng": round(lng, 8)}
    cache = _cache_path(safe_project_id, "elevation", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    value = _sample_raster(path, lat, lng)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": dataset_id,
        "lat": lat,
        "lng": lng,
        "elevation": value,
        "unit": "m",
        "cached": False,
    }
    _write_cache(cache, result)
    return result


@app.post("/api/analysis/{project_id}/profile")
def analysis_profile(project_id: str, payload: ProfilePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, payload.dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Profile requires a DTM/DSM TIFF source")
    samples = _interpolate_profile_points(payload.points, payload.samples)
    fp = _file_fingerprint(path)
    corridor_width_m = max(0.1, min(float(payload.corridor_width_m or 1.0), 1000.0))
    cache_payload = {
        "kind": "profile",
        "source": fp,
        "points": payload.points,
        "samples": payload.samples,
        "corridor_width_m": corridor_width_m,
    }
    cache = _cache_path(safe_project_id, "profile", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    values = []
    for sample in samples:
        try:
            elev: float | None = _sample_raster(path, sample["lat"], sample["lng"])
        except HTTPException:
            elev = None
        values.append({**sample, "elevation": elev})
    summary = _profile_summary(values, corridor_width_m)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": payload.dataset_id,
        "unit": "m",
        "points": values,
        **summary,
        "cached": False,
    }
    _write_cache(cache, result)
    return result


@app.post("/api/analysis/{project_id}/volume")
def analysis_volume(project_id: str, payload: VolumePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, payload.dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Volume requires a DTM/DSM TIFF source")
    points = payload.points
    if payload.circle_center and payload.circle_radius_m > 0:
        points = _circle_points(payload.circle_center, payload.circle_radius_m)
    fp = _file_fingerprint(path)
    cache_payload = {
        "kind": "single_dtm_volume",
        "source": fp,
        "points": points,
        "base_elevation": payload.base_elevation,
    }
    cache = _cache_path(safe_project_id, "single-volume", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    volume = _volume_for_raster(path, points, payload.base_elevation)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": payload.dataset_id,
        **volume,
        "cached": False,
    }
    _write_cache(cache, result)
    return result


@app.get("/api/compare/{project_id}/datasets")
def compare_datasets(project_id: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    rows = []
    for st in _project_dataset_statuses(safe_project_id):
        dataset_type = str(st.get("dataset_type") or _infer_dataset_type(str(st.get("dataset_name") or "")))
        if dataset_type not in ("dtm", "dsm", "csv"):
            continue
        raw_rel = str(st.get("raw_rel_path") or "")
        raw_path = Path(LOCAL_DATA_PATH) / raw_rel if raw_rel else None
        rows.append({
            "dataset_id": st.get("dataset_id", ""),
            "name": st.get("dataset_name", ""),
            "dataset_type": dataset_type,
            "month": st.get("month", ""),
            "status": st.get("status", ""),
            "has_source": bool(raw_path and raw_path.is_file()),
        })
    return {"datasets": rows}


@app.post("/api/compare/{project_id}/volume")
def compare_volume(project_id: str, payload: CompareVolumePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    selected = [_safe_dataset_id(item) for item in payload.dataset_ids if item]
    statuses = [
        st for st in _project_dataset_statuses(safe_project_id)
        if not selected or str(st.get("dataset_id")) in selected
    ]
    statuses.sort(key=lambda st: (str(st.get("month") or ""), str(st.get("dataset_name") or "")))
    csv_rows: list[dict[str, object]] = []
    dtm_rows: list[dict[str, str]] = []
    for st in statuses:
        dtype = str(st.get("dataset_type") or _infer_dataset_type(str(st.get("dataset_name") or "")))
        if dtype == "csv":
            try:
                csv_rows.extend(_read_volume_csv(_dataset_source_path(safe_project_id, str(st.get("dataset_id")))))
            except Exception:
                continue
        elif dtype in ("dtm", "dsm"):
            dtm_rows.append(st)
    if csv_rows:
        return {"status": "success", "source": "csv", "rows": csv_rows}
    volume_rows: list[dict[str, object]] = []
    for idx in range(1, len(dtm_rows)):
        row = _dtm_volume_between(
            safe_project_id,
            str(dtm_rows[idx - 1].get("dataset_id")),
            str(dtm_rows[idx].get("dataset_id")),
        )
        row["month"] = str(dtm_rows[idx].get("month") or row.get("month") or "")
        row["label"] = f"{dtm_rows[idx - 1].get('month') or dtm_rows[idx - 1].get('dataset_name')} to {dtm_rows[idx].get('month') or dtm_rows[idx].get('dataset_name')}"
        volume_rows.append(row)
    return {"status": "success", "source": "dtm", "rows": volume_rows}


@app.post("/api/compare/{project_id}/refresh-if-changed")
def compare_refresh_if_changed(project_id: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    cache_dir = _analysis_cache_dir(safe_project_id)
    removed = 0
    for cache_file in cache_dir.glob("*.json"):
        cache_file.unlink(missing_ok=True)
        removed += 1
    return {"status": "success", "removed": removed}


@app.get("/api/jobs/{project_id}")
def project_jobs(project_id: str, request: Request) -> dict[str, list[dict[str, str]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    jobs = _read_processing_jobs()
    return {"jobs": jobs.get(safe_project_id, [])}


@app.get("/api/proxy/tiles/{z}/{x}/{y}.png")
def proxy_tiles(z: int, x: int, y: int, path: str):
    full_path = (Path(LOCAL_DATA_PATH) / path).resolve().as_posix()
    encoded_url = quote(full_path, safe="")
    return RedirectResponse(
        f"/api/titiler/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url={encoded_url}"
    )


@app.get("/api/proxy/info")
def proxy_info(path: str):
    full_path = (Path(LOCAL_DATA_PATH) / path).resolve().as_posix()
    encoded_url = quote(full_path, safe="")
    return RedirectResponse(f"/api/titiler/info?url={encoded_url}")


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
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target_path


def _serve_project_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    target_path = _safe_project_file_response_path(safe_project_id, file_path)
    return FileResponse(str(target_path))


def _serve_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    base_dir = (Path(LOCAL_DATA_PATH) / "pointclouds" / safe_project_id).resolve()
    cleaned_path = file_path.replace("\\", "/").lstrip("/")
    if "\x00" in cleaned_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target_path = (base_dir / cleaned_path).resolve()
    base_abs = os.path.abspath(str(base_dir))
    target_abs = os.path.abspath(str(target_path))
    if target_abs != base_abs and not target_abs.startswith(base_abs + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target_path))


@app.get("/api/data/projects/{project_id}/{file_path:path}")
def secure_project_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_project_data_file(project_id, file_path, request)


@app.get("/api/data/pointclouds/{project_id}/{file_path:path}")
def secure_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_pointcloud_data_file(project_id, file_path, request)


@app.get("/data/projects/{project_id}/{file_path:path}")
def secure_legacy_project_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_project_data_file(project_id, file_path, request)


@app.get("/data/pointclouds/{project_id}/{file_path:path}")
def secure_legacy_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_pointcloud_data_file(project_id, file_path, request)


@app.get("/tiles/{file_path:path}")
def secure_legacy_tiles_file(file_path: str, request: Request) -> FileResponse:
    _require_user(request)
    cleaned_path = file_path.replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned_path.split("/") if part]
    if len(parts) >= 2 and parts[0] in {"projects", "pointclouds"}:
        _ensure_project_file_access(request, _safe_project_id(parts[1]))
    if "\x00" in cleaned_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    base_dir = Path(LOCAL_DATA_PATH).resolve()
    target_path = (base_dir / cleaned_path).resolve()
    base_abs = os.path.abspath(str(base_dir))
    target_abs = os.path.abspath(str(target_path))
    if target_abs != base_abs and not target_abs.startswith(base_abs + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(str(target_path))


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


@app.get("/api/projects/{project_id}/reports/{dataset_id}/view")
def view_project_report(project_id: str, dataset_id: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    path, st = _secure_dataset_file(safe_project_id, dataset_id, report_only=True)
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=str(st.get("dataset_name") or path.name),
        content_disposition_type="inline",
    )


@app.get("/api/projects/{project_id}/reports/{dataset_id}/download")
def download_project_report(project_id: str, dataset_id: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    path, st = _secure_dataset_file(safe_project_id, dataset_id, report_only=True)
    return FileResponse(
        str(path),
        media_type="application/pdf",
        filename=str(st.get("dataset_name") or path.name),
        content_disposition_type="attachment",
    )


@app.get("/api/projects/{project_id}/datasets/{dataset_id}/raw/download")
def download_project_dataset_raw(project_id: str, dataset_id: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    path, st = _secure_dataset_file(safe_project_id, dataset_id, report_only=False)
    return FileResponse(
        str(path),
        filename=str(st.get("dataset_name") or path.name),
        content_disposition_type="attachment",
    )


@app.get("/api/projects/{project_id}/files")
def project_files(project_id: str, request: Request) -> dict[str, list[dict[str, str]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    base_url = str(request.base_url).rstrip("/")
    cached = _get_cached_project_files(safe_project_id)
    if cached is not None:
        return {"files": cached}

    jobs_by_file = {
        job.get("file_name", ""): job
        for job in _read_processing_jobs().get(safe_project_id, [])
        if isinstance(job, dict)
    }
    files: list[dict[str, str]] = []
    listed_rel_paths: set[str] = set()

    raw_dir_proj, processed_root = get_project_dirs(safe_project_id)
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
    raw_meta_by_rel: dict[str, dict[str, str]] = {}
    if jobs_root.is_dir():
        for job_dir in sorted([p for p in jobs_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            st = _read_dataset_status(safe_project_id, job_dir.name)
            if not st:
                continue
            raw_rel = str(st.get("raw_rel_path") or "").strip()
            if raw_rel:
                raw_meta_by_rel[raw_rel] = st
    legacy_raw = Path(LOCAL_DATA_PATH) / "raw_uploads"
    raw_suffixes = {".tif", ".tiff", ".las", ".laz", ".zip", ".pdf"}
    for raw_dir in (raw_dir_proj, legacy_raw):
        if not raw_dir.is_dir():
            continue
        raw_candidates = raw_dir.rglob("*") if raw_dir == raw_dir_proj else raw_dir.glob(f"{safe_project_id}__*")
        for file_path in sorted(raw_candidates, key=lambda p: p.name.lower()):
            if not file_path.is_file():
                continue
            if file_path.suffix.lower() not in raw_suffixes:
                continue
            rel_path = file_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel_path in listed_rel_paths:
                continue
            meta = raw_meta_by_rel.get(rel_path, {})
            display_name = str(meta.get("dataset_name") or file_path.name.replace(f"{safe_project_id}__", "", 1))
            dataset_type = str(meta.get("dataset_type") or _infer_dataset_type(display_name))
            is_report = dataset_type == "reports" or file_path.suffix.lower() == ".pdf"
            dataset_id_for_file = str(meta.get("dataset_id") or "")
            if is_report and dataset_id_for_file:
                file_url = f"{base_url}/api/projects/{safe_project_id}/reports/{dataset_id_for_file}/view"
                download_url = f"{base_url}/api/projects/{safe_project_id}/reports/{dataset_id_for_file}/download"
            elif dataset_id_for_file:
                file_url = f"{base_url}/api/projects/{safe_project_id}/datasets/{dataset_id_for_file}/raw/download"
                download_url = file_url
            else:
                file_url = f"{base_url}/data/{rel_path}"
                download_url = file_url
            files.append(
                {
                    "name": display_name,
                    "kind": "Reports" if is_report else "Raw Survey Data",
                    "type": "pdf" if is_report else file_path.suffix.lower().lstrip(".") or "file",
                    "size_bytes": str(file_path.stat().st_size),
                    "status": str(meta.get("status") or jobs_by_file.get(display_name, {}).get("status", "Raw")),
                    "updated_at": str(meta.get("updated_at") or ""),
                    "file_url": file_url,
                    "download_url": download_url,
                    "layer_url": "",
                    "file_path": str(file_path.resolve()),
                    "rel_path": rel_path,
                    "dataset_id": dataset_id_for_file,
                    "dataset_type": dataset_type,
                    "month": str(meta.get("month") or ""),
                    "raw_rel_path": rel_path,
                    **_dataset_extra_response_fields(meta),
                },
            )
            listed_rel_paths.add(rel_path)

    if jobs_root.is_dir():
        for job_dir in sorted([p for p in jobs_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            st = _read_dataset_status(safe_project_id, job_dir.name)
            if not st:
                continue
            tile_folder = (st.get("tile_folder") or "").strip()
            raw_rel_path = (st.get("raw_rel_path") or "").strip()
            display_name = str(st.get("dataset_name") or job_dir.name)
            if str(st.get("dataset_type") or "").lower() == "csv" and raw_rel_path:
                csv_path = Path(LOCAL_DATA_PATH) / raw_rel_path
                if csv_path.is_file():
                    files.append(
                        {
                            "name": display_name,
                            "kind": "Analysis CSV",
                            "type": "csv",
                    "size_bytes": str(csv_path.stat().st_size),
                    "status": "Web-Ready",
                    "updated_at": str(st.get("updated_at") or ""),
                            "file_url": f"{base_url}/data/{raw_rel_path}",
                            "layer_url": "",
                            "file_path": str(csv_path.resolve()),
                            "rel_path": raw_rel_path,
                            "dataset_id": str(st.get("dataset_id") or job_dir.name),
                            "dataset_type": "csv",
                            "month": str(st.get("month") or ""),
                            "raw_rel_path": raw_rel_path,
                            **_dataset_extra_response_fields(st),
                        },
                    )
                    listed_rel_paths.add(raw_rel_path)
                continue
            if str(st.get("dataset_type") or "").lower() in ("vector", "cad"):
                vector_rel = str(st.get("vector_rel_path") or raw_rel_path).strip()
                vector_path = Path(LOCAL_DATA_PATH) / vector_rel if vector_rel else None
                if vector_path and vector_path.is_file():
                    dtype = str(st.get("dataset_type") or "").lower()
                    files.append(
                        {
                            "name": display_name,
                            "kind": "CAD Asset" if dtype == "cad" else "Vector GIS Layer",
                            "type": "CAD" if dtype == "cad" else "Vector",
                            "size_bytes": str(vector_path.stat().st_size),
                            "status": str(st.get("status") or "WEB-READY"),
                            "updated_at": str(st.get("updated_at") or ""),
                            "file_url": f"{base_url}/data/{vector_rel}",
                            "layer_url": "" if dtype == "cad" else f"{base_url}/data/{vector_rel}",
                            "file_path": str(vector_path.resolve()),
                            "rel_path": vector_rel,
                            "dataset_id": str(st.get("dataset_id") or job_dir.name),
                            "dataset_type": dtype,
                            "month": str(st.get("month") or ""),
                            "raw_rel_path": raw_rel_path,
                            **_dataset_extra_response_fields(st),
                        },
                    )
                    listed_rel_paths.add(vector_rel)
                continue
            cog_rel_path = str(st.get("cog_rel_path") or "").strip()
            cog_path = Path(str(st.get("cog_path") or "")).resolve() if str(st.get("cog_path") or "").strip() else None
            if cog_rel_path and (not cog_path or not cog_path.is_file()):
                cog_path = (Path(LOCAL_DATA_PATH) / cog_rel_path).resolve()
            if cog_path and cog_path.is_file():
                rel_base = cog_path.relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
                layer_type = str(st.get("layer_type") or _raster_layer_type(str(st.get("dataset_type") or ""), display_name))
                layer_url = _titiler_tile_url_template(
                    base_url,
                    str(cog_path),
                    layer_type,
                    str(st.get("rescale_min") or ""),
                    str(st.get("rescale_max") or ""),
                )
                files.append(
                    {
                        "name": display_name,
                        "kind": "Web-Optimized Data",
                        "type": "cog",
                        "layer_type": layer_type,
                        "size_bytes": str(cog_path.stat().st_size),
                        "status": str(st.get("status") or jobs_by_file.get(display_name, {}).get("status", "Web-Ready")),
                        "updated_at": str(st.get("updated_at") or ""),
                        "file_url": f"{base_url}/data/{rel_base}",
                        "layer_url": layer_url,
                        "file_path": str(cog_path),
                        "rel_path": rel_base,
                        "dataset_id": str(st.get("dataset_id") or job_dir.name),
                        "dataset_type": str(st.get("dataset_type") or _infer_dataset_type(display_name)),
                        "month": str(st.get("month") or ""),
                        "raw_rel_path": raw_rel_path,
                        **_dataset_extra_response_fields(st),
                    },
                )
                listed_rel_paths.add(rel_base)
                continue
            if not tile_folder:
                continue
            tiles_rel_path = str(st.get("tiles_rel_path") or "").strip()
            tile_root = Path(LOCAL_DATA_PATH) / tiles_rel_path if tiles_rel_path else processed_root / tile_folder
            if str(st.get("dataset_type") or "").lower() in ("3dmodel", "3dtiles"):
                tileset_path = _find_tileset_json(tile_root)
                if not tileset_path:
                    continue
                tileset_path = _ensure_tileset_alias(tileset_path)
                tile_root = tileset_path.parent
                rel_base = tile_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
                tileset_rel = tileset_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
                files.append(
                    {
                        "name": display_name,
                        "kind": "3D Photogrammetry Model",
                        "type": "3DModel",
                        "size_bytes": str(get_dir_size(tile_root)),
                        "status": str(st.get("status") or jobs_by_file.get(display_name, {}).get("status", "WEB-READY")),
                        "updated_at": str(st.get("updated_at") or ""),
                        "file_url": f"{base_url}/data/{tileset_rel}",
                        "layer_url": f"{base_url}/data/{tileset_rel}",
                        "file_path": str(tileset_path.resolve()),
                        "rel_path": rel_base,
                        "dataset_id": str(st.get("dataset_id") or job_dir.name),
                        "dataset_type": "3dmodel",
                        "month": str(st.get("month") or ""),
                        "raw_rel_path": raw_rel_path,
                        **_dataset_extra_response_fields(st),
                    },
                )
                listed_rel_paths.add(rel_base)
                listed_rel_paths.add(tileset_rel)
                continue
            if not _is_valid_tile_dataset(tile_root) and tile_folder:
                typed_root = processed_root / _dataset_type_folder(str(st.get("dataset_type") or "")) / tile_folder
                if _is_valid_tile_dataset(typed_root):
                    tile_root = typed_root
            if not _is_valid_tile_dataset(tile_root):
                continue
            rel_base = tile_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            layer_url = f"{base_url}/data/{rel_base}/{{z}}/{{x}}/{{y}}.png"
            files.append(
                {
                    "name": display_name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "layer_type": _raster_layer_type(str(st.get("dataset_type") or ""), display_name),
                    "size_bytes": str(get_dir_size(tile_root)),
                    "status": str(st.get("status") or jobs_by_file.get(display_name, {}).get("status", "Web-Ready")),
                    "updated_at": str(st.get("updated_at") or ""),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(tile_root.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": str(st.get("dataset_id") or job_dir.name),
                    "dataset_type": str(st.get("dataset_type") or _infer_dataset_type(display_name)),
                    "month": str(st.get("month") or ""),
                    "raw_rel_path": str(st.get("raw_rel_path") or ""),
                    **_dataset_extra_response_fields(st),
                },
            )
            listed_rel_paths.add(rel_base)

    # Include manual processed folders even when not synced/tracked yet.
    if processed_root.is_dir():
        for cog_file in sorted(
            [*processed_root.glob("*/*.cog.tif"), *processed_root.glob("*/*.cog.tiff")],
            key=lambda p: p.name.lower(),
        ):
            rel = cog_file.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths:
                continue
            display_name = cog_file.name.replace(".cog.tif", ".tif").replace(".cog.tiff", ".tiff")
            layer_type = _raster_layer_type(_infer_dataset_type(display_name), display_name)
            manual_metadata = _read_raster_manual_metadata(cog_file, _infer_dataset_type(display_name))
            files.append(
                {
                    "name": display_name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "layer_type": layer_type,
                    "size_bytes": str(cog_file.stat().st_size),
                    "status": "Web-Ready",
                    "updated_at": datetime.fromtimestamp(cog_file.stat().st_mtime, timezone.utc).isoformat(),
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": _titiler_tile_url_template(base_url, str(cog_file.resolve()), layer_type),
                    "file_path": str(cog_file.resolve()),
                    "rel_path": rel,
                    "dataset_id": cog_file.parent.name,
                    "dataset_type": _infer_dataset_type(display_name),
                    "month": "",
                    "raw_rel_path": "",
                    "cog_path": str(cog_file.resolve()),
                    "cog_rel_path": rel,
                    "rescale_min": str(manual_metadata.get("rescale_min") or ""),
                    "rescale_max": str(manual_metadata.get("rescale_max") or ""),
                    "bounds_wgs84": str(manual_metadata.get("bounds_wgs84") or ""),
                    "source_crs": str(manual_metadata.get("source_crs") or ""),
                    "detected_epsg": str(manual_metadata.get("detected_epsg") or ""),
                },
            )
            listed_rel_paths.add(rel)

        for vector_path in sorted(
            [*processed_root.glob("*/*.kml"), *processed_root.glob("*/*.geojson")],
            key=lambda p: p.name.lower(),
        ):
            rel = vector_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths:
                continue
            files.append(
                {
                    "name": vector_path.name,
                    "kind": "Vector GIS Layer",
                    "type": "Vector",
                    "size_bytes": str(vector_path.stat().st_size),
                    "status": "WEB-READY",
                    "updated_at": datetime.fromtimestamp(vector_path.stat().st_mtime, timezone.utc).isoformat(),
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": f"{base_url}/data/{rel}",
                    "file_path": str(vector_path.resolve()),
                    "rel_path": rel,
                    "dataset_id": vector_path.parent.name,
                    "dataset_type": "vector",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel)

        for potree_html in sorted(processed_root.glob("*/*.html"), key=lambda p: p.parent.name.lower()):
            rel = potree_html.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            rel_base = potree_html.parent.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths or rel_base in listed_rel_paths:
                continue
            source_name = ""
            source_marker = potree_html.parent / ".source_name.txt"
            if source_marker.is_file():
                try:
                    source_name = source_marker.read_text(encoding="utf-8").strip()
                except OSError:
                    source_name = ""
            display_name = source_name or f"{potree_html.parent.name}.las"
            files.append(
                {
                    "name": display_name,
                    "kind": "Droid 3D Point Cloud",
                    "type": "PointCloud",
                    "size_bytes": str(get_dir_size(potree_html.parent)),
                    "status": jobs_by_file.get(display_name, {}).get("status", "WEB-READY"),
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": f"{base_url}/data/{rel}",
                    "file_path": str(potree_html.resolve()),
                    "rel_path": rel,
                    "dataset_id": potree_html.parent.name,
                    "dataset_type": "pointcloud",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel)
            listed_rel_paths.add(rel_base)

        for model_root in _candidate_processed_model_dirs(processed_root):
            tileset_path = _find_tileset_json(model_root)
            if not tileset_path:
                continue
            tileset_path = _ensure_tileset_alias(tileset_path)
            model_root = tileset_path.parent
            display_name = _display_model_folder_name(model_root, processed_root)
            rel_base = model_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            tileset_rel = tileset_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel_base in listed_rel_paths or tileset_rel in listed_rel_paths:
                continue
            files.append(
                {
                    "name": display_name,
                    "kind": "3D Photogrammetry Model",
                    "type": "3DModel",
                    "size_bytes": str(get_dir_size(model_root)),
                    "status": "WEB-READY",
                    "file_url": f"{base_url}/data/{tileset_rel}",
                    "layer_url": f"{base_url}/data/{tileset_rel}",
                    "file_path": str(tileset_path.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": "",
                    "dataset_type": "3dmodel",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel_base)
            listed_rel_paths.add(tileset_rel)

        for tile_root in _candidate_processed_tile_dirs(processed_root):
            if _is_3d_model_dataset(tile_root):
                continue
            rel_base = tile_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel_base in listed_rel_paths:
                continue
            layer_url = f"{base_url}/data/{rel_base}/{{z}}/{{x}}/{{y}}.png"
            files.append(
                {
                    "name": tile_root.name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "layer_type": _raster_layer_type(_infer_dataset_type(tile_root.name), tile_root.name),
                    "size_bytes": str(get_dir_size(tile_root)),
                    "status": "Web-Ready",
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(tile_root.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": "",
                    "dataset_type": _infer_dataset_type(tile_root.name),
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel_base)

    dataset_root = Path(LOCAL_DATA_PATH) / "datasets" / safe_project_id
    if dataset_root.is_dir():
        for ds_dir in sorted([p for p in dataset_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            webtiles = ds_dir / "webtiles"
            if not webtiles.is_dir():
                continue
            if not any(d.is_dir() and d.name.isdigit() for d in webtiles.iterdir()):
                continue
            st: dict[str, str] = {}
            legacy_st = ds_dir / ".status.json"
            if legacy_st.is_file():
                try:
                    loaded = json.loads(legacy_st.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        st = {str(k): str(v) for k, v in loaded.items()}
                except (OSError, json.JSONDecodeError, TypeError):
                    st = {}
            display_name = str(st.get("dataset_name") or f"{ds_dir.name}.tif")
            rel_base = webtiles.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            layer_url = f"{base_url}/data/{rel_base}/{{z}}/{{x}}/{{y}}.png"
            files.append(
                {
                    "name": display_name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "size_bytes": str(get_dir_size(webtiles)),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(webtiles.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": str(st.get("dataset_id") or ds_dir.name),
                    "dataset_type": str(st.get("dataset_type") or _infer_dataset_type(display_name)),
                    "month": str(st.get("month") or ""),
                    "raw_rel_path": str(st.get("raw_rel_path") or ""),
                    **_dataset_extra_response_fields(st),
                },
            )
            listed_rel_paths.add(rel_base)

    pointcloud_root = Path(LOCAL_DATA_PATH) / "pointclouds" / safe_project_id
    if pointcloud_root.is_dir():
        for tileset in sorted(pointcloud_root.rglob("tileset.json"), key=lambda p: p.parent.name.lower()):
            rel = tileset.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            file_name = f"{tileset.parent.name}.las"
            files.append(
                {
                    "name": file_name,
                    "kind": "Web-Optimized Data",
                    "type": "pointcloud",
                    "size_bytes": str(get_dir_size(tileset.parent)),
                    "status": jobs_by_file.get(file_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": f"{base_url}/data/{rel}",
                    "file_path": str(tileset.resolve()),
                    "rel_path": rel,
                    "dataset_id": tileset.parent.name,
                    "dataset_type": "pointcloud",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel)

    reports_dir = Path(LOCAL_DATA_PATH) / "reports" / safe_project_id
    if reports_dir.is_dir():
        for report in sorted(reports_dir.rglob("*.pdf"), key=lambda p: p.name.lower()):
            rel = report.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            report_id = re.sub(r"[^A-Za-z0-9._-]+", "-", report.stem).strip("-")[:180] or "report"
            files.append(
                {
                    "name": report.name,
                    "kind": "Reports",
                    "type": "pdf",
                    "size_bytes": str(report.stat().st_size),
                    "status": "Completed",
                    "file_url": f"{base_url}/api/projects/{safe_project_id}/reports/{report_id}/view",
                    "download_url": f"{base_url}/api/projects/{safe_project_id}/reports/{report_id}/download",
                    "layer_url": "",
                    "file_path": str(report.resolve()),
                    "rel_path": rel,
                    "dataset_id": report_id,
                },
            )
            listed_rel_paths.add(rel)

    export_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "exports" / "grid"
    if export_root.is_dir():
        project_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id
        for export_file in sorted(export_root.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if not export_file.is_file() or export_file.suffix.lower() not in {".csv", ".dxf"}:
                continue
            rel = export_file.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths:
                continue
            metadata: dict[str, str] = {}
            metadata_path = export_file.with_suffix(f"{export_file.suffix}.json")
            if metadata_path.is_file():
                try:
                    loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        metadata = {str(k): str(v) for k, v in loaded.items()}
                except (OSError, json.JSONDecodeError, TypeError):
                    metadata = {}
            project_rel = export_file.relative_to(project_root).as_posix()
            file_url = f"{base_url}/api/data/projects/{safe_project_id}/{project_rel}"
            files.append(
                {
                    "name": str(metadata.get("name") or export_file.name),
                    "kind": "Generated Grid Export",
                    "type": export_file.suffix.lower().lstrip("."),
                    "size_bytes": str(export_file.stat().st_size),
                    "status": "Web-Ready",
                    "updated_at": datetime.fromtimestamp(export_file.stat().st_mtime, timezone.utc).isoformat(),
                    "file_url": file_url,
                    "download_url": file_url,
                    "layer_url": "",
                    "file_path": str(export_file.resolve()),
                    "rel_path": rel,
                    "dataset_id": str(metadata.get("dataset_id") or export_file.parent.name),
                    "dataset_type": "grid_export",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel)

    _set_cached_project_files(safe_project_id, files)
    return {"files": files}


@app.delete("/api/projects/{project_id}/files")
def delete_project_file(project_id: str, payload: FileDeletePayload, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    rel = (payload.rel_path or "").replace("\\", "/").lstrip("/")
    if ".." in rel:
        raise HTTPException(status_code=400, detail="Invalid rel_path")
    target = (Path(LOCAL_DATA_PATH) / rel).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if local_root not in target.parents and target != local_root:
        raise HTTPException(status_code=400, detail="Invalid target path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    matched = _find_dataset_status_for_rel(safe_project_id, rel)
    if matched:
        dataset_id, st = matched
        _delete_dataset_artifacts(safe_project_id, dataset_id, st)
        return {"status": "success"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.delete("/api/admin/projects/{project_id}/files")
def admin_force_delete_project_file(
    project_id: str,
    payload: FileDeletePayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    safe_project_id = _safe_project_id(project_id)
    rel = (payload.rel_path or "").replace("\\", "/").lstrip("/")
    if ".." in rel:
        raise HTTPException(status_code=400, detail="Invalid rel_path")
    target = (Path(LOCAL_DATA_PATH) / rel).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if local_root not in target.parents and target != local_root:
        raise HTTPException(status_code=400, detail="Invalid target path")
    if not target.exists():
        raise HTTPException(status_code=404, detail="File not found")
    matched = _find_dataset_status_for_rel(safe_project_id, rel)
    if matched:
        dataset_id, st = matched
        _delete_dataset_artifacts(safe_project_id, dataset_id, st)
        return {"status": "success"}
    if target.is_dir():
        shutil.rmtree(target)
    else:
        target.unlink(missing_ok=True)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/version")
def api_version() -> dict[str, str]:
    return {
        "version": PORTAL_VERSION,
        "dev_mode": "true" if str(os.getenv("DEV_MODE", "")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "manual_bulk_import": "true",
        "locate_folder": "true" if os.name == "nt" else "false",
    }
