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


@router.get("/api/admin/projects")
def admin_all_projects(
    request: Request,
    admin_user: dict[str, object] = Depends(verify_admin),
) -> dict[str, list[dict[str, object]]]:
    del request, admin_user
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.name, p.location, p.date, p.status, p.type,
                   u.id AS owner_user_id, u.email AS owner_email
            FROM projects p
            JOIN users u ON u.id = p.owner_user_id
            ORDER BY p.created_at DESC
            """,
        ).fetchall()
    return {
        "projects": [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "location": str(row["location"]),
                "date": str(row["date"]),
                "status": str(row["status"]),
                "type": str(row["type"]),
                "owner_user_id": int(row["owner_user_id"]),
                "owner_email": str(row["owner_email"]),
            }
            for row in rows
        ],
    }

@router.post("/api/admin/projects/{project_id}/datasets/resync")
def admin_resync_project_datasets(
    project_id: str,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    safe_project_id = _safe_project_id(project_id)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success", "project_id": safe_project_id}

@router.post("/api/admin/projects/{project_id}/jobs/cleanup-stale")
def admin_cleanup_stale_project_jobs(
    project_id: str,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, object]:
    safe_project_id = _safe_project_id(project_id)
    jobs = _read_processing_jobs()
    project_jobs = jobs.get(safe_project_id, [])
    now = datetime.now(timezone.utc)
    cleaned: list[str] = []
    for job in project_jobs:
        if not isinstance(job, dict):
            continue
        status = str(job.get("status") or "").strip().lower()
        if status in {"completed", "failed", "web-ready", "web ready"}:
            continue
        updated_raw = str(job.get("updated_at") or "")
        try:
            updated = datetime.fromisoformat(updated_raw.replace("Z", "+00:00"))
        except ValueError:
            updated = now - timedelta(days=1)
        if (now - updated).total_seconds() < 6 * 3600:
            continue
        job["status"] = "Failed"
        job["stage"] = "Stale processing job stopped"
        job["error"] = "Processing did not update for more than 6 hours. Admin cleanup marked it stale."
        job["updated_at"] = _now_iso()
        cleaned.append(str(job.get("job_id") or job.get("file_name") or "job"))
    jobs[safe_project_id] = project_jobs
    _write_processing_jobs(jobs)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success", "cleaned": cleaned}

@router.post("/api/admin/projects/{project_id}/manual-bulk-import")
async def admin_manual_bulk_import_for_project(
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

@router.get("/api/admin/override/project/{project_id}")
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

@router.patch("/api/admin/override/project/{project_id}")
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

@router.delete("/api/admin/projects/{project_id}")
def admin_delete_project(
    project_id: str,
    request: Request,
    admin_user: dict[str, object] = Depends(verify_admin),
) -> dict[str, object]:
    del request, admin_user
    safe_project_id = _safe_project_id(project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, name FROM projects WHERE id = ?",
            (safe_project_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Project not found")

    _delete_project_storage(safe_project_id)

    with get_db_connection() as connection:
        connection.execute("DELETE FROM camera_views WHERE project_id = ?", (safe_project_id,))
        connection.execute("DELETE FROM dataset_crop_masks WHERE project_id = ?", (safe_project_id,))
        connection.execute("DELETE FROM spatial_features WHERE project_id = ?", (safe_project_id,))
        connection.execute("DELETE FROM spatial_layers WHERE project_id = ?", (safe_project_id,))
        connection.execute("DELETE FROM projects WHERE id = ?", (safe_project_id,))
        connection.commit()
    _remove_project_processing_jobs(safe_project_id)
    _invalidate_project_files_cache(safe_project_id)
    return {
        "status": "success",
        "project_id": safe_project_id,
        "project_name": str(row["name"]),
    }

@router.put("/api/admin/projects/{project_id}/datasets/{dataset_name:path}")
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
        st["display_name"] = payload.name.strip()
        st["source_name"] = payload.name.strip()
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

@router.delete("/api/admin/projects/{project_id}/files")
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
