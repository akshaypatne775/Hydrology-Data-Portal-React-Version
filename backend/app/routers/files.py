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


@router.post("/api/projects/{project_id}/pointclouds/{dataset_id}/slice-export")
def start_pointcloud_slice_export(
    project_id: str,
    dataset_id: str,
    payload: PointCloudSliceExportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, str]:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_ept_folder_name(dataset_id)
    _ensure_project_file_access(request, safe_project_id)
    center = _finite_vector(payload.box.center, 3, "center")
    dimensions = _finite_vector(payload.box.dimensions, 3, "dimensions")
    if np.any(dimensions <= 0):
        raise HTTPException(status_code=400, detail="Section box dimensions must be positive")
    rotation = payload.box.rotation or [0.0, 0.0, 0.0]
    if len(rotation) < 3:
        rotation = rotation + [0.0] * (3 - len(rotation))
    _finite_vector(rotation[:3], 3, "rotation")
    if not np.all(np.isfinite(center)):
        raise HTTPException(status_code=400, detail="Invalid section box center")

    job_id = f"slice-{int(time.time())}-{secrets.token_hex(4)}"
    export_base_rel = f"exports/pointcloud_slices/{quote(safe_dataset_id, safe='')}/{job_id}"
    export_rel = f"{export_base_rel}/clipped_points.csv"
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": job_id,
            "kind": "pointcloud_slice_export",
            "file_name": payload.name or f"{safe_dataset_id} Slice",
            "status": "Processing",
            "stage": "Clipping point cloud inside section box",
            "progress_percent": "5",
            "download_url": f"/api/data/projects/{safe_project_id}/{export_rel}",
            "download_url_las": f"/api/data/projects/{safe_project_id}/{export_base_rel}/clipped_points.las",
            "updated_at": _now_iso(),
        },
    )
    payload_dict = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
    background_tasks.add_task(
        _run_pointcloud_slice_export,
        safe_project_id,
        safe_dataset_id,
        job_id,
        payload_dict,
    )
    return {
        "status": "processing",
        "job_id": job_id,
        "download_url": f"/api/data/projects/{safe_project_id}/{export_rel}",
        "download_url_las": f"/api/data/projects/{safe_project_id}/{export_base_rel}/clipped_points.las",
    }

@router.get("/api/data/projects/{project_id}/{file_path:path}")
def secure_project_data_file(project_id: str, file_path: str, request: Request) -> Response:
    return _serve_project_data_file(project_id, file_path, request)

@router.get("/data/projects/{project_id}/{file_path:path}")
def secure_legacy_project_data_file(project_id: str, file_path: str, request: Request) -> Response:
    return _serve_project_data_file(project_id, file_path, request)

@router.get("/tiles/{file_path:path}")
def secure_legacy_tiles_file(file_path: str, request: Request) -> FileResponse:
    _require_user(request)
    cleaned_path = file_path.replace("\\", "/").lstrip("/")
    parts = [part for part in cleaned_path.split("/") if part]
    if parts and parts[0] == "pointclouds":
        raise HTTPException(
            status_code=410,
            detail="Legacy point cloud tiles are disabled. Use the Droid EPT viewer endpoint.",
        )
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

@router.get("/api/projects/{project_id}/reports/{dataset_id}/view")
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

@router.get("/api/projects/{project_id}/reports/{dataset_id}/download")
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

@router.get("/api/projects/{project_id}/datasets/{dataset_id}/raw/download")
def download_project_dataset_raw(project_id: str, dataset_id: str, request: Request) -> FileResponse:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    path, st = _secure_dataset_file(safe_project_id, dataset_id, report_only=False)
    return FileResponse(
        str(path),
        filename=str(st.get("dataset_name") or path.name),
        content_disposition_type="attachment",
    )

@router.get("/api/projects/{project_id}/catalog-revision")
def project_catalog_revision(project_id: str, request: Request) -> dict[str, int]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    return {"revision": catalog_service.get_revision(safe_project_id)}

