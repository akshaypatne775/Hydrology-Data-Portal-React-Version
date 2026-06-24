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


@router.patch("/api/admin/datasets/{project_id}/metadata")
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

@router.patch("/api/admin/datasets/{project_id}/{dataset_id}/rename")
def admin_rename_dataset_by_id(
    project_id: str,
    dataset_id: str,
    payload: AdminDatasetRenamePayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    del request, admin_user
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id, st = _admin_dataset_status_by_key(safe_project_id, dataset_id)
    new_name = payload.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Dataset name is required")
    st["dataset_name"] = new_name
    st["display_name"] = new_name
    st["source_name"] = new_name
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, safe_dataset_id, st)
    _sync_dataset_metadata_to_processing_job(safe_project_id, safe_dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success", "dataset_id": safe_dataset_id, "name": new_name}

@router.post("/api/admin/catalog/{project_id}/reconcile")
def admin_reconcile_catalog(
    project_id: str,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, object]:
    safe_project_id = _safe_project_id(project_id)
    _invalidate_project_files_cache(safe_project_id)
    _CATALOG_FORCE_DISK_SCAN.add(safe_project_id)
    try:
        scanned = project_files(safe_project_id, request)["files"]
    finally:
        _CATALOG_FORCE_DISK_SCAN.discard(safe_project_id)
    stats = catalog_service.reconcile_from_file_rows(safe_project_id, scanned, local_data_path=LOCAL_DATA_PATH)
    stats["revision"] = catalog_service.get_revision(safe_project_id)
    return {"status": "success", **stats}
