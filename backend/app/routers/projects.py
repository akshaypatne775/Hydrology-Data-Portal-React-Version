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


@router.get("/api/projects")
def get_projects(request: Request) -> dict[str, list[ProjectOut]]:
    user = _require_user(request)
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, location, date, status, type
            FROM projects
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            """,
            (int(user["id"]),),
        ).fetchall()
    projects = [
        ProjectOut(
            id=str(row["id"]),
            name=str(row["name"]),
            location=str(row["location"]),
            date=str(row["date"]),
            status=str(row["status"]),
            type=str(row["type"]),
        )
        for row in rows
    ]
    return {"projects": projects}

@router.post("/api/projects")
def create_project(payload: ProjectCreatePayload, request: Request) -> ProjectOut:
    user = _require_user(request)
    project_id = f"proj_{secrets.token_hex(8)}"
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO projects (id, owner_user_id, name, location, date, status, type, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_id,
                int(user["id"]),
                payload.name.strip(),
                payload.location.strip(),
                payload.date.strip(),
                payload.status.strip(),
                payload.type.strip(),
                now,
            ),
        )
        connection.commit()
    return ProjectOut(
        id=project_id,
        name=payload.name.strip(),
        location=payload.location.strip(),
        date=payload.date.strip(),
        status=payload.status.strip(),
        type=payload.type.strip(),
    )

@router.patch("/api/projects/{project_id}")
def update_project(project_id: str, payload: ProjectUpdatePayload, request: Request) -> ProjectOut:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    name = payload.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Project name is required")
    with get_db_connection() as connection:
        connection.execute(
            "UPDATE projects SET name = ? WHERE id = ? AND owner_user_id = ?",
            (name, safe_project_id, int(user["id"])),
        )
        row = connection.execute(
            "SELECT id, name, location, date, status, type FROM projects WHERE id = ? AND owner_user_id = ?",
            (safe_project_id, int(user["id"])),
        ).fetchone()
        connection.commit()
    if not row:
        raise HTTPException(status_code=404, detail="Project not found")
    return ProjectOut(
        id=str(row["id"]),
        name=str(row["name"]),
        location=str(row["location"]),
        date=str(row["date"]),
        status=str(row["status"]),
        type=str(row["type"]),
    )

@router.get("/api/projects/{project_id}/camera-views")
def get_camera_views(project_id: str, request: Request) -> dict[str, list[dict[str, str | float]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, lat, lng, height, heading, pitch, roll, created_at, updated_at
            FROM camera_views
            WHERE project_id = ? AND owner_user_id = ?
            ORDER BY updated_at DESC
            """,
            (safe_project_id, int(user["id"])),
        ).fetchall()
    return {
        "views": [
            {
                "id": str(row["id"]),
                "name": str(row["name"]),
                "lat": float(row["lat"]),
                "lng": float(row["lng"]),
                "height": float(row["height"]),
                "heading": float(row["heading"]),
                "pitch": float(row["pitch"]),
                "roll": float(row["roll"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
            }
            for row in rows
        ],
    }

@router.post("/api/projects/{project_id}/camera-views")
def save_camera_view(project_id: str, payload: CameraViewPayload, request: Request) -> dict[str, str | float]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    name = payload.name.strip()[:80]
    if not name:
        raise HTTPException(status_code=400, detail="Camera view name is required")
    if not (-90 <= payload.lat <= 90 and -180 <= payload.lng <= 180):
        raise HTTPException(status_code=400, detail="Invalid camera location")
    view_id = _safe_dataset_id(f"cam-{secrets.token_hex(8)}")
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO camera_views
                (id, project_id, owner_user_id, name, lat, lng, height, heading, pitch, roll, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                view_id,
                safe_project_id,
                int(user["id"]),
                name,
                float(payload.lat),
                float(payload.lng),
                float(payload.height),
                float(payload.heading),
                float(payload.pitch),
                float(payload.roll),
                now,
                now,
            ),
        )
        connection.commit()
    return {
        "id": view_id,
        "name": name,
        "lat": float(payload.lat),
        "lng": float(payload.lng),
        "height": float(payload.height),
        "heading": float(payload.heading),
        "pitch": float(payload.pitch),
        "roll": float(payload.roll),
        "created_at": now,
        "updated_at": now,
    }

@router.delete("/api/projects/{project_id}/camera-views/{view_id}")
def delete_camera_view(project_id: str, view_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_view_id = _safe_dataset_id(view_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        cursor = connection.execute(
            "DELETE FROM camera_views WHERE id = ? AND project_id = ? AND owner_user_id = ?",
            (safe_view_id, safe_project_id, int(user["id"])),
        )
        connection.commit()
    if cursor.rowcount == 0:
        raise HTTPException(status_code=404, detail="Camera view not found")
    return {"status": "success"}

@router.get("/api/project-stats")
def project_stats() -> dict[str, list]:
    return {
        "catchment_stats": CATCHMENT_STATS,
        "stream_stats": STREAM_STATS,
        "lulc_rows": LULC_ROWS,
    }

@router.get("/api/survair-stats")
def survair_stats() -> dict[str, list]:
    return {
        "catchment_stats": CATCHMENT_STATS,
        "stream_stats": STREAM_STATS,
        "lulc_rows": LULC_ROWS,
    }