@router.get("/api/projects/{project_id}/files")
def project_files(project_id: str, request: Request) -> dict[str, list[dict[str, str]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    base_url = str(request.base_url).rstrip("/")
    force_disk = safe_project_id in _CATALOG_FORCE_DISK_SCAN
    cached = _get_cached_project_files(safe_project_id)
    if cached is not None and not catalog_service.catalog_db_enabled() and not force_disk:
        return {"files": cached}
    if (
        not force_disk
        and catalog_service.catalog_db_enabled()
        and catalog_service.asset_count(safe_project_id) > 0
    ):
        catalog_rows = [
            _canonical_file_row(row)
            for row in catalog_service.list_file_rows(safe_project_id, base_url, local_data_path=LOCAL_DATA_PATH)
        ]
        canonical_files = _dedupe_pointcloud_file_rows(catalog_rows, safe_project_id)
        _set_cached_project_files(safe_project_id, canonical_files)
        return {"files": canonical_files}

    jobs_by_file = {
        job.get("file_name", ""): job
        for job in _read_processing_jobs().get(safe_project_id, [])
        if isinstance(job, dict)
    }
    files: list[dict[str, str]] = []
    listed_rel_paths: set[str] = set()
    listed_pointcloud_keys: set[str] = set()

    raw_dir_proj, processed_root = get_project_dirs(safe_project_id)
    ready_pointcloud_names: set[str] = set()

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
        stem = stem.replace(safe_project_id.lower(), "")
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

    def add_ready_pointcloud_name_keys(name: str) -> None:
        stem = Path(str(name or "").strip()).stem.lower()
        if not stem:
            return
        ready_pointcloud_names.add(stem)
        ready_pointcloud_names.update(pointcloud_keys(name))
        base_without_hash = re.sub(r"-[a-f0-9]{8,}$", "", stem, flags=re.IGNORECASE)
        if base_without_hash:
            ready_pointcloud_names.add(base_without_hash)
            ready_pointcloud_names.update(pointcloud_keys(base_without_hash))

    ready_ept_paths = [
        *_project_pointcloud_root(safe_project_id).glob("*/ept.json"),
        *_legacy_project_pointcloud_root(safe_project_id).glob("*/ept.json"),
        *processed_root.glob("*/ept.json"),
        *processed_root.glob("*/*/ept.json"),
        *processed_root.glob("*/*/*/ept.json"),
    ]
    for ready_ept in ready_ept_paths:
        if not ready_ept.is_file() or (ready_ept.parent / ".conversion_error.txt").is_file():
            continue
        marker = ready_ept.parent / ".source_name.txt"
        source_name = ""
        if marker.is_file():
            try:
                source_name = marker.read_text(encoding="utf-8").strip()
            except OSError:
                source_name = ""
        add_ready_pointcloud_name_keys(source_name)
        add_ready_pointcloud_name_keys(ready_ept.parent.name)

    ready_copc_paths = _project_copc_assets(safe_project_id)
    for ready_copc in ready_copc_paths:
        if not ready_copc.is_file() or (ready_copc.parent / ".conversion_error.txt").is_file():
            continue
        marker = ready_copc.parent / ".source_name.txt"
        source_name = ""
        if marker.is_file():
            try:
                source_name = marker.read_text(encoding="utf-8").strip()
            except OSError:
                source_name = ""
        add_ready_pointcloud_name_keys(source_name)
        add_ready_pointcloud_name_keys(ready_copc.parent.name)

    for job in _read_processing_jobs().get(safe_project_id, []):
        if not isinstance(job, dict):
            continue
        if str(job.get("kind") or "").lower() != "pointcloud":
            continue
        if str(job.get("status") or "").strip().lower() not in {"completed", "web-ready", "web-ready"}:
            continue
        add_ready_pointcloud_name_keys(str(job.get("file_name") or ""))
        add_ready_pointcloud_name_keys(str(job.get("job_id") or ""))

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
            is_pointcloud_raw = dataset_type == "pointcloud" or file_path.suffix.lower() in {".las", ".laz"}
            dataset_id_for_file = str(meta.get("dataset_id") or "")
            raw_pc_keys = pointcloud_keys(display_name, file_path.name, dataset_id_for_file, rel_path)
            if is_pointcloud_raw and raw_pc_keys.intersection(ready_pointcloud_names):
                listed_rel_paths.add(rel_path)
                continue
            if is_report and dataset_id_for_file:
                file_url = f"{base_url}/api/projects/{safe_project_id}/reports/{dataset_id_for_file}/view"
                download_url = f"{base_url}/api/projects/{safe_project_id}/reports/{dataset_id_for_file}/download"
            elif is_pointcloud_raw:
                file_url = ""
                download_url = ""
            elif dataset_id_for_file:
                file_url = f"{base_url}/api/projects/{safe_project_id}/datasets/{dataset_id_for_file}/raw/download"
                download_url = file_url
            else:
                file_url = f"{base_url}/data/{rel_path}"
                download_url = file_url
            row_status = str(meta.get("status") or jobs_by_file.get(display_name, {}).get("status", "Raw"))
            if is_pointcloud_raw and not raw_pc_keys.intersection(ready_pointcloud_names):
                row_status = "Uploaded"
            files.append(
                {
                    "name": display_name,
                    "kind": "Reports" if is_report else "Raw Survey Data",
                    "type": "pdf" if is_report else file_path.suffix.lower().lstrip(".") or "file",
                    "size_bytes": str(file_path.stat().st_size),
                    "status": row_status,
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

        copc_paths = _project_copc_assets(safe_project_id)
        for copc_file in sorted({p.resolve() for p in copc_paths}, key=lambda p: (p.parent.stat().st_mtime, p.parent.name.lower()), reverse=True):
            compatibility_dir = _copc_ept_compat_dir(copc_file)
            if not POTREE_NATIVE_COPC_ENABLED and _ept_asset_quality(compatibility_dir) >= 0:
                continue
            rel = copc_file.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            rel_base = copc_file.parent.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths or rel_base in listed_rel_paths:
                continue
            if (copc_file.parent / ".conversion_error.txt").is_file():
                listed_rel_paths.add(rel)
                listed_rel_paths.add(rel_base)
                continue
            manifest = _read_dataset_manifest(copc_file.parent)
            source_name = str(manifest.get("source_name") or manifest.get("display_name") or manifest.get("dataset_name") or "")
            source_marker = copc_file.parent / ".source_name.txt"
            if source_marker.is_file():
                try:
                    source_name = source_marker.read_text(encoding="utf-8").strip()
                except OSError:
                    pass
            display_name = source_name or copc_file.name
            dataset_id = str(manifest.get("dataset_id") or copc_file.parent.name)
            project_asset_rel = copc_file.relative_to(
                Path(LOCAL_DATA_PATH) / "projects" / safe_project_id
            ).as_posix()
            pc_row_keys = pointcloud_keys(display_name, copc_file.parent.name, dataset_id, rel_base)
            if pc_row_keys.intersection(listed_pointcloud_keys):
                listed_rel_paths.add(rel)
                listed_rel_paths.add(rel_base)
                continue
            viewer_url = _copc_viewer_url(
                base_url,
                safe_project_id,
                copc_file.parent.name,
                display_name,
                project_asset_rel,
            )
            _write_dataset_manifest(
                copc_file.parent,
                {
                    **manifest,
                    "project_id": safe_project_id,
                    "dataset_id": dataset_id,
                    "display_name": display_name,
                    "dataset_name": display_name,
                    "dataset_type": "pointcloud",
                    "source_name": display_name,
                    "viewer_type": "copc",
                    "asset_name": copc_file.name,
                    "asset_rel_path": project_asset_rel,
                },
            )
            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "pointcloud",
                    "file_name": display_name,
                    "status": "Completed",
                    "updated_at": _now_iso(),
                    "result_url": viewer_url,
                    "viewer_type": "copc",
                    "dataset_type": "pointcloud",
                    "layer_type": "pointcloud",
                    "tiles_rel_path": rel_base,
                },
            )
            files.append(
                {
                    "name": display_name,
                    "display_name": display_name,
                    "kind": "Droid COPC Point Cloud",
                    "type": "PointCloud",
                    "size_bytes": str(copc_file.stat().st_size),
                    "status": "WEB-READY",
                    "asset_status": "WEB-READY",
                    "file_url": viewer_url,
                    "layer_url": viewer_url,
                    "viewer_url": viewer_url,
                    "copc_url": _copc_url(
                        base_url, safe_project_id, copc_file.parent.name, project_asset_rel
                    ),
                    "viewer_type": "copc",
                    "file_path": str(copc_file.resolve()),
                    "rel_path": rel,
                    "dataset_id": dataset_id,
                    "dataset_type": "pointcloud",
                    "layer_type": "pointcloud",
                    "month": "",
                    "raw_rel_path": "",
                    "source_rel_path": str(manifest.get("raw_rel_path") or ""),
                    "canonical_key": canonical_pointcloud_key(display_name) or canonical_pointcloud_key(dataset_id) or rel_base,
                },
            )
            listed_rel_paths.add(rel)
            listed_rel_paths.add(rel_base)
            listed_pointcloud_keys.update(pc_row_keys)

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

    slice_export_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "exports" / "pointcloud_slices"
    if slice_export_root.is_dir():
        project_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id
        for export_file in sorted(slice_export_root.rglob("*"), key=lambda p: p.stat().st_mtime if p.is_file() else 0, reverse=True):
            if not export_file.is_file() or export_file.suffix.lower() not in {".csv", ".las"}:
                continue
            rel = export_file.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
            if rel in listed_rel_paths:
                continue
            metadata: dict[str, str] = {}
            metadata_path = export_file.parent / "slice_export.json"
            if metadata_path.is_file():
                try:
                    loaded = json.loads(metadata_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        metadata = {str(k): str(v) for k, v in loaded.items()}
                except (OSError, json.JSONDecodeError, TypeError):
                    metadata = {}
            project_rel = export_file.relative_to(project_root).as_posix()
            file_url = f"{base_url}/api/data/projects/{safe_project_id}/{project_rel}"
            dataset_id = str(metadata.get("dataset_id") or export_file.parent.parent.name)
            label = "Clipped Points LAS" if export_file.suffix.lower() == ".las" else "Clipped Points CSV"
            files.append(
                {
                    "name": f"{str(metadata.get('job_id') or export_file.parent.name)} - {label}",
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
                    "dataset_id": dataset_id,
                    "dataset_type": "pointcloud_slice_export",
                    "month": "",
                    "raw_rel_path": "",
                },
            )
            listed_rel_paths.add(rel)

    def pointcloud_row_rank(row: dict[str, str]) -> int:
        if str(row.get("viewer_type") or "").lower() in {"copc", "ept"}:
            return 0
        status = str(row.get("status") or "").strip().lower()
        if status in {"processing", "uploaded", "queued", "running"}:
            return 1
        return 2

    canonical_files: list[dict[str, str]] = []
    pointcloud_groups: list[dict[str, object]] = []
    ignored_identity_keys = {"ept", "copc", "pointcloud", "point-cloud", "pc", "output", "index", "las", "laz"}
    for index, file_row in enumerate(files):
        row = _canonical_file_row(file_row)
        signature = " ".join(
            str(row.get(key) or "").lower()
            for key in ("kind", "type", "layer_type", "dataset_type", "name", "viewer_type")
        )
        if "pointcloud" not in signature and "point cloud" not in signature:
            canonical_files.append(row)
            continue
        keys = {
            key for key in pointcloud_keys(
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
        if str(winner.get("viewer_url") or "").strip():
            winner["status"] = "WEB-READY"
            winner["asset_status"] = "WEB-READY"
            winner["layer_type"] = "pointcloud"
            winner["dataset_type"] = "pointcloud"
        canonical_files.append(_canonical_file_row(winner))

    if catalog_service.catalog_db_enabled():
        catalog_service.reconcile_from_file_rows(safe_project_id, canonical_files, local_data_path=LOCAL_DATA_PATH)

    _set_cached_project_files(safe_project_id, canonical_files)
    return {"files": canonical_files}

@router.delete("/api/projects/{project_id}/catalog/{asset_id}")
def delete_catalog_asset(project_id: str, asset_id: str, request: Request) -> dict[str, int | str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_asset_id = str(asset_id or "").strip()
    if not safe_asset_id or ".." in safe_asset_id:
        raise HTTPException(status_code=400, detail="Invalid asset id")
    return _purge_catalog_dataset(safe_project_id, safe_asset_id)

@router.post("/api/projects/{project_id}/bulk-delete-datasets")
def bulk_delete_project_datasets(
    project_id: str,
    payload: AdminBulkDeletePayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    deleted: list[str] = []
    errors: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in payload.items or []:
        keys = [
            str(item.dataset_id or "").strip(),
            str(item.file_name or "").strip(),
            Path(str(item.rel_path or "").strip()).stem if str(item.rel_path or "").strip() else "",
        ]
        for key in keys:
            if not key or key in seen:
                continue
            seen.add(key)
            try:
                _purge_catalog_dataset(safe_project_id, key)
                deleted.append(key)
            except HTTPException as exc:
                errors.append({"key": key, "error": str(exc.detail)})
            except Exception as exc:  # noqa: BLE001
                errors.append({"key": key, "error": str(exc)[:400]})
            break
    if catalog_service.catalog_db_enabled():
        catalog_service.prune_missing_assets(safe_project_id, LOCAL_DATA_PATH)
        catalog_service.bump_revision(safe_project_id)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success", "deleted": deleted, "errors": errors, "deleted_count": len(deleted)}

@router.delete("/api/projects/{project_id}/files")
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
        purge = _purge_catalog_dataset(safe_project_id, Path(rel).stem or rel)
        if int(purge.get("removed_paths", 0)) >= 0:
            return {"status": "success"}
        raise HTTPException(status_code=404, detail="File not found")
    if catalog_service.catalog_db_enabled():
        catalog_service.delete_asset_by_rel_path(
            safe_project_id,
            rel,
            local_data_path=LOCAL_DATA_PATH,
        )
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
