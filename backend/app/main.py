import asyncio
import base64
import hashlib
import hmac
import os
import platform
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path
import sqlite3
import json
from datetime import datetime, timezone
from importlib.util import find_spec
from urllib.parse import quote

from fastapi import (
    BackgroundTasks,
    FastAPI,
    File,
    Form,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
import laspy
from PIL import Image
from pydantic import BaseModel
from titiler.core.factory import TilerFactory

# Project_Data lives beside backend/ and frontend/ (repo root).
BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROJECT_DATA = BASE_DIR / "Project_Data"
LOCAL_DATA_PATH = os.getenv("LOCAL_DATA_PATH", str(_DEFAULT_PROJECT_DATA))

# Map tiles, ortho, DEM, terrain quantized-mesh, videos, etc.
# - `/data` — preferred URL prefix for files under Project_Data (see StaticFiles mount).
# - `/tiles` — same directory, kept for flood/media and older clients.
ISSUES_DB_PATH = Path(LOCAL_DATA_PATH) / "issues.db"

Path(LOCAL_DATA_PATH).mkdir(parents=True, exist_ok=True)

# Large uploads: headroom above merged file size (e.g. 14GB LAS + merge buffer).
DISK_HEADROOM_BYTES = int(os.getenv("UPLOAD_DISK_HEADROOM_MB", "512")) * 1024 * 1024
MERGE_COPY_BUFFER_BYTES = 8 * 1024 * 1024  # streaming merge, avoid loading whole file in RAM
POINTCLOUD_SRS_IN = os.getenv("POINTCLOUD_SRS_IN", "").strip()
POINTCLOUD_SRS_OUT = os.getenv("POINTCLOUD_SRS_OUT", "4978").strip()
OSGEO4W_BAT = os.getenv(
    "OSGEO4W_BAT",
    r"C:\Program Files\QGIS 3.44.8\OSGeo4W.bat",
).strip()
PROJECT_FILES_CACHE_TTL_SECONDS = float(os.getenv("PROJECT_FILES_CACHE_TTL_SECONDS", "4"))
SESSION_COOKIE_NAME = "droid_cloud_session"
SESSION_TTL_SECONDS = int(os.getenv("SESSION_TTL_SECONDS", "604800"))
SESSION_SECRET_FILE = Path(LOCAL_DATA_PATH) / ".session_signing_secret"


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
    origin.strip()
    for origin in os.getenv(
        "FRONTEND_ORIGINS",
        "http://localhost:5173,http://127.0.0.1:5173",
    ).split(",")
    if origin.strip()
]

app = FastAPI(
    title="Hydrology & Mapping Portal API",
    description="Backend services for hydrology data and mapping.",
    version="0.1.0",
)
cog_tiler = TilerFactory()
if find_spec("multipart") is None:
    print(
        "WARNING: python-multipart is not installed. "
        "Upload endpoints may fail with 422/validation errors.",
    )

app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(
    cog_tiler.router,
    prefix="/api/cog",
    tags=["COG"],
)

app.mount(
    "/tiles",
    StaticFiles(directory=LOCAL_DATA_PATH),
    name="local-tiles",
)
app.mount(
    "/data",
    StaticFiles(directory=str(LOCAL_DATA_PATH)),
    name="project-data",
)
# Static files under Project_Data, e.g.:
# /data/projects/{project_id}/processed/{tile_folder}/{z}/{x}/{y}.png
# /data/pointclouds/{project_id}/{tileset_id}/tileset.json

# Study metrics (mirrors frontend HydrologyStats placeholders; PDF scope 964 Acres).
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


class PointCloudProcessPayload(BaseModel):
    filename: str
    project_id: str = "default-project"


class CompleteUploadPayload(BaseModel):
    filename: str
    totalChunks: int
    project_id: str = "default-project"


class AuthPayload(BaseModel):
    email: str
    password: str


