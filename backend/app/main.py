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
import time
import zipfile
from pathlib import Path
import sqlite3
import json
import xml.etree.ElementTree as ET
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
from starlette.middleware.base import BaseHTTPMiddleware
import laspy
from PIL import Image
from pydantic import BaseModel
from titiler.core.factory import TilerFactory

from app.core.database import configure_database, ensure_tables, get_db_connection

# Project_Data lives beside backend/ and frontend/ (repo root).
BASE_DIR = Path(__file__).resolve().parent.parent.parent
_DEFAULT_PROJECT_DATA = BASE_DIR / "Project_Data"
LOCAL_DATA_PATH = os.getenv("LOCAL_DATA_PATH", str(_DEFAULT_PROJECT_DATA))

# Map tiles, ortho, DEM, terrain quantized-mesh, videos, etc.
# - `/data` â€” preferred URL prefix for files under Project_Data (see StaticFiles mount).
# - `/tiles` â€” same directory, kept for flood/media and older clients.
ISSUES_DB_PATH = Path(LOCAL_DATA_PATH) / "issues.db"

Path(LOCAL_DATA_PATH).mkdir(parents=True, exist_ok=True)
configure_database(ISSUES_DB_PATH)

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
    name="data",
)
# Static files under Project_Data, e.g.:
# /data/projects/{project_id}/processed/{tile_folder}/{z}/{x}/{y}.png
# /data/pointclouds/{project_id}/{tileset_id}/tileset.json

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


class DatasetMetaPayload(BaseModel):
    dataset_id: str
    month: str = ""
    dataset_type: str = ""


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
    if suffix not in (".tif", ".tiff", ".las", ".laz", ".csv", ".zip"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.las/.laz/.csv/.zip files are supported")
    return base


def _infer_dataset_type(name: str) -> str:
    lowered = name.lower()
    suffix = Path(lowered).suffix
    if suffix == ".csv":
        return "csv"
    if suffix == ".zip":
        return "3dmodel"
    if "dtm" in lowered:
        return "dtm"
    if "dsm" in lowered:
        return "dsm"
    if "ortho" in lowered or suffix in (".tif", ".tiff"):
        return "ortho"
    if suffix in (".las", ".laz"):
        return "pointcloud"
    return "dataset"


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


def _run_gdal2tiles_subprocess(
    input_tif: str,
    output_dir: str,
    project_id: str,
    dataset_name: str,
) -> None:
    """Run gdal2tiles via QGIS OSGeo4W shell with an 8-bit fallback for DTM/DSM rasters."""
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

    print(f"Starting GDAL processing for {dataset_name} in project {project_id}...")
    result = run_osgeo(f'gdal2tiles --xyz -z 1-22 -w none "{in_abs}" "{out_abs}"')
    if result.returncode == 0:
        if has_usable_tiles():
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
) -> None:
    await asyncio.to_thread(
        _run_gdal2tiles_subprocess,
        input_tif,
        output_dir,
        project_id,
        dataset_name,
    )


async def process_dataset_background(
    project_id: str,
    dataset_id: str,
    input_tif: str,
    file_name: str | None,
    tile_output_dir: str,
    tile_folder: str,
) -> None:
    tiles_dir = str(Path(tile_output_dir).resolve())
    existing_status = _read_dataset_status(project_id, dataset_id) or {}
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
            "status": "Generating XYZ tiles",
            "updated_at": _now_iso(),
        },
    )
    err_path = _dataset_dir(project_id, dataset_id) / ".conversion_error.txt"
    err_path.unlink(missing_ok=True)
    try:
        await process_tif_to_tiles(
            input_tif,
            tiles_dir,
            project_id,
            file_name or Path(input_tif).name,
        )
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
                **common_status,
                "status": "Web-Ready",
                "updated_at": _now_iso(),
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


def _dataset_type_folder(dataset_type: str) -> str:
    normalized = _normalize_dataset_type(dataset_type, "")
    if normalized in {"ortho", "dtm", "dsm", "pointcloud", "csv", "3dmodel"}:
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
    if path.name.lower() == "tileset.json":
        return True
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


