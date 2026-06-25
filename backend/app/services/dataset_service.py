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


def _infer_dataset_type(name: str) -> str:
    lowered = name.lower()
    suffix = Path(lowered).suffix
    if "dtm" in lowered or "dem" in lowered:
        return "dtm"
    if "dsm" in lowered:
        return "dsm"
    if "ortho" in lowered:
        return "ortho"
    if suffix == ".csv":
        return "csv"
    if suffix == ".zip":
        return "3dmodel"
    if suffix in (".kml", ".geojson"):
        return "vector"
    if suffix == ".dwg":
        return "cad"
    if suffix == ".pdf":
        return "reports"
    if suffix in (".tif", ".tiff"):
        return "ortho"
    if suffix in (".las", ".laz"):
        return "pointcloud"
    return "dataset"

def _raster_layer_type(dataset_type: str, name: str = "") -> str:
    normalized = _normalize_dataset_type(dataset_type, name)
    if normalized == "dtm":
        return "DTM"
    if normalized == "dsm":
        return "DSM"
    if normalized == "ortho":
        return "Ortho"
    return "cog"

def _normalize_dataset_type(value: str, fallback_name: str = "") -> str:
    normalized = (value or "").strip().lower().replace(" ", "")
    aliases = {
        "orthomosaic": "ortho",
        "ortho": "ortho",
        "dtm": "dtm",
        "dem": "dtm",
        "dsm": "dsm",
        "pointcloud": "pointcloud",
        "3dmodel": "3dmodel",
        "3dtiles": "3dmodel",
        "cesium3dtiles": "3dmodel",
        "las": "pointcloud",
        "laz": "pointcloud",
        "csv": "csv",
        "vector": "vector",
        "kml": "vector",
        "geojson": "vector",
        "cad": "cad",
        "dwg": "cad",
        "pdf": "reports",
        "report": "reports",
        "reports": "reports",
    }
    return aliases.get(normalized) or _infer_dataset_type(fallback_name)

def _read_dataset_manifest(path: Path) -> dict[str, str]:
    manifest = path if path.name == _dataset_manifest_name() else _manifest_target_for(path)
    if not manifest.is_file():
        return {}
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError, TypeError):
        return {}
    return {}

