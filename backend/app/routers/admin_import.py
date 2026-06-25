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


@router.post("/api/admin/locate-folder")
async def admin_locate_folder(payload: AdminLocateFolderPayload, request: Request) -> dict[str, str]:
    verify_admin(request)
    folder_path = await run_in_threadpool(_browse_server_folder, payload.initial_path, payload.kind, payload.mode)
    return {"status": "success", "folder_path": folder_path}

@router.post("/api/admin/manual-bulk-import")
async def admin_manual_bulk_import(
    payload: AdminManualBulkImportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    project_id = str(payload.project_id or "").strip()
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    return _queue_admin_manual_bulk_import(
        project_id=project_id,
        payload=payload,
        request=request,
        background_tasks=background_tasks,
    )
