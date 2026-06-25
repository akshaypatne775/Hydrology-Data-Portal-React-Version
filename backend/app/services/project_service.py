import os
import sys
import math
import traceback
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import json
import logging
import uuid
import struct
import base64
import asyncio
import hashlib
import time

import numpy as np
from fastapi import Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

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

from app.services.catalog_service import mirror_processing_job, delete_asset_artifacts, upsert_asset, bump_revision
from app.services.raster import convert_tif_to_cog
from app.core.database import get_db_connection, get_db

# Deferred imports
def _get_project_dirs(*args, **kwargs):
    from app.main import get_project_dirs
    return get_project_dirs(*args, **kwargs)

def _read_dataset_status(*args, **kwargs):
    from app.main import _read_dataset_status
    return _read_dataset_status(*args, **kwargs)


def get_project_dirs(project_id: str) -> tuple[Path, Path]:
    """Per-project raw uploads and Python Rasterio XYZ output under Project_Data/projects."""
    safe = _safe_project_id(project_id)
    project_dir = Path(LOCAL_DATA_PATH) / "projects" / safe
    raw_dir = project_dir / "raw"
    processed_dir = project_dir / "processed"
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir

def get_project_dataset_type_dirs(project_id: str, dataset_type: str) -> tuple[Path, Path]:
    raw_root, processed_root = get_project_dirs(project_id)
    folder = _dataset_type_folder(dataset_type)
    raw_dir = raw_root / folder
    processed_dir = processed_root / folder
    raw_dir.mkdir(parents=True, exist_ok=True)
    processed_dir.mkdir(parents=True, exist_ok=True)
    return raw_dir, processed_dir

def _is_admin_user_id(user_id: int) -> bool:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
    return bool(row and str(row["role"]).lower() == "admin")

def _ensure_project_owner(user_id: int, project_id: str) -> None:
    if _is_admin_user_id(user_id):
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT id FROM projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row:
            return
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM projects WHERE id = ? AND owner_user_id = ?",
            (project_id, user_id),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")

def _delete_project_storage(project_id: str) -> None:
    safe_project_id = _safe_project_id(project_id)
    local_root = Path(LOCAL_DATA_PATH).resolve()
    for target in (
        local_root / "projects" / safe_project_id,
        local_root / "datasets" / safe_project_id,
        local_root / "pointclouds" / safe_project_id,
    ):
        resolved = target.resolve()
        if resolved.exists() and local_root in resolved.parents:
            shutil.rmtree(resolved, ignore_errors=True)
