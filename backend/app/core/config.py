"""
Centralized configuration and environment variables for the Droid Survair Cloud Portal backend.

All constants, env-var reads, and path helpers live here so the rest of the
codebase can simply ``from app.core.config import X``.
"""

from __future__ import annotations

import os
import secrets
import time
from pathlib import Path

# ---------------------------------------------------------------------------
# Project root (repo root containing backend/, frontend/, Project_Data/)
# ---------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent


def _load_local_env_file() -> None:
    """Read backend/.env into os.environ (does NOT override existing vars except force_keys)."""
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
            force_keys = {"POTREE_NATIVE_COPC_ENABLED", "PDAL_EXE"}
            if key and (key not in os.environ or key in force_keys):
                os.environ[key] = value
    except OSError as exc:
        print(f"Could not load backend .env: {exc}")


_load_local_env_file()

# ---------------------------------------------------------------------------
# Data paths
# ---------------------------------------------------------------------------
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
# through authenticated routes. Do not mount Project_Data as StaticFiles.
DATABASE_DIR = Path(LOCAL_DATA_PATH)
ERROR_LOG_DIR = Path(LOCAL_DATA_PATH) / "logs"

# ---------------------------------------------------------------------------
# Upload / disk
# ---------------------------------------------------------------------------
# Large uploads: headroom above merged file size (e.g. 14GB LAS + merge buffer).
DISK_HEADROOM_BYTES = int(os.getenv("UPLOAD_DISK_HEADROOM_MB", "512")) * 1024 * 1024
MERGE_COPY_BUFFER_BYTES = 8 * 1024 * 1024  # streaming merge, avoid loading whole file in RAM
DIRECT_RASTER_UPLOAD_LIMIT_BYTES = 1024 * 1024 * 1024

# ---------------------------------------------------------------------------
# Point cloud configuration
# ---------------------------------------------------------------------------
POINTCLOUD_SRS_IN = os.getenv("POINTCLOUD_SRS_IN", "").strip()
POINTCLOUD_SRS_OUT = os.getenv("POINTCLOUD_SRS_OUT", "4978").strip()
POINTCLOUD_EPT_PROJECT_GEOGRAPHIC = os.getenv("POINTCLOUD_EPT_PROJECT_GEOGRAPHIC", "true").strip().lower() not in {"0", "false", "no", "off"}
POINTCLOUD_EPT_TARGET_EPSG = os.getenv("POINTCLOUD_EPT_TARGET_EPSG", "").strip()
OSGEO4W_BAT = os.getenv(
    "OSGEO4W_BAT",
    r"C:\Program Files\QGIS 3.44.8\OSGeo4W.bat",
).strip()
UNTWINE_EXE = os.getenv(
    "UNTWINE_EXE",
    r"C:\Program Files\QGIS 3.22.8\apps\qgis-ltr\untwine.exe",
).strip()
PDAL_EXE = os.getenv(
    "PDAL_EXE",
    r"C:\Program Files\QGIS 3.22.8\bin\pdal.exe",
).strip()
POTREE_NATIVE_COPC_ENABLED = os.getenv("POTREE_NATIVE_COPC_ENABLED", "true").strip().lower() in {
    "1",
    "true",
    "yes",
    "on",
}
print(
    f"[pointcloud] POTREE_NATIVE_COPC_ENABLED={POTREE_NATIVE_COPC_ENABLED} "
    f"PDAL_EXE={PDAL_EXE}"
)

# ---------------------------------------------------------------------------
# Tiling configuration
# ---------------------------------------------------------------------------
PROJECT_FILES_CACHE_TTL_SECONDS = float(os.getenv("PROJECT_FILES_CACHE_TTL_SECONDS", "15"))
TIFF_TILE_BUDGET_MB = float(os.getenv("TIFF_TILE_BUDGET_MB", "100"))
TIFF_TILE_MIN_ZOOM_LIMIT = int(os.getenv("TIFF_TILE_MIN_ZOOM_LIMIT", "14"))
TIFF_TILE_MAX_ZOOM_LIMIT = int(os.getenv("TIFF_TILE_MAX_ZOOM_LIMIT", "19"))
TIFF_TILE_SIZE = int(os.getenv("TIFF_TILE_SIZE", "256"))

# ---------------------------------------------------------------------------
# Session / auth
# ---------------------------------------------------------------------------
SESSION_COOKIE_NAME = "droid_cloud_session"
SESSION_TTL_SECONDS = max(86_400, int(os.getenv("SESSION_TTL_SECONDS", "604800")))
SESSION_RENEW_GRACE_SECONDS = max(86_400, int(os.getenv("SESSION_RENEW_GRACE_SECONDS", "604800")))
SESSION_REFRESH_THRESHOLD_SECONDS = max(3_600, int(os.getenv("SESSION_REFRESH_THRESHOLD_SECONDS", "86400")))
SESSION_AUTH_CACHE_SECONDS = max(5, int(os.getenv("SESSION_AUTH_CACHE_SECONDS", "30")))
SESSION_SECRET_FILE = Path(LOCAL_DATA_PATH) / ".session_signing_secret"
_SESSION_USER_CACHE: dict[str, tuple[float, int, dict[str, object]]] = {}


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

# ---------------------------------------------------------------------------
# Contact / notification (defaults are placeholders — real values in .env)
# ---------------------------------------------------------------------------
OWNER_APPROVAL_EMAIL = os.getenv("OWNER_APPROVAL_EMAIL", "admin@example.com").strip()
ADMIN_ALERT_PHONE = os.getenv("ADMIN_ALERT_PHONE", "").strip()
PUBLIC_PORTAL_URL = os.getenv("PUBLIC_PORTAL_URL", "https://portal.droidminingsolutions.com").strip()
PORTAL_VERSION = os.getenv("PORTAL_VERSION", f"local-{int(time.time())}").strip()

# ---------------------------------------------------------------------------
# CORS / frontend origins
# ---------------------------------------------------------------------------
FRONTEND_ORIGINS = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
    "http://localhost:3000",
    "https://portal.droidminingsolutions.com",
]

# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------
RATE_LIMIT_WINDOW_SECONDS = 60.0
RATE_LIMIT_HEAVY_REQUESTS = 5
_RATE_LIMIT_BUCKETS: dict[str, list[float]] = {}

# ---------------------------------------------------------------------------
# Runtime caches (mutable module-level state)
# ---------------------------------------------------------------------------
_PROJECT_FILES_CACHE: dict[str, tuple[float, list[dict[str, str]]]] = {}

# ---------------------------------------------------------------------------
# Study metrics placeholders for report/demo API responses (PDF scope 964 Acres).
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# File type constants
# ---------------------------------------------------------------------------
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