class ProjectCreatePayload(BaseModel):
    name: str
    location: str
    date: str
    status: str
    type: str


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
    if suffix not in (".tif", ".tiff", ".las", ".laz"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.las/.laz files are supported")
    return base


def _upload_session_dir(filename: str, total_chunks: int, project_id: str) -> Path:
    """Stable temp folder for one logical upload (same as frontend chunk sequence)."""
    safe_name = _safe_pointcloud_basename(filename)
    digest = hashlib.sha256(
        f"{project_id}\0{safe_name}\0{total_chunks}".encode("utf-8"),
    ).hexdigest()
    return Path(LOCAL_DATA_PATH) / "uploads" / "chunks" / digest


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


def _run_gdal2tiles_subprocess(input_tif: str, output_dir: str) -> None:
    """Run gdal2tiles via QGIS OSGeo4W shell (blocking; call from asyncio.to_thread)."""
    in_abs = os.path.abspath(input_tif)
    out_abs = os.path.abspath(output_dir)
    os.makedirs(out_abs, exist_ok=True)
    bat = OSGEO4W_BAT
    command = [
        bat,
        "gdal2tiles",
        "-z",
        "1-22",
        "-w",
        "none",
        in_abs,
        out_abs,
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode == 0:
        return
    if platform.system() == "Windows":
        inner = f'gdal2tiles -z 1-22 -w none "{in_abs}" "{out_abs}"'
        cmdline = f'call "{bat}" {inner}'
        result2 = subprocess.run(
            cmdline,
            shell=True,
            capture_output=True,
            text=True,
            executable=os.environ.get("COMSPEC", "cmd.exe"),
        )
        if result2.returncode == 0:
            return
        msg2 = (result2.stderr or result2.stdout or "").strip()
        msg1 = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(msg2 or msg1 or "gdal2tiles failed")
    msg = (result.stderr or result.stdout or "").strip()
    raise RuntimeError(msg or "gdal2tiles failed")


async def process_tif_to_tiles(input_tif: str, output_dir: str) -> None:
    await asyncio.to_thread(_run_gdal2tiles_subprocess, input_tif, output_dir)


async def process_dataset_background(
    project_id: str,
    dataset_id: str,
    input_tif: str,
    file_name: str | None,
    tile_output_dir: str,
    tile_folder: str,
) -> None:
    tiles_dir = str(Path(tile_output_dir).resolve())
    _write_dataset_status(
        project_id,
        dataset_id,
        {
            "status": "Generating XYZ tiles",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": file_name or Path(input_tif).name,
            "tile_folder": tile_folder,
        },
    )
    err_path = _dataset_dir(project_id, dataset_id) / ".conversion_error.txt"
    err_path.unlink(missing_ok=True)
    try:
        await process_tif_to_tiles(input_tif, tiles_dir)
        tiles_rel = Path(tiles_dir).resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": file_name or Path(input_tif).name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{tiles_rel}",
            },
        )
        _invalidate_project_files_cache(project_id)
        _write_dataset_status(
            project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": file_name or Path(input_tif).name,
                "tile_folder": tile_folder,
                "tiles_rel_path": tiles_rel,
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
                "status": "Failed",
                "error": msg[:8000],
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": file_name or Path(input_tif).name,
                "tile_folder": tile_folder,
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


def _safe_project_id(project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", project_id or ""):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    return project_id


def get_project_dirs(project_id: str) -> tuple[Path, Path]:
    """Per-project raw uploads and gdal2tiles output under Project_Data/projects."""
    safe = _safe_project_id(project_id)
    project_dir = Path(LOCAL_DATA_PATH) / "projects" / safe
    raw_dir = project_dir / "raw"
    processed_dir = project_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir


def _safe_tileset_id(tileset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", tileset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid tileset_id")
    return tileset_id


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


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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


def _require_user(request: Request) -> dict[str, str | int]:
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
            SELECT u.id AS user_id, u.email
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ? AND s.expires_at >= ?
            """,
            (_token_hash(raw), now_ts),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    return {"id": int(row["user_id"]), "email": str(row["email"])}


def _ensure_project_owner(user_id: int, project_id: str) -> None:
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
    Poll conversion progress: tileset.json appears when py3dtiles finishes.
    If conversion fails, .conversion_error.txt is written under the output folder.
    """
    user = _require_user(request)
    safe_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_id)
    base_url = str(request.base_url).rstrip("/")
    project_root = Path(LOCAL_DATA_PATH) / "pointclouds" / safe_id

    candidates: list[Path] = []
    if tileset_id:
        safe_tileset_id = _safe_tileset_id(tileset_id)
        candidates.append(project_root / safe_tileset_id)
    else:
        if (project_root / "tileset.json").is_file():
            candidates.append(project_root)
        if project_root.is_dir():
            children = sorted(
                [p for p in project_root.iterdir() if p.is_dir()],
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            candidates.extend(children)

    for candidate in candidates:
        tileset = candidate / "tileset.json"
        err_file = candidate / ".conversion_error.txt"
        if err_file.is_file():
            try:
                msg = err_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                msg = "Unknown conversion error"
            suffix = (
                f"/{candidate.name}" if candidate.resolve() != project_root.resolve() else ""
            )
            return {
                "ready": False,
                "failed": True,
                "error": msg[:8000],
                "tileset_url": f"{base_url}/data/pointclouds/{safe_id}{suffix}/tileset.json",
            }
        if tileset.is_file():
            suffix = (
                f"/{candidate.name}" if candidate.resolve() != project_root.resolve() else ""
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
        "tileset_url": f"{base_url}/data/pointclouds/{safe_id}{pending_suffix}/tileset.json",
    }


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(ISSUES_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_tables() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY,
                lat REAL,
                lng REAL,
                title TEXT,
                description TEXT,
                status TEXT DEFAULT 'open'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.commit()


@app.on_event("startup")
def startup() -> None:
    ensure_tables()


@app.post("/api/auth/signup")
def auth_signup(payload: AuthPayload, response: Response) -> dict[str, str]:
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    password_hash = _hash_password(payload.password)
    created_at = _now_iso()
    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, password_hash, created_at),
            )
            user_id = int(cursor.lastrowid)
            connection.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc

    raw_token = secrets.token_urlsafe(48)
    expires_at = int(datetime.now(timezone.utc).timestamp()) + SESSION_TTL_SECONDS
    with get_db_connection() as connection:
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(raw_token), user_id, expires_at, created_at),
        )
        connection.commit()
    _set_session_cookie(response, raw_token)
    return {"status": "success", "email": email}


