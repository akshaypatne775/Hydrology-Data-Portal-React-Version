import os
import sys
import time
import math
import json
import uuid
import shutil
import struct
import base64
import hashlib
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from app.core.config import *
from app.core.database import *
from app.models.datasets import *


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

def _safe_project_id(project_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", project_id or ""):
        raise HTTPException(status_code=400, detail="Invalid project_id")
    return project_id

def _safe_tileset_id(tileset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", tileset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid tileset_id")
    return tileset_id

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

def _safe_dataset_id(dataset_id: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,240}", dataset_id or ""):
        raise HTTPException(status_code=400, detail="Invalid dataset_id")
    return dataset_id

def _fast_tile_dir_size(tile_root: Path) -> str:
    """Avoid walking thousands of XYZ PNG tiles just to populate a UI size label."""
    for marker_name in ("tilemapresource.xml", "doc.kml"):
        marker = tile_root / marker_name
        if marker.is_file():
            return str(max(marker.stat().st_size, 1))
    return "1"

def _safe_tile_folder_name(tile_folder: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9._ /-]{1,300}", tile_folder or "") or ".." in tile_folder:
        raise HTTPException(status_code=400, detail="Invalid tile_folder")
    return tile_folder.strip("/")

def _safe_spatial_id(value: str, label: str = "id") -> str:
    clean = (value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,120}", clean):
        raise HTTPException(status_code=400, detail=f"Invalid {label}")
    return clean

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

def _file_fingerprint(path: Path) -> dict[str, str]:
    st = path.stat()
    return {
        "path": path.resolve().as_posix(),
        "size": str(st.st_size),
        "mtime_ns": str(st.st_mtime_ns),
    }

def _safe_export_stem(value: str, fallback: str = "dataset") -> str:
    stem = Path(os.path.basename(value.strip() or fallback)).stem or fallback
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-")
    return cleaned[:120] or fallback
