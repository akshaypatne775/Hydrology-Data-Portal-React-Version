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
from app.core.utils import *
from app.core.paths import *
from app.services.raster import *
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


@router.get("/api/pointcloud-status/{project_id}")
def pointcloud_status(
    project_id: str, request: Request, tileset_id: str | None = None
) -> dict[str, bool | str]:
    """
    Poll conversion progress: output.copc.laz appears when COPC finishes;
    ept.json appears when the Untwine EPT fallback finishes.
    If conversion fails, .conversion_error.txt is written under the output folder.
    """
    user = _require_user(request)
    safe_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_id)
    base_url = str(request.base_url).rstrip("/")
    processed_root = _project_processed_root(safe_id)
    ept_root = _project_pointcloud_root(safe_id)
    legacy_pointcloud_root = _legacy_project_pointcloud_root(safe_id)

    def ept_search_roots() -> list[Path]:
        roots = [ept_root, legacy_pointcloud_root, processed_root]
        return [root for index, root in enumerate(roots) if root not in roots[:index]]

    def source_name_for(folder: Path) -> str:
        marker = folder / ".source_name.txt"
        if marker.is_file():
            try:
                return marker.read_text(encoding="utf-8", errors="replace").strip()
            except OSError:
                return ""
        return ""

    def add_candidate(folder: Path, bucket: list[Path]) -> None:
        if folder not in bucket:
            bucket.append(folder)

    def pointcloud_lookup_keys(value: object) -> set[str]:
        text = str(value or "").strip()
        if not text:
            return set()
        try:
            text = unquote(text)
        except Exception:
            pass
        text = text.replace("\\", "/").split("/")[-1]
        stem = Path(text).stem.lower()
        raw = text.lower()
        canonical = stem
        canonical = canonical.replace(safe_id.lower(), "")
        canonical = re.sub(r"^(?:ept|copc|pointcloud|point-cloud|pc)(?=[0-9._\-\s])[\W_]*", "", canonical, flags=re.IGNORECASE)
        canonical = re.sub(r"[\W_]*(?:ept|copc|pointcloud|point-cloud|pc)$", "", canonical, flags=re.IGNORECASE)
        canonical = re.sub(r"[-_][a-f0-9]{8,}$", "", canonical, flags=re.IGNORECASE)
        canonical = re.sub(r"[^a-z0-9]+", "", canonical)
        return {key for key in {raw, stem, canonical} if key}

    def job_match_candidates(lookup: str) -> list[str]:
        lookup_keys = pointcloud_lookup_keys(lookup)
        if not lookup_keys:
            return []
        matches: list[str] = []
        for job in _read_processing_jobs().get(safe_id, []):
            job_id = str(job.get("job_id") or "").strip()
            file_name = str(job.get("file_name") or "").strip()
            if not job_id:
                continue
            job_keys = pointcloud_lookup_keys(job_id) | pointcloud_lookup_keys(file_name)
            if lookup_keys.intersection(job_keys):
                matches.append(job_id)
        return matches

    candidates: list[Path] = []
    if tileset_id:
        safe_tileset_id = _safe_ept_folder_name(tileset_id)
        add_candidate(_ept_dataset_dir(safe_id, safe_tileset_id), candidates)
        add_candidate(_legacy_ept_pointcloud_dataset_dir(safe_id, safe_tileset_id), candidates)
        add_candidate(_legacy_ept_dataset_dir(safe_id, safe_tileset_id), candidates)
        status = _read_dataset_status(safe_id, safe_tileset_id)
        if status:
            tile_folder = str(status.get("tile_folder") or "").strip()
            if tile_folder:
                add_candidate(_ept_dataset_dir(safe_id, tile_folder), candidates)
                add_candidate(_legacy_ept_pointcloud_dataset_dir(safe_id, tile_folder), candidates)
                add_candidate(_legacy_ept_dataset_dir(safe_id, tile_folder), candidates)
            for rel_key in ("tiles_rel_path", "source_asset_rel_path"):
                rel_value = str(status.get(rel_key) or "").replace("\\", "/").strip("/")
                if not rel_value or ".." in rel_value:
                    continue
                status_path = Path(LOCAL_DATA_PATH) / rel_value
                add_candidate(status_path if status_path.is_dir() else status_path.parent, candidates)
        for job_id in job_match_candidates(tileset_id):
            safe_job_id = _safe_ept_folder_name(job_id)
            add_candidate(_ept_dataset_dir(safe_id, safe_job_id), candidates)
            add_candidate(_legacy_ept_pointcloud_dataset_dir(safe_id, safe_job_id), candidates)
            add_candidate(_legacy_ept_dataset_dir(safe_id, safe_job_id), candidates)
        lookup = tileset_id.strip().lower()
        lookup_stem = Path(lookup).stem
        lookup_keys = pointcloud_lookup_keys(tileset_id)
        for root in ept_search_roots():
            if root.is_dir():
                for child in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.stat().st_mtime, reverse=True):
                    if child.name in {"ortho", "dtm", "dsm", "pointclouds"}:
                        continue
                    source_name = source_name_for(child).lower()
                    child_keys = pointcloud_lookup_keys(child.name) | pointcloud_lookup_keys(source_name)
                    if lookup_keys.intersection(child_keys):
                        add_candidate(child, candidates)
                    elif source_name and lookup in {source_name, Path(source_name).stem}:
                        add_candidate(child, candidates)
                    elif lookup_stem and child.name.lower().startswith(f"{lookup_stem}-"):
                        add_candidate(child, candidates)
    else:
        for root in ept_search_roots():
            if root.is_dir():
                children = sorted(
                    [p for p in root.iterdir() if p.is_dir() and p.name not in {"ortho", "dtm", "dsm", "pointclouds"}],
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )
                for child in children:
                    add_candidate(child, candidates)

    for candidate in candidates:
        copc_file = _copc_asset_in_dir(candidate)
        if copc_file is not None:
            source_name = source_name_for(candidate)
            return {
                "ready": True,
                "failed": False,
                "tileset_url": _copc_viewer_url(
                    base_url, safe_id, candidate.name, source_name,
                    copc_file.relative_to(Path(LOCAL_DATA_PATH) / "projects" / safe_id).as_posix(),
                ),
                "copc_url": _copc_url(
                    base_url, safe_id, candidate.name,
                    copc_file.relative_to(Path(LOCAL_DATA_PATH) / "projects" / safe_id).as_posix(),
                ),
                "viewer_type": "copc",
            }

    for candidate in candidates:
        err_file = candidate / ".conversion_error.txt"
        if err_file.is_file():
            try:
                msg = err_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                msg = "Unknown conversion error"
            return {
                "ready": False,
                "failed": True,
                "error": msg[:8000],
                "tileset_url": _copc_viewer_url(base_url, safe_id, candidate.name),
                "copc_url": _copc_url(base_url, safe_id, candidate.name),
                "viewer_type": "copc",
            }

    pending_suffix = f"/{quote(_safe_ept_folder_name(tileset_id), safe='')}" if tileset_id else ""
    return {
        "ready": False,
        "failed": False,
        "tileset_url": (
            _copc_viewer_url(base_url, safe_id, _safe_ept_folder_name(tileset_id))
            if tileset_id
            else f"{base_url}/data/projects/{safe_id}/processed{pending_suffix}/"
        ),
        "viewer_type": "pending",
    }

@router.post("/api/process-pointcloud")
async def process_pointcloud_request(payload: PointCloudProcessPayload, request: Request) -> dict[str, str]:
    user = _require_upload_user(request)
    safe_project_id = _safe_project_id(payload.project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    raise HTTPException(
        status_code=410,
        detail="This legacy point cloud processing endpoint has been replaced by chunk upload + EPT conversion.",
    )

@router.get("/api/data/pointclouds/{project_id}/{file_path:path}")
def secure_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_pointcloud_data_file(project_id, file_path, request)

@router.get("/data/pointclouds/{project_id}/{file_path:path}")
def secure_legacy_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    return _serve_pointcloud_data_file(project_id, file_path, request)
