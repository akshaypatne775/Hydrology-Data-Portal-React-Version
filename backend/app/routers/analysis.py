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


@router.get("/api/analysis/{project_id}/elevation")
def analysis_elevation(
    project_id: str,
    dataset_id: str,
    lat: float,
    lng: float,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Elevation requires a DTM/DSM TIFF source")
    fp = _file_fingerprint(path)
    cache_payload = {"kind": "elevation", "source": fp, "lat": round(lat, 8), "lng": round(lng, 8)}
    cache = _cache_path(safe_project_id, "elevation", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    value = _sample_raster(path, lat, lng)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": dataset_id,
        "lat": lat,
        "lng": lng,
        "elevation": value,
        "unit": "m",
        "cached": False,
    }
    _write_cache(cache, result)
    return result

@router.post("/api/analysis/{project_id}/profile")
def analysis_profile(project_id: str, payload: ProfilePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, payload.dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Profile requires a DTM/DSM TIFF source")
    samples = _interpolate_profile_points(payload.points, payload.samples)
    fp = _file_fingerprint(path)
    corridor_width_m = max(0.1, min(float(payload.corridor_width_m or 1.0), 1000.0))
    cache_payload = {
        "kind": "profile",
        "source": fp,
        "points": payload.points,
        "samples": payload.samples,
        "corridor_width_m": corridor_width_m,
    }
    cache = _cache_path(safe_project_id, "profile", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    values = []
    for sample in samples:
        try:
            elev: float | None = _sample_raster(path, sample["lat"], sample["lng"])
        except HTTPException:
            elev = None
        values.append({**sample, "elevation": elev})
    summary = _profile_summary(values, corridor_width_m)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": payload.dataset_id,
        "unit": "m",
        "points": values,
        **summary,
        "cached": False,
    }
    _write_cache(cache, result)
    return result

@router.post("/api/analysis/cross-section")
def analysis_cross_section(payload: CrossSectionPayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(payload.project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    if payload.dataset_id:
        path = _dataset_source_path(safe_project_id, payload.dataset_id)
        dataset_id = payload.dataset_id
    else:
        if not payload.dtm_file_path:
            raise HTTPException(status_code=400, detail="dataset_id or dtm_file_path is required")
        local_root = Path(LOCAL_DATA_PATH).resolve()
        path = _local_data_path_from_user_value(payload.dtm_file_path).resolve()
        try:
            path.relative_to(local_root)
        except ValueError as exc:
            raise HTTPException(status_code=403, detail="DTM path must stay inside Project_Data") from exc
        dataset_id = path.stem

    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Cross-section requires a DTM/DSM TIFF source")
    if not path.is_file():
        raise HTTPException(status_code=404, detail="DTM/DSM file not found")

    coordinates: list[object] = []
    if payload.line and payload.line.get("type") == "LineString":
        raw_coordinates = payload.line.get("coordinates")
        if isinstance(raw_coordinates, list):
            coordinates = raw_coordinates
    elif payload.coordinates:
        coordinates = payload.coordinates
    if len(coordinates) < 2:
        raise HTTPException(status_code=400, detail="LineString with at least two coordinates is required")

    fp = _file_fingerprint(path)
    samples = max(2, min(int(payload.samples or 180), 800))
    cache_payload = {
        "kind": "cross_section",
        "source": fp,
        "coordinates": coordinates,
        "samples": samples,
    }
    cache = _cache_path(safe_project_id, "cross-section", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached

    try:
        sampled = sample_cross_section(path, coordinates, samples=samples)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        _write_portal_error_log("cross_section", str(exc), {"project_id": safe_project_id, "dataset_id": dataset_id})
        raise HTTPException(status_code=500, detail="Cross-section sampling failed") from exc

    result: dict[str, object] = {
        "status": "success",
        "project_id": safe_project_id,
        "dataset_id": dataset_id,
        "unit": "m",
        **sampled,
        "cached": False,
    }
    _write_cache(cache, result)
    return result

@router.post("/api/analysis/{project_id}/volume")
def analysis_volume(project_id: str, payload: VolumePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    path = _dataset_source_path(safe_project_id, payload.dataset_id)
    if path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Volume requires a DTM/DSM TIFF source")
    points = payload.points
    if payload.circle_center and payload.circle_radius_m > 0:
        points = _circle_points(payload.circle_center, payload.circle_radius_m)
    fp = _file_fingerprint(path)
    cache_payload = {
        "kind": "single_dtm_volume",
        "source": fp,
        "points": points,
        "base_elevation": payload.base_elevation,
    }
    cache = _cache_path(safe_project_id, "single-volume", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    volume = _volume_for_raster(path, points, payload.base_elevation)
    result: dict[str, object] = {
        "status": "success",
        "dataset_id": payload.dataset_id,
        **volume,
        "cached": False,
    }
    _write_cache(cache, result)
    return result

@router.get("/api/compare/{project_id}/datasets")
def compare_datasets(project_id: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    rows = []
    for st in _project_dataset_statuses(safe_project_id):
        dataset_type = str(st.get("dataset_type") or _infer_dataset_type(str(st.get("dataset_name") or "")))
        if dataset_type not in ("dtm", "dsm", "csv"):
            continue
        raw_rel = str(st.get("raw_rel_path") or "")
        raw_path = Path(LOCAL_DATA_PATH) / raw_rel if raw_rel else None
        rows.append({
            "dataset_id": st.get("dataset_id", ""),
            "name": st.get("dataset_name", ""),
            "dataset_type": dataset_type,
            "month": st.get("month", ""),
            "status": st.get("status", ""),
            "has_source": bool(raw_path and raw_path.is_file()),
        })
    return {"datasets": rows}

@router.post("/api/compare/{project_id}/volume")
def compare_volume(project_id: str, payload: CompareVolumePayload, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    selected = [_safe_dataset_id(item) for item in payload.dataset_ids if item]
    statuses = [
        st for st in _project_dataset_statuses(safe_project_id)
        if not selected or str(st.get("dataset_id")) in selected
    ]
    statuses.sort(key=lambda st: (str(st.get("month") or ""), str(st.get("dataset_name") or "")))
    csv_rows: list[dict[str, object]] = []
    dtm_rows: list[dict[str, str]] = []
    for st in statuses:
        dtype = str(st.get("dataset_type") or _infer_dataset_type(str(st.get("dataset_name") or "")))
        if dtype == "csv":
            try:
                csv_rows.extend(_read_volume_csv(_dataset_source_path(safe_project_id, str(st.get("dataset_id")))))
            except Exception:
                continue
        elif dtype in ("dtm", "dsm"):
            dtm_rows.append(st)
    if csv_rows:
        return {"status": "success", "source": "csv", "rows": csv_rows}
    volume_rows: list[dict[str, object]] = []
    for idx in range(1, len(dtm_rows)):
        row = _dtm_volume_between(
            safe_project_id,
            str(dtm_rows[idx - 1].get("dataset_id")),
            str(dtm_rows[idx].get("dataset_id")),
        )
        row["month"] = str(dtm_rows[idx].get("month") or row.get("month") or "")
        row["label"] = f"{dtm_rows[idx - 1].get('month') or dtm_rows[idx - 1].get('dataset_name')} to {dtm_rows[idx].get('month') or dtm_rows[idx].get('dataset_name')}"
        volume_rows.append(row)
    return {"status": "success", "source": "dtm", "rows": volume_rows}

@router.post("/api/compare/{project_id}/refresh-if-changed")
def compare_refresh_if_changed(project_id: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    cache_dir = _analysis_cache_dir(safe_project_id)
    removed = 0
    for cache_file in cache_dir.glob("*.json"):
        cache_file.unlink(missing_ok=True)
        removed += 1
    return {"status": "success", "removed": removed}