def _write_dataset_manifest(path: Path, payload: dict[str, object]) -> None:
    try:
        target = _manifest_target_for(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        current = _read_dataset_manifest(target)
        current.update({str(k): str(v) for k, v in payload.items() if v is not None})
        current["updated_at"] = _now_iso()
        target.write_text(json.dumps(current, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass

def _status_manifest_payload(project_id: str, dataset_id: str, st: dict[str, str]) -> dict[str, str]:
    return {
        "project_id": project_id,
        "dataset_id": dataset_id,
        "display_name": str(st.get("dataset_name") or dataset_id),
        "dataset_type": str(st.get("dataset_type") or ""),
        "raw_rel_path": str(st.get("raw_rel_path") or ""),
        "rel_path": str(st.get("rel_path") or st.get("tiles_rel_path") or st.get("cog_rel_path") or ""),
        "source_name": str(st.get("source_name") or st.get("dataset_name") or dataset_id),
    }

def _write_status_manifests(project_id: str, dataset_id: str, st: dict[str, str]) -> None:
    payload = _status_manifest_payload(project_id, dataset_id, st)
    _write_dataset_manifest(_dataset_dir(project_id, dataset_id), payload)
    for key in (
        "raw_rel_path",
        "tiles_rel_path",
        "tileset_rel_path",
        "vector_rel_path",
        "model_rel_path",
        "cog_rel_path",
    ):
        rel = str(st.get(key) or "").strip().replace("\\", "/").lstrip("/")
        if not rel or ".." in rel:
            continue
        target = Path(LOCAL_DATA_PATH) / rel
        if target.exists():
            _write_dataset_manifest(target, payload)
    tile_folder = str(st.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = _get_project_dirs(project_id)
        for target in (
            processed_root / tile_folder,
            processed_root / _dataset_type_folder(str(st.get("dataset_type") or "")) / tile_folder,
            _ept_dataset_dir(project_id, tile_folder),
        ):
            if target.exists():
                _write_dataset_manifest(target, payload)

def _sync_dataset_metadata_to_processing_job(project_id: str, dataset_id: str, st: dict[str, str]) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    matched = False
    for item in current:
        if item.get("job_id") != dataset_id:
            continue
        matched = True
        item["file_name"] = str(st.get("dataset_name") or item.get("file_name") or dataset_id)
        item["status"] = str(st.get("status") or item.get("status") or "Completed")
        item["updated_at"] = str(st.get("updated_at") or _now_iso())
        for key in (
            "height_offset",
            "dataset_type",
            "month",
            "raw_rel_path",
            "tiles_rel_path",
            "tileset_rel_path",
            "cog_path",
            "cog_rel_path",
            "rescale_min",
            "rescale_max",
            "bounds_wgs84",
        ):
            if key in st:
                item[key] = str(st.get(key) or "")
        break
    if not matched:
        current.insert(
            0,
            {
                "job_id": dataset_id,
                "kind": str(st.get("dataset_type") or "dataset"),
                "file_name": str(st.get("dataset_name") or dataset_id),
                "status": str(st.get("status") or "Completed"),
                "updated_at": str(st.get("updated_at") or _now_iso()),
                "height_offset": str(st.get("height_offset") or ""),
                "dataset_type": str(st.get("dataset_type") or ""),
            },
        )
    jobs[project_id] = current[:200]
    _write_processing_jobs(jobs)

def _write_dataset_status(project_id: str, dataset_id: str, payload: dict[str, str]) -> None:
    status_path = _dataset_status_file(project_id, dataset_id)
    status_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        status_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
        _write_status_manifests(project_id, dataset_id, payload)
    except OSError:
        pass
    catalog_service.mirror_dataset_status(project_id, dataset_id, payload)

def _read_dataset_status(project_id: str, dataset_id: str) -> dict[str, str] | None:
    status_path = _dataset_status_file(project_id, dataset_id)
    if not status_path.is_file():
        return None
    try:
        data = json.loads(status_path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return None
    return None

def _deep_rename_dataset_artifacts(project_id: str, dataset_id: str, st: dict[str, str], display_name: str) -> dict[str, str]:
    updated = dict(st)
    local_root = Path(LOCAL_DATA_PATH).resolve()
    for key in ("raw_rel_path", "vector_rel_path", "model_rel_path", "cog_rel_path"):
        rel = str(updated.get(key) or "").strip().replace("\\", "/").lstrip("/")
        if not rel or ".." in rel:
            continue
        current = (Path(LOCAL_DATA_PATH) / rel).resolve()
        if not current.exists() or not current.is_relative_to(local_root):
            continue
        renamed = _safe_rename_dataset_path(current, display_name)
        try:
            updated[key] = renamed.relative_to(local_root).as_posix()
            if key == "cog_rel_path":
                updated["cog_path"] = str(renamed)
        except ValueError:
            pass

    tile_folder = str(updated.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = _get_project_dirs(project_id)
        for key, current in (
            ("tiles_rel_path", Path(LOCAL_DATA_PATH) / str(updated.get("tiles_rel_path") or "")),
            ("tileset_rel_path", Path(LOCAL_DATA_PATH) / str(updated.get("tileset_rel_path") or "")),
        ):
            if str(updated.get(key) or "").strip() and current.exists() and current.resolve().is_relative_to(local_root):
                renamed = _safe_rename_dataset_path(current.resolve(), display_name)
                try:
                    updated[key] = renamed.relative_to(local_root).as_posix()
                except ValueError:
                    pass
        for current in (
            processed_root / tile_folder,
            processed_root / _dataset_type_folder(str(updated.get("dataset_type") or "")) / tile_folder,
            _ept_dataset_dir(project_id, tile_folder),
            _legacy_ept_pointcloud_dataset_dir(project_id, tile_folder),
            _legacy_ept_dataset_dir(project_id, tile_folder),
        ):
            if current.exists():
                renamed = _safe_rename_dataset_path(current, display_name)
                updated["tile_folder"] = renamed.name
                try:
                    source_marker = renamed / ".source_name.txt"
                    if source_marker.exists() or str(updated.get("dataset_type") or "").lower() == "pointcloud":
                        source_marker.write_text(display_name, encoding="utf-8")
                except OSError:
                    pass
                break
    return updated

def _delete_dataset_artifacts(project_id: str, dataset_id: str, st: dict[str, str]) -> int:
    removed = 0
    if catalog_service.catalog_db_enabled():
        removed += catalog_service.delete_asset_artifacts(
            project_id,
            dataset_id,
            local_data_path=LOCAL_DATA_PATH,
        )
    for key in ("raw_rel_path", "tiles_rel_path", "tileset_rel_path", "vector_rel_path", "model_rel_path", "cog_rel_path"):
        rel = str(st.get(key) or "").strip().replace("\\", "/").lstrip("/")
        if rel and ".." not in rel:
            removed += _safe_remove_dataset_path(Path(LOCAL_DATA_PATH) / rel)
    tile_folder = str(st.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = _get_project_dirs(project_id)
        for candidate in (
            processed_root / tile_folder,
            processed_root / _dataset_type_folder(str(st.get("dataset_type") or "")) / tile_folder,
        ):
            if candidate.exists():
                removed += _safe_remove_dataset_path(candidate)
    _safe_remove_dataset_path(_dataset_dir(project_id, dataset_id))
    _remove_processing_job(project_id, dataset_id)
    _invalidate_project_files_cache(project_id)
    return removed


