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


@router.get("/api/admin/users/activity")
def admin_user_activity(
    request: Request,
    admin_user: dict[str, object] = Depends(verify_admin),
) -> dict[str, list[dict[str, object]]]:
    now = datetime.now(timezone.utc)
    active_cutoff = now.timestamp() - 15 * 60
    with get_db_connection() as connection:
        users = connection.execute(
            """
            SELECT id, email, role, requested_role, approval_status, created_at, can_access_catalog, can_upload_data, location_required, hidden_tabs
            FROM users
            ORDER BY created_at ASC
            """
        ).fetchall()
        activity_rows = connection.execute(
            """
            SELECT user_id, ip_address, method, endpoint, device_label,
                   latitude, longitude, location_accuracy, accessed_at
            FROM activity_logs
            WHERE user_id IS NOT NULL
            ORDER BY accessed_at DESC
            """
        ).fetchall()

    by_user: dict[int, list[sqlite3.Row]] = {}
    for row in activity_rows:
        by_user.setdefault(int(row["user_id"]), []).append(row)

    result: list[dict[str, object]] = []
    for user_row in users:
        user_id = int(user_row["id"])
        rows = by_user.get(user_id, [])
        latest = rows[0] if rows else None
        last_seen = str(latest["accessed_at"]) if latest else ""
        last_seen_ts = 0.0
        if last_seen:
            try:
                last_seen_ts = datetime.fromisoformat(last_seen).timestamp()
            except ValueError:
                last_seen_ts = 0.0
        result.append(
            {
                "user_id": user_id,
                "email": str(user_row["email"]),
                "role": str(user_row["role"] or "user"),
                "requested_role": str(user_row["requested_role"] or user_row["role"] or "user"),
                "can_access_catalog": bool(user_row["can_access_catalog"]),
                "can_upload_data": bool(user_row["can_upload_data"]),
                "location_required": False if str(user_row["role"] or "user").lower() == "admin" else bool(user_row["location_required"]),
                "hidden_tabs": _normalize_hidden_tabs(user_row["hidden_tabs"]),
                "approval_status": str(user_row["approval_status"] or "pending"),
                "status": (
                    "Offline"
                    if latest and str(latest["method"]).upper() == "LOGOUT"
                    else "Active" if last_seen_ts >= active_cutoff else "Offline"
                ),
                "current_ip": str(latest["ip_address"]) if latest else "",
                "device_label": str(latest["device_label"] or "") if latest else "",
                "location": (
                    f"{float(latest['latitude']):.5f}, {float(latest['longitude']):.5f}"
                    if latest and latest["latitude"] is not None and latest["longitude"] is not None
                    else ""
                ),
                "location_accuracy_m": (
                    int(float(latest["location_accuracy"]))
                    if latest and latest["location_accuracy"] is not None
                    else 0
                ),
                "unique_ip_count": len({str(row["ip_address"]) for row in rows}),
                "last_accessed_data": (
                    f"{latest['method']} {latest['endpoint']}" if latest else ""
                ),
                "last_seen_at": last_seen,
            },
        )
    return {"users": result}