@app.post("/api/auth/login")
def auth_login(payload: AuthPayload, response: Response) -> dict[str, str]:
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if not row or not _verify_password(payload.password, str(row["password_hash"])):
        raise HTTPException(status_code=401, detail="Invalid email or password")

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
    return {"status": "success", "email": email}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    raw = _unsign_session_token(signed) if signed else None
    if raw:
        with get_db_connection() as connection:
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(raw),))
            connection.commit()
    _clear_session_cookie(response)
    return {"status": "success"}


@app.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, str | int]:
    user = _require_user(request)
    return {"id": int(user["id"]), "email": str(user["email"])}


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


@app.get("/api/project-stats")
def project_stats() -> dict[str, list]:
    return {
        "catchment_stats": CATCHMENT_STATS,
        "stream_stats": STREAM_STATS,
        "lulc_rows": LULC_ROWS,
    }


@app.get("/api/hydrology-stats")
def hydrology_stats() -> dict[str, list]:
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
async def process_pointcloud(payload: PointCloudProcessPayload, request: Request) -> dict[str, str]:
    user = _require_user(request)
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
    user = _require_user(request)
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
    with open(part_path, "wb") as dest:
        shutil.copyfileobj(chunk.file, dest, length=MERGE_COPY_BUFFER_BYTES)

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
    user = _require_user(request)
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
    output_dir = Path(LOCAL_DATA_PATH) / "pointclouds" / safe_project_id / reused_tileset_id
    final_path = out_path
    hash_marker = output_dir / ".source_hash.txt"
    existing_hash = None
    try:
        if hash_marker.is_file():
            existing_hash = hash_marker.read_text(encoding="utf-8").strip()
    except OSError:
        existing_hash = None

    if (output_dir / "tileset.json").is_file() and existing_hash == content_hash:
        final_path.unlink(missing_ok=True)
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": reused_tileset_id,
                "kind": "pointcloud",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/pointclouds/{safe_project_id}/{reused_tileset_id}/tileset.json",
            },
        )
        return {
            "status": "success",
            "message": (
                f"Merged {total} chunks into {safe_name}. "
                "Found existing converted tiles for same file content; reusing project tiles."
            ),
            "tileset_url": "PENDING",
            "project_id": safe_project_id,
            "target_tileset_url": (
                f"{str(request.base_url).rstrip('/')}/data/pointclouds/"
                f"{safe_project_id}/{reused_tileset_id}/tileset.json"
            ),
            "tileset_id": reused_tileset_id,
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
            "job_id": reused_tileset_id,
            "kind": "pointcloud",
            "file_name": safe_name,
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_pointcloud_background,
        final_path,
        output_dir,
        safe_project_id,
        reused_tileset_id,
        safe_name,
    )

    return {
        "status": "success",
        "message": "File merged. 3D Tile processing started in background.",
        "tileset_url": "PENDING",
        "project_id": safe_project_id,
        "target_tileset_url": (
            f"{str(request.base_url).rstrip('/')}/data/pointclouds/"
            f"{safe_project_id}/{reused_tileset_id}/tileset.json"
        ),
        "tileset_id": reused_tileset_id,
    }


