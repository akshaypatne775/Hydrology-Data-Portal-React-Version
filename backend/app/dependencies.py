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


def _require_user(request: Request) -> dict[str, object]:
    signed = request.cookies.get(SESSION_COOKIE_NAME)
    if not signed:
        raise HTTPException(status_code=401, detail="Authentication required")
    raw = _unsign_session_token(signed)
    if not raw:
        raise HTTPException(status_code=401, detail="Invalid session token")

    now_ts = int(datetime.now(timezone.utc).timestamp())
    token_hash = _token_hash(raw)
    cached = _SESSION_USER_CACHE.get(token_hash)
    if cached:
        cached_until, cached_expires_at, cached_user = cached
        if cached_until >= time.time() and cached_expires_at >= now_ts:
            return dict(cached_user)
        _SESSION_USER_CACHE.pop(token_hash, None)

    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT
                s.expires_at,
                u.id AS user_id,
                u.email,
                u.role,
                u.approval_status,
                u.can_access_catalog,
                u.can_upload_data,
                u.location_required,
                u.hidden_tabs
            FROM sessions s
            JOIN users u ON u.id = s.user_id
            WHERE s.token_hash = ?
            """,
            (token_hash,),
        ).fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Session expired")
    expires_at = int(row["expires_at"])
    if expires_at < now_ts:
        if expires_at + SESSION_RENEW_GRACE_SECONDS < now_ts:
            raise HTTPException(status_code=401, detail="Session expired")
        expires_at = now_ts + SESSION_TTL_SECONDS
        with get_db_connection() as connection:
            connection.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (expires_at, token_hash),
            )
            connection.commit()
    elif expires_at - now_ts < SESSION_REFRESH_THRESHOLD_SECONDS:
        expires_at = now_ts + SESSION_TTL_SECONDS
        with get_db_connection() as connection:
            connection.execute(
                "UPDATE sessions SET expires_at = ? WHERE token_hash = ?",
                (expires_at, token_hash),
            )
            connection.commit()
    if str(row["approval_status"] or "pending").lower() != "approved":
        raise HTTPException(status_code=403, detail="Account approval is pending")
    user = {
        "id": int(row["user_id"]),
        "email": str(row["email"]),
        "role": str(row["role"] or "user"),
        "can_access_catalog": bool(row["can_access_catalog"]),
        "can_upload_data": bool(row["can_upload_data"]),
        "location_required": False if str(row["role"] or "user").lower() == "admin" else bool(row["location_required"]),
        "hidden_tabs": _normalize_hidden_tabs(row["hidden_tabs"]),
        "approval_status": str(row["approval_status"] or "pending"),
    }
    _SESSION_USER_CACHE[token_hash] = (time.time() + SESSION_AUTH_CACHE_SECONDS, expires_at, dict(user))
    return user

def _get_optional_user(request: Request) -> dict[str, object] | None:
    try:
        return _require_user(request)
    except HTTPException:
        return None

def _require_admin(request: Request) -> dict[str, object]:
    user = _require_user(request)
    if str(user.get("role", "")).lower() != "admin":
        raise HTTPException(status_code=403, detail="Admin access required")
    return user

def verify_admin(request: Request) -> dict[str, object]:
    return _require_admin(request)

def _require_upload_user(request: Request) -> dict[str, object]:
    user = _require_user(request)
    is_admin = str(user.get("role", "")).lower() == "admin"
    if not is_admin and not bool(user.get("can_upload_data", False)):
        raise HTTPException(status_code=403, detail="Upload access required")
    return user

def _client_ip_for_limit(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for", "")
    forwarded_ip = forwarded_for.split(",", 1)[0].strip()
    return forwarded_ip or (request.client.host if request.client else "unknown")

def _enforce_rate_limit(
    request: Request,
    bucket_name: str,
    limit: int = RATE_LIMIT_HEAVY_REQUESTS,
    window_seconds: float = RATE_LIMIT_WINDOW_SECONDS,
) -> None:
    # Chunked uploads legitimately send many sequential requests. Authentication,
    # project access checks, disk limits, and completion processing still protect
    # the upload flow; the generic heavy-request limiter must not block chunks.
    if request.url.path in {"/api/upload-chunk", "/api/upload-dataset-chunk"}:
        return
    now = time.monotonic()
    cutoff = now - window_seconds
    ip_address = _client_ip_for_limit(request)
    key = f"{bucket_name}:{ip_address}"
    bucket = [stamp for stamp in _RATE_LIMIT_BUCKETS.get(key, []) if stamp >= cutoff]
    if len(bucket) >= limit:
        retry_after = max(1, int(window_seconds - (now - bucket[0])))
        raise HTTPException(
            status_code=429,
            detail=f"Too many requests. Please try again in {retry_after} seconds.",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)
    _RATE_LIMIT_BUCKETS[key] = bucket
    if len(_RATE_LIMIT_BUCKETS) > 10_000:
        for stale_key in list(_RATE_LIMIT_BUCKETS):
            _RATE_LIMIT_BUCKETS[stale_key] = [
                stamp for stamp in _RATE_LIMIT_BUCKETS[stale_key] if stamp >= cutoff
            ]
            if not _RATE_LIMIT_BUCKETS[stale_key]:
                _RATE_LIMIT_BUCKETS.pop(stale_key, None)