def _is_valid_tile_dataset(folder: Path) -> bool:
    """
    Accept either:
    - classical gdal2tiles metadata (`tilemapresource.xml`), OR
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
async def process_pointcloud_request(payload: PointCloudProcessPayload, request: Request) -> dict[str, str]:
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


@app.post("/api/process-dataset", response_model=ProcessDatasetOut)
async def process_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    dataset_type: str = Form(""),
    month: str = Form(""),
) -> ProcessDatasetOut:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(file.filename or "")
    ext = Path(safe_name).suffix.lower()
    if ext not in (".tif", ".tiff", ".csv", ".zip"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.csv/.zip dataset files are supported")
    normalized_type = "3dmodel" if ext == ".zip" else _normalize_dataset_type(dataset_type, safe_name)
    normalized_month = _normalize_month(month)

    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")

    raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
    meta_dir = _dataset_dir(safe_project_id, dataset_id)
    meta_dir.mkdir(parents=True, exist_ok=True)

    input_path = raw_dir / f"{tile_output_folder}{ext}"

    content_length = request.headers.get("content-length")
    expected_bytes = int(content_length) if content_length and content_length.isdigit() else 0
    _ensure_disk_space_for_bytes(raw_dir, max(expected_bytes * 2, 512 * 1024 * 1024))

    output_tile_dir = processed_dir / tile_output_folder
    if ext != ".csv":
        output_tile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(input_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store dataset: {exc}") from exc
    finally:
        await file.close()

    raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
    if ext == ".zip":
        print(f"Extracting 3D Tiles ZIP {safe_name}...")
        if output_tile_dir.exists():
            shutil.rmtree(output_tile_dir)
        output_tile_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(input_path, output_tile_dir)
        tileset_root = _find_extracted_tileset_root(output_tile_dir)
        tileset_rel = (tileset_root / "tileset.json").resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        model_rel = tileset_root.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
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
                "raw_rel_path": raw_rel,
                "tiles_rel_path": model_rel,
                "tileset_rel_path": tileset_rel,
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
                "raw_rel_path": raw_rel,
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
            "month": normalized_month,
            "raw_rel_path": raw_rel,
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


@app.post("/api/datasets/{project_id}/sync")
def sync_manual_datasets(project_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    _, processed_dir = get_project_dirs(safe_project_id)
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    tracked_folders: set[str] = set()
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        st = _read_dataset_status(safe_project_id, job_dir.name)
        if not st:
            continue
        folder = (st.get("tile_folder") or "").strip()
        if folder:
            tracked_folders.add(folder)
        rel = (st.get("tiles_rel_path") or "").strip()
        if rel:
            tracked_folders.add(rel)

    found_new = 0
    candidates: list[tuple[Path, str, str]] = [
        *[(item, "cog", _infer_dataset_type(item.name)) for item in _candidate_processed_tile_dirs(processed_dir)],
        *[(item, "3DModel", "3dmodel") for item in _candidate_processed_model_dirs(processed_dir)],
    ]
    for item, layer_kind, dataset_type in candidates:
        rel_path = item.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        folder_name = _display_model_folder_name(item, processed_dir) if layer_kind == "3DModel" else item.name
        if folder_name in tracked_folders or rel_path in tracked_folders:
            continue

        dataset_id = _safe_dataset_id(
            f"manual-{re.sub(r'[^A-Za-z0-9._-]+', '-', folder_name)[:48]}-{secrets.token_hex(4)}",
        )
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
                "tiles_rel_path": rel_path,
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


@app.post("/api/datasets/{project_id}/open-manual-folder")
def open_manual_dataset_folder(project_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
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
    if str(status.get("dataset_type") or "").lower() in ("3dmodel", "3dtiles"):
        tileset_rel = status.get("tileset_rel_path", "").strip() or f"{tiles_rel.rstrip('/')}/tileset.json"
        status["cog_tile_url_template"] = f"{base}/data/{tileset_rel}"
        status["layer_type"] = "3DModel"
    elif tiles_rel:
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
    listed_rel_paths: set[str] = set()

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
                    "dataset_id": "",
                    "dataset_type": _infer_dataset_type(display_name),
                    "month": "",
                    "raw_rel_path": rel_path,
                },
            )
            listed_rel_paths.add(rel_path)

    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
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
                            "file_url": f"{base_url}/data/{raw_rel_path}",
                            "layer_url": "",
                            "file_path": str(csv_path.resolve()),
                            "rel_path": raw_rel_path,
                            "dataset_id": str(st.get("dataset_id") or job_dir.name),
                            "dataset_type": "csv",
                            "month": str(st.get("month") or ""),
                            "raw_rel_path": raw_rel_path,
                        },
                    )
                    listed_rel_paths.add(raw_rel_path)
                continue
            if not tile_folder:
                continue
            tiles_rel_path = str(st.get("tiles_rel_path") or "").strip()
            tile_root = Path(LOCAL_DATA_PATH) / tiles_rel_path if tiles_rel_path else processed_root / tile_folder
            if str(st.get("dataset_type") or "").lower() in ("3dmodel", "3dtiles") or _is_3d_model_dataset(tile_root):
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
                        "size_bytes": str(tileset_path.stat().st_size),
                        "status": jobs_by_file.get(display_name, {}).get("status", str(st.get("status") or "WEB-READY")),
                        "file_url": f"{base_url}/data/{tileset_rel}",
                        "layer_url": f"{base_url}/data/{tileset_rel}",
                        "file_path": str(tileset_path.resolve()),
                        "rel_path": rel_base,
                        "dataset_id": str(st.get("dataset_id") or job_dir.name),
                        "dataset_type": "3dmodel",
                        "month": str(st.get("month") or ""),
                        "raw_rel_path": raw_rel_path,
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
                    "size_bytes": _fast_tile_dir_size(tile_root),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(tile_root.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": str(st.get("dataset_id") or job_dir.name),
                    "dataset_type": str(st.get("dataset_type") or _infer_dataset_type(display_name)),
                    "month": str(st.get("month") or ""),
                    "raw_rel_path": str(st.get("raw_rel_path") or ""),
                },
            )
            listed_rel_paths.add(rel_base)

    # Include manual processed folders even when not synced/tracked yet.
    if processed_root.is_dir():
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
                    "size_bytes": str(potree_html.stat().st_size),
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
                    "size_bytes": str(tileset_path.stat().st_size),
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
                    "size_bytes": _fast_tile_dir_size(tile_root),
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
                    "size_bytes": _fast_tile_dir_size(webtiles),
                    "status": jobs_by_file.get(display_name, {}).get("status", "Web-Ready"),
                    "file_url": f"{base_url}/data/{rel_base}",
                    "layer_url": layer_url,
                    "file_path": str(webtiles.resolve()),
                    "rel_path": rel_base,
                    "dataset_id": str(st.get("dataset_id") or ds_dir.name),
                    "dataset_type": str(st.get("dataset_type") or _infer_dataset_type(display_name)),
                    "month": str(st.get("month") or ""),
                    "raw_rel_path": str(st.get("raw_rel_path") or ""),
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
                    "size_bytes": str(tileset.stat().st_size),
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
    if target.is_dir():
        raise HTTPException(status_code=400, detail="Only file deletion is supported")
    target.unlink(missing_ok=True)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
