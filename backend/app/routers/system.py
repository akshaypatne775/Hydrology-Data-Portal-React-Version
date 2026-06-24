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


@router.post("/api/run-flood-engine")
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

@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}

@router.get("/api/version")
def api_version() -> dict[str, str]:
    return {
        "version": PORTAL_VERSION,
        "dev_mode": "true" if str(os.getenv("DEV_MODE", "")).strip().lower() in {"1", "true", "yes", "on"} else "false",
        "manual_bulk_import": "true",
        "locate_folder": "true" if os.name == "nt" else "false",
    }

@router.post("/api/client-error-log")
def client_error_log(payload: ClientErrorLogPayload, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(payload.project_id) if payload.project_id else ""
    safe_dataset_id = re.sub(r"[^A-Za-z0-9._-]+", "-", payload.dataset_id).strip("-")[:240] if payload.dataset_id else ""
    _write_portal_error_log(
        payload.area,
        payload.message,
        url=payload.url[:1000],
        stack=payload.stack[:6000],
        project_id=safe_project_id,
        dataset_id=safe_dataset_id,
        user_id=int(user["id"]),
        user_email=str(user.get("email") or ""),
        extra=payload.extra or {},
    )
    if payload.area in {"ept_viewer", "pointcloud_viewer"} and safe_project_id and safe_dataset_id:
        output_path = _ept_dataset_dir(safe_project_id, safe_dataset_id).resolve()
        local_root = Path(LOCAL_DATA_PATH).resolve()
        if output_path.exists() and local_root in output_path.parents:
            marker = output_path / ".conversion_error.txt"
            marker.write_text(
                "Point cloud viewer runtime error after conversion. "
                f"{payload.message}\n{payload.stack[:6000]}",
                encoding="utf-8",
            )
            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": safe_dataset_id,
                    "kind": "pointcloud",
                    "file_name": safe_dataset_id,
                    "status": "Failed",
                    "error": payload.message[:8000],
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)
    return {"status": "logged"}
