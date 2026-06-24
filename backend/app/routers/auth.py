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


@router.post("/api/auth/signup")
def auth_signup(payload: AuthPayload, request: Request) -> dict[str, str]:
    return _create_pending_user(payload.email, payload.password, "user", request)

@router.post("/api/auth/request-admin")
def auth_request_admin(payload: AuthPayload, request: Request) -> dict[str, str]:
    return _create_pending_user(payload.email, payload.password, "admin", request)

@router.post("/api/auth/login")
def auth_login(payload: AuthPayload, response: Response) -> dict[str, str]:
    ensure_tables()
    email = payload.email.strip().lower()
    if "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, password_hash, role, approval_status, can_access_catalog, can_upload_data, location_required, hidden_tabs FROM users WHERE email = ?",
            (email,),
        ).fetchone()
    if not row or not _verify_password(payload.password, str(row["password_hash"])):
        raise HTTPException(status_code=401, detail="Invalid email or password")
    if str(row["approval_status"] or "pending").lower() != "approved":
        raise HTTPException(status_code=403, detail="Account approval is pending")

    raw_token = secrets.token_urlsafe(48)
    now_ts = int(datetime.now(timezone.utc).timestamp())
    expires_at = now_ts + SESSION_TTL_SECONDS
    with get_db_connection() as connection:
        connection.execute("DELETE FROM sessions WHERE user_id = ?", (int(row["id"]),))
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
            (_token_hash(raw_token), int(row["id"]), expires_at, _now_iso()),
        )
        connection.commit()
    _clear_session_auth_cache()
    _set_session_cookie(response, raw_token)
    _send_owner_sms(f"Droid Cloud login: {email} logged in as {str(row['role'] or 'user')}.")
    return {"status": "success", "email": email}

@router.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict[str, str]:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    raw = _unsign_session_token(signed) if signed else None
    if raw:
        with get_db_connection() as connection:
            row = connection.execute(
                "SELECT user_id FROM sessions WHERE token_hash = ?",
                (_token_hash(raw),),
            ).fetchone()
            connection.execute("DELETE FROM sessions WHERE token_hash = ?", (_token_hash(raw),))
            if row:
                connection.execute(
                    """
                    INSERT INTO activity_logs (user_id, ip_address, method, endpoint, device_label, accessed_at)
                    VALUES (?, ?, 'LOGOUT', '/api/auth/logout', ?, ?)
                    """,
                    (
                        int(row["user_id"]),
                        request.client.host if request.client else "unknown",
                        request.headers.get("x-droid-device", "").strip()[:160],
                        _now_iso(),
                    ),
                )
            connection.commit()
    _clear_session_auth_cache()
    _clear_session_cookie(response)
    return {"status": "success"}

@router.get("/api/approvals/approve")
def approve_access_request(token: str) -> Response:
    token_hash = _token_hash(token)
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT id, email, requested_role
            FROM users
            WHERE approval_token_hash = ? AND approval_status = 'pending'
            """,
            (token_hash,),
        ).fetchone()
        if not row:
            return Response(
                "Approval link is invalid or this request was already handled.",
                media_type="text/plain",
                status_code=404,
            )
        role = "admin" if str(row["requested_role"]).lower() == "admin" else "user"
        approved_at = _now_iso()
        connection.execute(
            """
            UPDATE users
            SET approval_status = 'approved',
                role = ?,
                approved_at = ?,
                approval_token_hash = NULL
            WHERE id = ?
            """,
            (role, approved_at, int(row["id"])),
        )
        connection.commit()
    user_email = str(row["email"])
    _send_email(
        user_email,
        "Droid Cloud access approved",
        (
            "Your Droid Cloud access has been approved.\n\n"
            f"Approved role: {role}\n"
            f"You can now login here: {PUBLIC_PORTAL_URL}\n\n"
            "You are approved for this role and can manage data according to your permissions."
        ),
    )
    _send_owner_sms(f"Droid Cloud approved: {user_email} is now {role}.")
    return Response(
        f"Approved {user_email} as {role}. The user has been notified.",
        media_type="text/plain",
    )

@router.get("/api/auth/me")
def auth_me(request: Request) -> dict[str, object]:
    ensure_tables()
    user = _require_user(request)
    return {
        "id": int(user["id"]),
        "email": str(user["email"]),
        "role": str(user.get("role", "user")),
        "can_access_catalog": bool(user.get("can_access_catalog", True)),
        "can_upload_data": bool(user.get("can_upload_data", False)),
        "location_required": False if str(user.get("role", "user")).lower() == "admin" else bool(user.get("location_required", True)),
        "hidden_tabs": _normalize_hidden_tabs(user.get("hidden_tabs", [])),
        "approval_status": str(user.get("approval_status", "approved")),
    }
