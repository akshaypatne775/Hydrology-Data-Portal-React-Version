import os
import sys
import math
import traceback
import subprocess
import shutil
import json
import logging
import uuid
import struct
import base64
import asyncio
import hashlib
import time
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, List, Dict, Optional

import numpy as np
from fastapi import APIRouter, Request, Response, Depends, UploadFile, File, Form, HTTPException, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from sqlalchemy.exc import IntegrityError

from app.core.config import *
from app.models.auth import *
from app.models.projects import *
from app.models.datasets import *
from app.models.admin import *
from app.models.pointclouds import *
from app.models.spatial import *
from app.models.analysis import *
from app.models.issues import *
from app.models.misc import *

from app.core.database import get_db_connection, get_db
from app.dependencies import _require_user, _get_optional_user, _require_admin, verify_admin, _require_upload_user, _enforce_rate_limit, _client_ip_for_limit

from app.services.catalog_service import mirror_processing_job, delete_asset_artifacts, upsert_asset, bump_revision, list_job_rows, list_file_rows, reconcile_from_file_rows, find_assets_by_key, delete_assets_by_key
from app.services.raster import convert_tif_to_cog
from app.services.pointcloud.ept_service import *
from app.services.pointcloud.copc_service import *
from app.services.pointcloud.pointcloud_jobs import *
from app.services.pointcloud.pointcloud_slice import *
from app.services.pointcloud.pdal_tools import *

# Ensure all Phase 5 services are imported
from app.core.security import *
from app.core.middleware import *
from app.services.auth_service import *
from app.services.project_service import *
from app.services.dataset_service import *
from app.services.upload_service import *
from app.services.analysis_service import *
from app.services.grid_export_service import *
from app.services.spatial_feature_service import *
from app.services.bulk_import_service import *

router = APIRouter()


@router.post("/api/upload-chunk")
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
    user = _require_upload_user(request)
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

@router.post("/api/complete-upload")
async def complete_upload(
    payload: CompleteUploadPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    """
    Merge chunk files in order into a single LAS/LAZ under projects/<id>/raw/.
    Uses streaming copy + per-chunk delete to limit peak disk and avoid RAM spikes.
    """
    user = _require_upload_user(request)
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
    ept_dataset_name = _ept_dataset_name(reused_tileset_id)
    output_dir = _ept_dataset_dir(safe_project_id, ept_dataset_name)
    final_path = out_path
    hash_marker = output_dir / ".source_hash.txt"
    existing_hash = None
    try:
        if hash_marker.is_file():
            existing_hash = hash_marker.read_text(encoding="utf-8").strip()
    except OSError:
        existing_hash = None

    copc_file = output_dir / "output.copc.laz"
    cached_viewer_type = "copc" if copc_file.is_file() else ""
    cached_viewer_dataset_name = ept_dataset_name
    if cached_viewer_type and existing_hash == content_hash:
        try:
            (output_dir / ".source_name.txt").write_text(safe_name, encoding="utf-8")
            (output_dir / ".viewer_type.txt").write_text(cached_viewer_type, encoding="utf-8")
        except OSError:
            pass
        cached_viewer_url = _copc_viewer_url(
            str(request.base_url), safe_project_id, cached_viewer_dataset_name, safe_name
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": ept_dataset_name,
                "kind": "pointcloud",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": _copc_viewer_url("", safe_project_id, cached_viewer_dataset_name, safe_name),
                "viewer_type": cached_viewer_type,
            },
        )
        return {
            "status": "success",
            "message": (
                f"Merged {total} chunks into {safe_name}. "
                "Found existing COPC viewer for same file content; reusing project viewer."
            ),
            "tileset_url": "PENDING",
            "project_id": safe_project_id,
            "target_tileset_url": cached_viewer_url,
            "copc_url": _copc_url(str(request.base_url), safe_project_id, ept_dataset_name),
            "viewer_type": cached_viewer_type,
            "tileset_id": ept_dataset_name,
        }

    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        hash_marker.write_text(content_hash, encoding="utf-8")
        (output_dir / ".source_name.txt").write_text(safe_name, encoding="utf-8")
        _write_dataset_manifest(
            output_dir,
            {
                "project_id": safe_project_id,
                "dataset_id": ept_dataset_name,
                "display_name": safe_name,
                "dataset_type": "pointcloud",
                "source_name": safe_name,
                "raw_rel_path": out_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix(),
                "viewer_type": "pending",
            },
        )
        _write_dataset_manifest(
            out_path,
            {
                "project_id": safe_project_id,
                "dataset_id": ept_dataset_name,
                "display_name": safe_name,
                "dataset_type": "pointcloud",
                "source_name": safe_name,
                "raw_rel_path": out_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix(),
            },
        )
    except OSError:
        pass
    cache[user_cache_key] = reused_tileset_id
    _write_conversion_cache(cache)
    raw_rel = out_path.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
    _upsert_processing_job(
        safe_project_id,
            {
                "job_id": ept_dataset_name,
                "kind": "pointcloud",
                "file_name": safe_name,
                "status": "Processing",
            "updated_at": _now_iso(),
            "raw_rel_path": raw_rel,
            "content_hash": content_hash,
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_pointcloud_ept_job,
        str(final_path),
        str(output_dir),
        ept_dataset_name,
        safe_project_id,
        ept_dataset_name,
        safe_name,
        content_hash,
    )

    return {
        "status": "success",
        "message": "File merged. COPC conversion started in background.",
        "tileset_url": "PENDING",
        "project_id": safe_project_id,
        "target_tileset_url": _copc_viewer_url(str(request.base_url), safe_project_id, ept_dataset_name, safe_name),
        "copc_url": _copc_url(str(request.base_url), safe_project_id, ept_dataset_name),
        "viewer_type": "pending",
        "tileset_id": ept_dataset_name,
    }

@router.post("/api/upload-dataset-chunk")
async def upload_dataset_chunk(
    request: Request,
    chunk: UploadFile = File(...),
    filename: str = Form(...),
    project_id: str = Form(...),
    chunkIndex: int = Form(...),
    totalChunks: int = Form(...),
) -> dict[str, str]:
    user = _require_upload_user(request)
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

@router.post("/api/complete-dataset-upload", response_model=ProcessDatasetOut)
async def complete_dataset_upload(
    payload: CompleteDatasetUploadPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ProcessDatasetOut:
    user = _require_upload_user(request)
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

@router.post("/api/upload-dataset")
async def upload_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    _require_upload_user(request)
    await process_dataset(request, background_tasks, file, project_id)
    return {"status": "processing"}