@app.post("/api/process-dataset", response_model=ProcessDatasetOut)
async def process_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> ProcessDatasetOut:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_tif_basename(file.filename or "")

    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")

    raw_dir, processed_dir = get_project_dirs(safe_project_id)
    meta_dir = _dataset_dir(safe_project_id, dataset_id)
    meta_dir.mkdir(parents=True, exist_ok=True)

    ext = Path(safe_name).suffix.lower()
    if ext not in (".tif", ".tiff"):
        ext = ".tif"
    input_tif = raw_dir / f"{tile_output_folder}{ext}"

    content_length = request.headers.get("content-length")
    expected_bytes = int(content_length) if content_length and content_length.isdigit() else 0
    _ensure_disk_space_for_bytes(raw_dir, max(expected_bytes * 2, 512 * 1024 * 1024))

    output_tile_dir = processed_dir / tile_output_folder
    output_tile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(input_tif, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store dataset: {exc}") from exc
    finally:
        await file.close()

    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Uploading",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": safe_name,
            "tile_folder": tile_output_folder,
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
        str(input_tif),
        safe_name,
        str(output_tile_dir),
        tile_output_folder,
    )

    tiles_rel = output_tile_dir.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
    tile_template = (
        f"{str(request.base_url).rstrip('/')}/data/{tiles_rel}/{{z}}/{{x}}/{{y}}.png"
    )
    return ProcessDatasetOut(
        status="success",
        message="Dataset uploaded. gdal2tiles XYZ generation started in background.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=safe_name,
        cog_path="",
        cog_tile_url_template=tile_template,
    )


@app.post("/api/upload-dataset")
async def upload_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    await process_dataset(request, background_tasks, file, project_id)
    return {"status": "processing"}


@app.post("/api/dataset-metadata")
async def dataset_metadata(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    user = _require_user(request)
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
        raise HTTPException(status_code=404, detail="Dataset status not found")

    base = str(request.base_url).rstrip("/")
    tiles_rel = status.get("tiles_rel_path", "").strip()
    if tiles_rel:
        status["cog_tile_url_template"] = f"{base}/data/{tiles_rel}/{{z}}/{{x}}/{{y}}.png"
    else:
        cog_path = status.get("cog_path", "")
        if cog_path:
            encoded_cog_path = quote(cog_path.replace("\\", "/"), safe="")
            status["cog_tile_url_template"] = (
                f"{base}/api/cog/tiles/WebMercatorQuad/{{z}}/{{x}}/{{y}}.png"
                f"?url={encoded_cog_path}"
            )
    return status


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
        f"/api/cog/tiles/WebMercatorQuad/{z}/{x}/{y}.png?url={encoded_url}"
    )


