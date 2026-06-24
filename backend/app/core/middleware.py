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


class Debug404Middleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if response.status_code == 404 and request.url.path.startswith("/data/"):
            rel = request.url.path.replace("/data/", "", 1).lstrip("/")
            expected_path = Path(LOCAL_DATA_PATH) / rel
            print(
                f"[DEBUG 404] Frontend requested: {request.url.path} | "
                f"looked for: {expected_path.resolve()} | exists: {expected_path.exists()}",
                flush=True,
            )
        return response

class ActivityTrackingMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        if request.url.path in {"/api/auth/logout", "/api/version", "/health"}:
            return response
        try:
            user = _get_optional_user(request)
            forwarded_for = request.headers.get("x-forwarded-for", "")
            forwarded_ip = forwarded_for.split(",", 1)[0].strip()
            ip_address = forwarded_ip or (request.client.host if request.client else "unknown")
            device_label = request.headers.get("x-droid-device", "").strip()[:160]
            lat_raw = request.headers.get("x-droid-lat", "").strip()
            lng_raw = request.headers.get("x-droid-lng", "").strip()
            accuracy_raw = request.headers.get("x-droid-location-accuracy", "").strip()
            latitude = float(lat_raw) if lat_raw else None
            longitude = float(lng_raw) if lng_raw else None
            location_accuracy = float(accuracy_raw) if accuracy_raw else None
            with get_db_connection() as connection:
                connection.execute(
                    """
                    INSERT INTO activity_logs (
                        user_id, ip_address, method, endpoint, device_label,
                        latitude, longitude, location_accuracy, accessed_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        int(user["id"]) if user else None,
                        ip_address,
                        request.method,
                        request.url.path,
                        device_label,
                        latitude,
                        longitude,
                        location_accuracy,
                        _now_iso(),
                    ),
                )
                connection.commit()
        except Exception as exc:  # noqa: BLE001
            print(f"Activity tracking failed: {exc}")
        return response

class ProtectedDataPathMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path.replace("\\", "/").lower()
        if path.startswith("/api/titiler/") or path.startswith("/api/dji-terra/") or path.startswith("/api/ortho-cog/"):
            source_url = request.query_params.get("url")
            if source_url:
                try:
                    local_root = Path(LOCAL_DATA_PATH).resolve()
                    target = _local_data_path_from_user_value(source_url)
                    if target != local_root and not target.is_relative_to(local_root):
                        return Response(status_code=403)
                except Exception:  # noqa: BLE001
                    return Response(status_code=403)
        user_upload_path = (
            path.startswith("/api/upload")
            or path.startswith("/api/complete-upload")
            or path.startswith("/api/complete-dataset-upload")
            or path == "/api/process-dataset"
            or path == "/api/process-pointcloud"
            or path == "/api/dataset-metadata"
        )
        admin_only_upload = (
            "metadata-probe" in path
            or re.match(r"^/api/datasets/[^/]+/(sync|open-manual-folder)$", path) is not None
            or ("manual" in path and ("folder" in path or "sync" in path))
        )
        protected_upload = user_upload_path or admin_only_upload
        if protected_upload and request.method.upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            try:
                user = _require_user(request)
                is_admin = str(user.get("role", "")).lower() == "admin"
                can_upload = bool(user.get("can_upload_data", False))
                if admin_only_upload and not is_admin:
                    return Response(status_code=403)
                if user_upload_path and not (is_admin or can_upload):
                    return Response(status_code=403)
            except HTTPException:
                return Response(status_code=403)
        if path.startswith("/data/") and ("/raw/" in path or path.endswith(".pdf")):
            # Raw assets and PDFs must be served through authenticated /api endpoints
            # so project paths are not exposed in the browser.
            try:
                _require_user(request)
            except HTTPException:
                return Response(status_code=404)
            return Response(status_code=404)
        return await call_next(request)