@router.get("/api/admin/users/{user_id}/projects")
def admin_user_projects(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, list[ProjectOut]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, name, location, date, status, type
            FROM projects
            WHERE owner_user_id = ?
            ORDER BY created_at DESC
            """,
            (user_id,),
        ).fetchall()
    return {
        "projects": [
            ProjectOut(
                id=str(row["id"]),
                name=str(row["name"]),
                location=str(row["location"]),
                date=str(row["date"]),
                status=str(row["status"]),
                type=str(row["type"]),
            )
            for row in rows
        ],
    }

@router.post("/api/admin/users/{user_id}/approve")
def admin_approve_user(
    user_id: int,
    payload: AdminUserApprovalPayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    role = "admin" if payload.role.strip().lower() == "admin" else "user"
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET role = ?,
                requested_role = ?,
                approval_status = 'approved',
                approved_at = ?,
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (role, role, _now_iso(), user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    _send_email(
        str(row["email"]),
        "Droid Cloud access approved",
        (
            f"Your Droid Cloud {role} access has been approved.\n\n"
            f"You can now login here: {PUBLIC_PORTAL_URL}\n"
        ),
    )
    return {"status": "success"}

@router.patch("/api/admin/users/{user_id}/role")
def admin_assign_user_role(
    user_id: int,
    payload: AdminUserRolePayload,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    role = "admin" if payload.role.strip().lower() == "admin" else "user"
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET role = ?,
                requested_role = ?,
                approval_status = 'approved',
                approved_at = COALESCE(approved_at, ?)
            WHERE id = ?
            """,
            (role, role, _now_iso(), user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    _send_email(
        str(row["email"]),
        "Droid Cloud role updated",
        f"Your Droid Cloud role is now: {role}.\n\nLogin: {PUBLIC_PORTAL_URL}\n",
    )
    return {"status": "success", "role": role}

@router.patch("/api/admin/users/{user_id}/password")
def admin_reset_user_password(
    user_id: int,
    payload: AdminUserPasswordResetPayload,
    admin: dict[str, object] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    password_hash = _hash_password(payload.password)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET password_hash = ? WHERE id = ?",
            (password_hash, user_id),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "user_id": user_id, "email": str(row["email"])}

@router.patch("/api/admin/users/{user_id}/catalog-access")
def admin_set_user_catalog_access(
    user_id: int,
    payload: dict[str, bool],
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    enabled = bool(payload.get("enabled", True))
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET can_access_catalog = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "user_id": user_id, "can_access_catalog": enabled}

@router.patch("/api/admin/users/{user_id}/upload-access")
def admin_set_user_upload_access(
    user_id: int,
    payload: AdminUserUploadAccessPayload,
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    enabled = bool(payload.enabled)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET can_upload_data = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "user_id": user_id, "can_upload_data": enabled}

@router.patch("/api/admin/users/{user_id}/location-required")
def admin_set_user_location_required(
    user_id: int,
    payload: AdminUserLocationRequiredPayload,
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    enabled = bool(payload.enabled)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, role FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        if str(row["role"] or "user").lower() == "admin":
            enabled = False
        connection.execute(
            "UPDATE users SET location_required = ? WHERE id = ?",
            (1 if enabled else 0, user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "user_id": user_id, "location_required": enabled}

@router.patch("/api/admin/users/{user_id}/hidden-tabs")
def admin_set_user_hidden_tabs(
    user_id: int,
    payload: AdminUserHiddenTabsPayload,
    admin: dict[str, str] = Depends(verify_admin),
) -> dict[str, object]:
    del admin
    hidden_tabs = _normalize_hidden_tabs(payload.hidden_tabs)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            "UPDATE users SET hidden_tabs = ? WHERE id = ?",
            (json.dumps(hidden_tabs), user_id),
        )
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "user_id": user_id, "hidden_tabs": hidden_tabs}

@router.post("/api/admin/users/{user_id}/disapprove")
def admin_disapprove_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT email FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute(
            """
            UPDATE users
            SET approval_status = 'rejected',
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (user_id,),
        )
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.commit()
    _clear_session_auth_cache()
    _send_email(
        str(row["email"]),
        "Droid Cloud access request update",
        "Your Droid Cloud access request was not approved. Contact the owner for more details.",
    )
    return {"status": "success"}

@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str]:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="User not found")
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        try:
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        except sqlite3.IntegrityError:
            connection.execute(
                """
                UPDATE users
                SET email = ?,
                    role = 'user',
                    requested_role = 'user',
                    approval_status = 'deleted',
                    approval_token_hash = NULL
                WHERE id = ?
                """,
                (f"deleted-user-{user_id}@local.invalid", user_id),
            )
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success"}

@router.delete("/api/admin/users/{user_id}/advanced")
def admin_advanced_delete_user(
    user_id: int,
    request: Request,
    admin_user: dict[str, str | int] = Depends(verify_admin),
) -> dict[str, str | int]:
    with get_db_connection() as connection:
        user_row = connection.execute(
            "SELECT id FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()
        if not user_row:
            raise HTTPException(status_code=404, detail="User not found")
        project_rows = connection.execute(
            "SELECT id FROM projects WHERE owner_user_id = ?",
            (user_id,),
        ).fetchall()
        project_ids = [str(row["id"]) for row in project_rows]

    for project_id in project_ids:
        _delete_project_storage(project_id)

    with get_db_connection() as connection:
        for project_id in project_ids:
            connection.execute("DELETE FROM camera_views WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM dataset_crop_masks WHERE project_id = ?", (project_id,))
            connection.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            _invalidate_project_files_cache(project_id)
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM activity_logs WHERE user_id = ?", (user_id,))
        connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        connection.commit()
    _clear_session_auth_cache()
    return {"status": "success", "deleted_projects": len(project_ids)}