@app.get("/api/proxy/info")
def proxy_info(path: str):
    full_path = (Path(LOCAL_DATA_PATH) / path).resolve().as_posix()
    encoded_url = quote(full_path, safe="")
    return RedirectResponse(f"/api/cog/info?url={encoded_url}")


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

    raw_dir_proj, processed_root = get_project_dirs(safe_project_id)
    legacy_raw = Path(LOCAL_DATA_PATH) / "raw_uploads"
    for raw_dir in (raw_dir_proj, legacy_raw):
        if not raw_dir.is_dir():
            continue
        for file_path in sorted(raw_dir.glob(f"{safe_project_id}__*"), key=lambda p: p.name.lower()):
            if not file_path.is_file():
                continue
            display_name = file_path.name.replace(f"{safe_project_id}__", "", 1)
            rel_path = file_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            files.append(
                {
                    "name": display_name,
                    "kind": "Raw Survey Data",
                    "type": file_path.suffix.lower().lstrip(".") or "file",
                    "size_bytes": str(file_path.stat().st_size),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Raw"),
                    "file_url": f"{base_url}/data/{rel_path}",
                    "layer_url": "",
                    "file_path": str(file_path.resolve()),
                    "rel_path": rel_path,
                },
            )

    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
    if jobs_root.is_dir():
        for job_dir in sorted([p for p in jobs_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            st = _read_dataset_status(safe_project_id, job_dir.name)
            if not st:
                continue
            tile_folder = (st.get("tile_folder") or "").strip()
            if not tile_folder:
                continue
            tile_root = processed_root / tile_folder
            if not tile_root.is_dir():
                continue
            if not any(d.is_dir() and d.name.isdigit() for d in tile_root.iterdir()):
                continue
            display_name = str(st.get("dataset_name") or f"{tile_folder}.tif")
            total_bytes = sum(p.stat().st_size for p in tile_root.rglob("*") if p.is_file())
            rel_base = tile_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            layer_url = f"{base_url}/data/{rel_base}/{{z}}/{{x}}/{{y}}.png"
            files.append(
                {
                    "name": display_name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "size_bytes": str(total_bytes or 1),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(tile_root.resolve()),
                    "rel_path": rel_base,
                },
            )

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
            total_bytes = sum(p.stat().st_size for p in webtiles.rglob("*") if p.is_file())
            rel_base = webtiles.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            layer_url = f"{base_url}/data/{rel_base}/{{z}}/{{x}}/{{y}}.png"
            files.append(
                {
                    "name": display_name,
                    "kind": "Web-Optimized Data",
                    "type": "cog",
                    "size_bytes": str(total_bytes or 1),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(webtiles.resolve()),
                    "rel_path": rel_base,
                },
            )

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
                    "size_bytes": str(tileset.stat().st_size),
                    "status": jobs_by_file.get(file_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": f"{base_url}/data/{rel}",
                    "file_path": str(tileset.resolve()),
                    "rel_path": rel,
                },
            )

    reports_dir = Path(LOCAL_DATA_PATH) / "reports" / safe_project_id
    if reports_dir.is_dir():
        for report in sorted(reports_dir.rglob("*.pdf"), key=lambda p: p.name.lower()):
            rel = report.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            files.append(
                {
                    "name": report.name,
                    "kind": "Reports",
                    "type": "pdf",
                    "size_bytes": str(report.stat().st_size),
                    "status": "Completed",
                    "file_url": f"{base_url}/data/{rel}",
                    "layer_url": "",
                    "file_path": str(report.resolve()),
                    "rel_path": rel,
                },
            )

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
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Only file deletion is supported")
    target.unlink(missing_ok=True)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
