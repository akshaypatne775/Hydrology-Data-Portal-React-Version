import asyncio
import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
import sqlite3
import json

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
import laspy
from PIL import Image
from pydantic import BaseModel

# Map tiles, ortho, DEM, terrain quantized-mesh, videos, etc. (served under /tiles).
# Keep overridable so frontend viewers (Leaflet/Cesium) can fetch local datasets.
LOCAL_DATA_PATH = os.getenv(
    "LOCAL_DATA_PATH",
    r"D:/Codings/Hydrology Data Portal React Version/Project_Data",
)
ISSUES_DB_PATH = Path(LOCAL_DATA_PATH) / "issues.db"

Path(LOCAL_DATA_PATH).mkdir(parents=True, exist_ok=True)

# Large uploads: headroom above merged file size (e.g. 14GB LAS + merge buffer).
DISK_HEADROOM_BYTES = int(os.getenv("UPLOAD_DISK_HEADROOM_MB", "512")) * 1024 * 1024
MERGE_COPY_BUFFER_BYTES = 8 * 1024 * 1024  # streaming merge, avoid loading whole file in RAM
POINTCLOUD_SRS_IN = os.getenv("POINTCLOUD_SRS_IN", "").strip()
POINTCLOUD_SRS_OUT = os.getenv("POINTCLOUD_SRS_OUT", "4978").strip()

app = FastAPI(
    title="Hydrology & Mapping Portal API",
    description="Backend services for hydrology data and mapping.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount(
    "/tiles",
    StaticFiles(directory=LOCAL_DATA_PATH),
    name="local-tiles",
)
# The /tiles static mount also serves generated Cesium point cloud artifacts:
# /tiles/pointclouds/{project_id}/tileset.json (+ child .pnts files).

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


def _upload_session_dir(filename: str, total_chunks: int) -> Path:
    """Stable temp folder for one logical upload (same as frontend chunk sequence)."""
    safe_name = _safe_pointcloud_basename(filename)
    digest = hashlib.sha256(
        f"{safe_name}\0{total_chunks}".encode("utf-8"),
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
    /tiles/pointclouds/<project_id>/tileset.json resolves correctly.
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


def _run_py3dtiles_conversion(input_file: Path, output_dir: Path) -> None:
    """
    Background conversion worker:
    py3dtiles convert <input_file> --out <output_dir>
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    err_path = output_dir / ".conversion_error.txt"
    if err_path.exists():
        err_path.unlink(missing_ok=True)

    cmd = [
        "py3dtiles",
        "convert",
        str(input_file),
        "--out",
        str(output_dir),
    ]
    # Optional CRS reprojection for local/projected LAS sources.
    srs_in = POINTCLOUD_SRS_IN or _detect_input_srs(input_file) or ""
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
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr or exc.stdout or str(exc)
        print("py3dtiles conversion failed:", msg)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
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


@app.get("/api/pointcloud-status/{project_id}")
def pointcloud_status(project_id: str) -> dict[str, bool | str]:
    """
    Poll conversion progress: tileset.json appears when py3dtiles finishes.
    If conversion fails, .conversion_error.txt is written under the output folder.
    """
    safe_id = _safe_project_id(project_id)
    out_dir = Path(LOCAL_DATA_PATH) / "pointclouds" / safe_id
    tileset = out_dir / "tileset.json"
    err_file = out_dir / ".conversion_error.txt"
    tileset_url = f"http://localhost:8000/tiles/pointclouds/{safe_id}/tileset.json"

    if err_file.is_file():
        try:
            msg = err_file.read_text(encoding="utf-8", errors="replace")
        except OSError:
            msg = "Unknown conversion error"
        return {
            "ready": False,
            "failed": True,
            "error": msg[:8000],
            "tileset_url": tileset_url,
        }

    if tileset.is_file():
        return {
            "ready": True,
            "failed": False,
            "tileset_url": tileset_url,
        }

    return {
        "ready": False,
        "failed": False,
        "tileset_url": tileset_url,
    }


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(ISSUES_DB_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_issues_table() -> None:
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
        connection.commit()


@app.on_event("startup")
def startup() -> None:
    ensure_issues_table()


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
def media() -> dict[str, list[dict[str, str]]]:
    media_dir = Path(LOCAL_DATA_PATH) / "media"
    media_dir.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, str]] = []
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
                "url": f"http://localhost:8000/tiles/media/{file_path.name}",
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
async def process_pointcloud(payload: PointCloudProcessPayload) -> dict[str, str]:
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
        f"http://localhost:8000/tiles/pointclouds/{payload.project_id}/tileset.json"
    )

    return {
        "status": "success",
        "message": f"Point cloud processed for {payload.filename}.",
        "tileset_url": tileset_url,
    }


@app.post("/api/upload-chunk")
async def upload_chunk(
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    chunkIndex: int = Form(...),
    totalChunks: int = Form(...),
) -> dict[str, str]:
    """
    Accept one binary chunk of a larger LAS/LAZ upload.
    Chunks are written to a temp folder under LOCAL_DATA_PATH/uploads/chunks/.
    """
    safe_name = _safe_pointcloud_basename(filename)
    if totalChunks < 1 or totalChunks > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")
    if chunkIndex < 0 or chunkIndex >= totalChunks:
        raise HTTPException(status_code=400, detail="Invalid chunkIndex")

    session_dir = _upload_session_dir(safe_name, totalChunks)
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
    payload: CompleteUploadPayload, background_tasks: BackgroundTasks
) -> dict[str, str]:
    """
    Merge chunk files in order into a single LAS/LAZ under raw_uploads/.
    Uses streaming copy + per-chunk delete to limit peak disk and avoid RAM spikes.
    """
    safe_name = _safe_pointcloud_basename(payload.filename)
    total = payload.totalChunks
    if total < 1 or total > 500_000:
        raise HTTPException(status_code=400, detail="Invalid totalChunks")

    session_dir = _upload_session_dir(safe_name, total)
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

    raw_dir = Path(LOCAL_DATA_PATH) / "raw_uploads"
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / safe_name

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
    cached_project_id = f"pc-{content_hash[:24]}"
    cache = _read_conversion_cache()
    reused_project_id = cache.get(content_hash, cached_project_id)
    pointcloud_out_dir = Path(LOCAL_DATA_PATH) / "pointclouds" / reused_project_id
    tileset_url = (
        f"http://localhost:8000/tiles/pointclouds/{reused_project_id}/tileset.json"
    )

    if (pointcloud_out_dir / "tileset.json").is_file():
        out_path.unlink(missing_ok=True)
        return {
            "status": "success",
            "message": (
                f"Merged {total} chunks into {safe_name}. "
                "Found existing converted tiles for same file content; reusing cache."
            ),
            "path": str(out_path),
            "size_bytes": str(total_bytes),
            "tileset_url": tileset_url,
            "project_id": reused_project_id,
        }

    cache[content_hash] = reused_project_id
    _write_conversion_cache(cache)
    background_tasks.add_task(_run_py3dtiles_conversion, out_path, pointcloud_out_dir)

    return {
        "status": "success",
        "message": (
            f"Merged {total} chunks into {safe_name}. "
            "Background py3dtiles conversion started."
        ),
        "path": str(out_path),
        "size_bytes": str(total_bytes),
        "tileset_url": tileset_url,
        "project_id": payload.project_id,
    }


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
