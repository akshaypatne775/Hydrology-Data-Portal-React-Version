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


@router.post("/api/process-dataset", response_model=ProcessDatasetOut)
async def process_dataset(
    request: Request,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    project_id: str = Form(...),
    dataset_type: str = Form(""),
    month: str = Form(""),
    created_at: str = Form(""),
    epsg: str = Form(""),
) -> ProcessDatasetOut:
    user = _require_upload_user(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(file.filename or "")
    ext = Path(safe_name).suffix.lower()
    if ext not in (".tif", ".tiff", ".csv", ".zip", ".kml", ".geojson", ".dwg", ".pdf"):
        raise HTTPException(status_code=400, detail="Only .tif/.tiff/.csv/.zip/.kml/.geojson/.dwg/.pdf dataset files are supported")
    normalized_type = _normalize_dataset_type(dataset_type, safe_name)
    if ext in (".tif", ".tiff") and normalized_type == "3dmodel":
        normalized_type = _infer_dataset_type(safe_name)
        if normalized_type == "3dmodel":
            normalized_type = "ortho"
    if ext == ".zip" and normalized_type != "3dmodel":
        raise HTTPException(
            status_code=400,
            detail=(
                "ZIP uploads are supported only for 3D Model tilesets. "
                "Upload DTM, DSM, and Ortho datasets as .tif or .tiff files."
            ),
        )
    submitted_date = (created_at or "").strip()
    submitted_month = (month or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_month):
        submitted_date = submitted_date or submitted_month
        submitted_month = submitted_month[:7]
    ddmmyyyy_match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", submitted_month)
    if ddmmyyyy_match:
        day, month_part, year = ddmmyyyy_match.groups()
        submitted_date = submitted_date or f"{year}-{int(month_part):02d}-{int(day):02d}"
        submitted_month = submitted_date[:7]
    try:
        normalized_month = _normalize_month(submitted_month)
    except HTTPException as exc:
        if exc.status_code == 400 and re.fullmatch(r"\d{4}-\d{2}-\d{2}", submitted_month):
            submitted_date = submitted_date or submitted_month
            normalized_month = submitted_month[:7]
        else:
            raise

    manual_epsg = _normalize_epsg_input(locals().get("epsg", "")) if locals().get("ext", "") in (".tif", ".tiff") else ""

    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")

    raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
    meta_dir = _dataset_dir(safe_project_id, dataset_id)
    meta_dir.mkdir(parents=True, exist_ok=True)

    input_path = raw_dir / f"{tile_output_folder}{ext}"

    content_length = request.headers.get("content-length")
    expected_bytes = int(content_length) if content_length and content_length.isdigit() else 0
    if ext in (".tif", ".tiff") and expected_bytes > DIRECT_RASTER_UPLOAD_LIMIT_BYTES:
        raise HTTPException(
            status_code=413,
            detail=(
                "Large raster upload must use chunked upload. "
                "Refresh the portal and try again, or use Sync Manual Folders for very large local files."
            ),
        )
    _ensure_disk_space_for_bytes(raw_dir, max(expected_bytes * 2, 512 * 1024 * 1024))

    output_tile_dir = processed_dir / tile_output_folder
    if ext not in (".csv", ".pdf"):
        output_tile_dir.mkdir(parents=True, exist_ok=True)

    try:
        with open(input_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Failed to store dataset: {exc}") from exc
    finally:
        await file.close()

    raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
    if ext == ".pdf":
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": "reports",
                "layer_type": "Reports",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "report_rel_path": raw_rel,
                "processed_size_bytes": str(input_path.stat().st_size),
                "processed_size": _format_size_bytes(input_path.stat().st_size),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "report",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{raw_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="PDF report uploaded and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{raw_rel}",
        )

    if ext in (".kml", ".geojson", ".dwg"):
        asset_type = "cad" if ext == ".dwg" else "vector"
        asset_root = processed_dir / tile_output_folder
        asset_root.mkdir(parents=True, exist_ok=True)
        asset_path = asset_root / safe_name
        try:
            shutil.copyfile(input_path, asset_path)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to prepare vector asset: {exc}") from exc
        asset_rel = asset_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        asset_size_bytes = calculate_folder_size(asset_root)
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": asset_type,
                "layer_type": "CAD" if asset_type == "cad" else "Vector",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "vector_rel_path": asset_rel,
                "processed_size_bytes": str(asset_size_bytes),
                "processed_size": _format_size_bytes(asset_size_bytes),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": asset_type,
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{asset_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="CAD asset saved." if asset_type == "cad" else "Vector layer uploaded and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{asset_rel}",
        )

    if ext == ".zip":
        print(f"Extracting 3D Tiles ZIP {safe_name}...")
        if output_tile_dir.exists():
            shutil.rmtree(output_tile_dir)
        output_tile_dir.mkdir(parents=True, exist_ok=True)
        _safe_extract_zip(input_path, output_tile_dir)
        tileset_root = _find_extracted_tileset_root(output_tile_dir)
        tileset_rel = (tileset_root / "tileset.json").resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        model_rel = tileset_root.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        model_size_bytes = calculate_folder_size(tileset_root)
        tileset_url = f"{str(request.base_url).rstrip('/')}/data/{tileset_rel}"
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": Path(safe_name).stem,
                "tile_folder": tile_output_folder,
                "dataset_type": "3dmodel",
                "layer_type": "3DModel",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "tiles_rel_path": model_rel,
                "tileset_rel_path": tileset_rel,
                "processed_size_bytes": str(model_size_bytes),
                "processed_size": _format_size_bytes(model_size_bytes),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{tileset_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="3D model ZIP extracted and ready.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=Path(safe_name).stem,
            cog_path="",
            cog_tile_url_template=tileset_url,
        )

    if ext == ".csv":
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": safe_name,
                "tile_folder": "",
                "dataset_type": "csv",
                "month": normalized_month,
                "created_at": submitted_date,
                "raw_rel_path": raw_rel,
                "processed_size_bytes": str(input_path.stat().st_size),
                "processed_size": _format_size_bytes(input_path.stat().st_size),
            },
        )
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "dataset",
                "file_name": safe_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{raw_rel}",
            },
        )
        _invalidate_project_files_cache(safe_project_id)
        return ProcessDatasetOut(
            status="success",
            message="CSV dataset uploaded and ready for comparison.",
            project_id=safe_project_id,
            dataset_id=dataset_id,
            dataset_name=safe_name,
            cog_path="",
            cog_tile_url_template="",
        )

    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Uploading",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": safe_name,
            "tile_folder": tile_output_folder,
            "dataset_type": normalized_type,
            "layer_type": _raster_layer_type(normalized_type, safe_name),
            "month": normalized_month,
            "created_at": submitted_date,
            "raw_rel_path": raw_rel,
            "cog_path": str((output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()),
        },
    )
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": dataset_id,
            "kind": "dataset",
            "file_name": safe_name,
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)

    background_tasks.add_task(
        process_dataset_background,
        safe_project_id,
        dataset_id,
        str(input_path),
        safe_name,
        str(output_tile_dir),
        tile_output_folder,
        manual_epsg,
    )

    pending_cog_path = (output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()
    tile_template = _titiler_tile_url_template(
        str(request.base_url),
        str(pending_cog_path),
        _raster_layer_type(normalized_type, safe_name),
    )
    return ProcessDatasetOut(
        status="success",
        message="Dataset uploaded. COG conversion started in background.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=safe_name,
        cog_path=str(pending_cog_path),
        cog_tile_url_template=tile_template,
    )

@router.post("/api/datasets/{project_id}/generate-contours", response_model=ProcessDatasetOut)
async def generate_contours(
    project_id: str,
    payload: ContourGeneratePayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> ProcessDatasetOut:
    user = _require_user(request)
    _enforce_rate_limit(request, "generate-contours")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    if payload.interval <= 0:
        raise HTTPException(status_code=400, detail="Contour interval must be greater than 0")
    if payload.dataset_id:
        source_path = _dataset_source_path(safe_project_id, payload.dataset_id)
    else:
        raw_rel = payload.source_tif.replace("\\", "/").lstrip("/")
        if ".." in raw_rel:
            raise HTTPException(status_code=400, detail="Invalid source_tif")
        source_path = (Path(LOCAL_DATA_PATH) / raw_rel).resolve()
        local_root = Path(LOCAL_DATA_PATH).resolve()
        if local_root not in source_path.parents or not source_path.is_file():
            raise HTTPException(status_code=404, detail="Source DEM .tif not found")
    if source_path.suffix.lower() not in (".tif", ".tiff"):
        raise HTTPException(status_code=400, detail="Source must be a DEM .tif/.tiff")

    source_name = source_path.stem
    dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", f"{source_name}-contours-{payload.interval:g}m").strip("-")
    dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
    _, processed_dir = get_project_dataset_type_dirs(safe_project_id, "vector")
    output_dir = processed_dir / dataset_id
    output_geojson = output_dir / f"{dataset_stem}.geojson"
    rel = output_geojson.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()

    _write_dataset_status(
        safe_project_id,
        dataset_id,
        {
            "status": "Processing",
            "updated_at": _now_iso(),
            "dataset_id": dataset_id,
            "dataset_name": f"{source_name} contours",
            "tile_folder": "",
            "dataset_type": "vector",
            "layer_type": "Vector",
            "vector_rel_path": rel,
        },
    )
    _upsert_processing_job(
        safe_project_id,
        {
            "job_id": dataset_id,
            "kind": "vector",
            "file_name": f"{source_name} contours",
            "status": "Processing",
            "updated_at": _now_iso(),
        },
    )
    _invalidate_project_files_cache(safe_project_id)
    background_tasks.add_task(
        process_contours_background,
        safe_project_id,
        dataset_id,
        str(source_path),
        str(output_geojson),
        payload.interval,
        f"{source_name} contours",
    )
    return ProcessDatasetOut(
        status="success",
        message="Contour generation started.",
        project_id=safe_project_id,
        dataset_id=dataset_id,
        dataset_name=f"{source_name} contours",
        cog_path="",
        cog_tile_url_template=f"{str(request.base_url).rstrip('/')}/data/{rel}",
    )

@router.post("/api/datasets/{project_id}/sync")
def sync_manual_datasets(
    project_id: str, request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    _, processed_dir = get_project_dirs(safe_project_id)
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "_dataset_jobs"
    jobs_root.mkdir(parents=True, exist_ok=True)

    tracked_folders: set[str] = set()
    tracked_dataset_by_key: dict[str, str] = {}
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        st = _read_dataset_status(safe_project_id, job_dir.name)
        if not st:
            continue
        folder = (st.get("tile_folder") or "").strip()
        if folder:
            tracked_folders.add(folder)
            tracked_dataset_by_key[folder] = job_dir.name
        rel = (st.get("tiles_rel_path") or "").strip()
        if rel:
            tracked_folders.add(rel)
            tracked_dataset_by_key[rel] = job_dir.name
        cog_rel = (st.get("cog_rel_path") or "").strip()
        if cog_rel:
            tracked_folders.add(cog_rel)
            tracked_dataset_by_key[cog_rel] = job_dir.name

    found_new = 0
    manual_copc_dirs = {path.parent for path in _project_copc_assets(safe_project_id)}

    candidates: list[tuple[Path, str, str, str]] = [
        *[(item, "pointcloud", "pointcloud", "pointcloud_copc") for item in sorted(manual_copc_dirs, key=lambda p: p.name.lower())],
        *[
            (
                item,
                _raster_layer_type(_infer_dataset_type(f"{item.parent.name} {item.name}"), item.name),
                _infer_dataset_type(f"{item.parent.name} {item.name}"),
                "cog",
            )
            for item in _candidate_processed_cog_files(processed_dir)
        ],
        *[(item, _raster_layer_type(_infer_dataset_type(item.name), item.name), _infer_dataset_type(item.name), "folder") for item in _candidate_processed_tile_dirs(processed_dir)],
        *[(item, "3DModel", "3dmodel", "folder") for item in _candidate_processed_model_dirs(processed_dir)],
    ]
    for item, layer_kind, dataset_type, asset_kind in candidates:
        rel_path = item.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        folder_name = _display_model_folder_name(item, processed_dir) if layer_kind == "3DModel" else item.name
        if asset_kind == "cog":
            folder_name = folder_name.replace(".cog.tiff", ".tiff").replace(".cog.tif", ".tif").replace("_cog.tiff", ".tiff").replace("_cog.tif", ".tif")
        if asset_kind.startswith("pointcloud_"):
            dataset_type = "pointcloud"
            layer_kind = "pointcloud"
        elif layer_kind != "3DModel":
            dataset_type = _normalize_dataset_type(dataset_type, folder_name)
            layer_kind = _raster_layer_type(dataset_type, folder_name)
        if folder_name in tracked_folders or rel_path in tracked_folders:
            existing_dataset_id = tracked_dataset_by_key.get(rel_path) or tracked_dataset_by_key.get(folder_name)
            if existing_dataset_id and asset_kind.startswith("pointcloud_"):
                existing_status = _read_dataset_status(safe_project_id, existing_dataset_id) or {}
                if (
                    str(existing_status.get("dataset_type") or "").lower() != "pointcloud"
                    or str(existing_status.get("layer_type") or "").lower() != "pointcloud"
                    or not str(existing_status.get("viewer_type") or "").strip()
                ):
                    viewer_type = "copc"
                    viewer_url = _copc_viewer_url("", safe_project_id, folder_name, folder_name)
                    _write_dataset_status(
                        safe_project_id,
                        existing_dataset_id,
                        {
                            **existing_status,
                            "status": "Web-Ready",
                            "updated_at": _now_iso(),
                            "dataset_type": "pointcloud",
                            "layer_type": "pointcloud",
                            "viewer_type": viewer_type,
                            "tiles_rel_path": rel_path,
                        },
                    )
                    _upsert_processing_job(
                        safe_project_id,
                        {
                            "job_id": existing_dataset_id,
                            "kind": "pointcloud",
                            "file_name": folder_name,
                            "status": "Completed",
                            "updated_at": _now_iso(),
                            "result_url": viewer_url,
                            "viewer_type": viewer_type,
                            "dataset_type": "pointcloud",
                            "layer_type": "pointcloud",
                            "tiles_rel_path": rel_path,
                        },
                    )
            elif existing_dataset_id and asset_kind == "cog":
                existing_status = _read_dataset_status(safe_project_id, existing_dataset_id) or {}
                if not existing_status.get("bounds_wgs84") or not existing_status.get("source_crs"):
                    raster_metadata = _read_raster_manual_metadata(item, dataset_type)
                    if raster_metadata:
                        _write_dataset_status(
                            safe_project_id,
                            existing_dataset_id,
                            {
                                **existing_status,
                                **raster_metadata,
                                "updated_at": _now_iso(),
                            },
                        )
            continue

        dataset_id = _safe_dataset_id(
            f"manual-{re.sub(r'[^A-Za-z0-9._-]+', '-', folder_name)[:48]}-{secrets.token_hex(4)}",
        )
        raster_metadata = _read_raster_manual_metadata(item, dataset_type) if asset_kind == "cog" else {}
        _write_dataset_status(
            safe_project_id,
            dataset_id,
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": folder_name,
                "tile_folder": folder_name,
                "dataset_type": dataset_type,
                "layer_type": layer_kind,
                "viewer_type": "copc" if asset_kind == "pointcloud_copc" else "",
                "month": "",
                "raw_rel_path": "",
                "tiles_rel_path": "" if asset_kind == "cog" else rel_path,
                "cog_path": str(item.resolve()) if asset_kind == "cog" else "",
                "cog_rel_path": rel_path if asset_kind == "cog" else "",
                **raster_metadata,
            },
        )
        result_url = f"/data/{rel_path}/tileset.json" if layer_kind == "3DModel" else f"/data/{rel_path}"
        if asset_kind == "pointcloud_copc":
            result_url = _copc_viewer_url("", safe_project_id, folder_name, folder_name)
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": dataset_id,
                "kind": "pointcloud" if asset_kind.startswith("pointcloud_") else "dataset",
                "file_name": folder_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": result_url,
                "viewer_type": "copc" if asset_kind == "pointcloud_copc" else "",
                "dataset_type": dataset_type,
                "layer_type": layer_kind,
                "tiles_rel_path": "" if asset_kind == "cog" else rel_path,
                "cog_path": str(item.resolve()) if asset_kind == "cog" else "",
                "cog_rel_path": rel_path if asset_kind == "cog" else "",
                **raster_metadata,
            },
        )
        tracked_folders.add(folder_name)
        tracked_folders.add(rel_path)
        found_new += 1

    _invalidate_project_files_cache(safe_project_id)
    return {
        "status": "success",
        "message": f"Found {found_new} manual datasets",
        "new_count": str(found_new),
    }

@router.post("/api/datasets/{project_id}/open-manual-folder")
def open_manual_dataset_folder(project_id: str, request: Request) -> dict[str, str]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    _, processed_dir = get_project_dirs(safe_project_id)
    folder = processed_dir.resolve()
    try:
        if os.name == "nt":
            os.startfile(str(folder))  # type: ignore[attr-defined]
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(folder)], check=False)
        else:
            subprocess.run(["xdg-open", str(folder)], check=False)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {exc}") from exc
    return {
        "status": "success",
        "message": "Manual tiles folder opened.",
        "folder_path": str(folder),
    }

@router.get("/api/datasets/{project_id}/{tile_folder:path}/crop-mask")
def get_crop_mask(project_id: str, tile_folder: str, request: Request) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    record = _get_crop_mask(safe_project_id, safe_tile_folder)
    if not record:
        return {"status": "none", "points": []}
    try:
        points = json.loads(record["points_json"])
    except json.JSONDecodeError:
        points = []
    if not isinstance(points, list):
        points = []
    return {
        "status": "success",
        "source": record["source"],
        "updated_at": record["updated_at"],
        "points": points,
    }

@router.post("/api/datasets/{project_id}/{tile_folder:path}/crop-mask/kml")
async def save_crop_mask_from_kml(
    project_id: str,
    tile_folder: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    _, processed_dir = get_project_dirs(safe_project_id)
    if not (processed_dir / safe_tile_folder).is_dir():
        raise HTTPException(status_code=404, detail="Tile folder not found")
    try:
        raw = await file.read()
        text = raw.decode("utf-8", errors="replace")
    finally:
        await file.close()
    points = _extract_kml_points(text)
    _save_crop_mask(safe_project_id, safe_tile_folder, "kml", points)
    return {"status": "success", "source": "kml", "points": points}

@router.post("/api/datasets/{project_id}/{tile_folder:path}/crop-mask/draw")
def save_crop_mask_from_draw(
    project_id: str,
    tile_folder: str,
    payload: CropMaskPayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_tile_folder = _safe_tile_folder_name(tile_folder)
    _, processed_dir = get_project_dirs(safe_project_id)
    if not (processed_dir / safe_tile_folder).is_dir():
        raise HTTPException(status_code=404, detail="Tile folder not found")
    points = _normalize_crop_points(payload.points)
    _save_crop_mask(safe_project_id, safe_tile_folder, "draw", points)
    return {"status": "success", "source": "draw", "points": points}

@router.post("/api/dataset-metadata")
async def dataset_metadata(
    request: Request,
    file: UploadFile = File(...),
    project_id: str = Form(...),
) -> dict[str, str]:
    user = _require_upload_user(request)
    _enforce_rate_limit(request, "upload")
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = _safe_dataset_upload_basename(file.filename or "")
    probe_dir = Path(LOCAL_DATA_PATH) / "uploads" / "metadata_probe" / safe_project_id
    probe_dir.mkdir(parents=True, exist_ok=True)
    probe_path = probe_dir / f"{secrets.token_hex(8)}-{safe_name}"
    try:
        with open(probe_path, "wb") as out_f:
            shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
    finally:
        await file.close()
    epsg = _detect_epsg_from_file(probe_path) or ""
    probe_path.unlink(missing_ok=True)
    return {"filename": safe_name, "epsg": epsg}

@router.get("/api/dataset-status/{project_id}/{dataset_id}")
def dataset_status(project_id: str, dataset_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    status = _read_dataset_status(safe_project_id, safe_dataset_id)
    if not status:
        for job in _read_processing_jobs().get(safe_project_id, []):
            if isinstance(job, dict) and str(job.get("job_id") or "") == safe_dataset_id:
                return {
                    "status": str(job.get("status") or "Processing"),
                    "dataset_id": safe_dataset_id,
                    "dataset_name": str(job.get("file_name") or safe_dataset_id),
                    "stage": str(job.get("stage") or "Waiting for processor"),
                    "progress_percent": str(job.get("progress_percent") or "45"),
                    "eta_seconds": str(job.get("eta_seconds") or ""),
                    "updated_at": str(job.get("updated_at") or _now_iso()),
                }
        return {
            "status": "Processing",
            "dataset_id": safe_dataset_id,
            "dataset_name": safe_dataset_id,
            "stage": "Waiting for processor",
            "progress_percent": "45",
            "eta_seconds": "",
            "updated_at": _now_iso(),
        }

    base = str(request.base_url).rstrip("/")
    tiles_rel = status.get("tiles_rel_path", "").strip()
    if str(status.get("dataset_type") or "").lower() in ("3dmodel", "3dtiles"):
        tileset_rel = status.get("tileset_rel_path", "").strip() or f"{tiles_rel.rstrip('/')}/tileset.json"
        status["cog_tile_url_template"] = f"{base}/data/{tileset_rel}"
        status["layer_type"] = "3DModel"
    elif tiles_rel:
        status["cog_tile_url_template"] = f"{base}/data/{tiles_rel}/{{z}}/{{x}}/{{y}}.png"
    else:
        cog_path = status.get("cog_path", "")
        if not cog_path and status.get("cog_rel_path", ""):
            cog_path = str((Path(LOCAL_DATA_PATH) / status.get("cog_rel_path", "")).resolve())
        if cog_path:
            status["cog_tile_url_template"] = _titiler_tile_url_template(
                base,
                cog_path,
                str(status.get("layer_type") or _raster_layer_type(str(status.get("dataset_type") or ""), str(status.get("dataset_name") or ""))),
                str(status.get("rescale_min") or ""),
                str(status.get("rescale_max") or ""),
            )
    return status

@router.get("/api/datasets/{project_id}/{dataset_id}/grid-export")
def export_dataset_grid(
    project_id: str,
    dataset_id: str,
    request: Request,
    interval: float = 2.0,
    format: str = "csv",
):
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    export_format = format.lower().strip()
    if export_format not in {"csv", "dxf"}:
        raise HTTPException(status_code=400, detail="Grid export format must be csv or dxf.")

    dataset_path, st = _grid_export_raster_path(safe_project_id, safe_dataset_id)
    _, _, point_count = _validate_grid_export_request(dataset_path, interval)
    display_name = Path(st.get("dataset_name") or dataset_path.stem).stem
    safe_name = _safe_export_stem(display_name, safe_dataset_id)
    interval_token = str(interval).replace(".", "p")
    output_name = f"{safe_name}_grid_{interval_token}m.{export_format}"
    output_path = _grid_export_output_path(safe_project_id, safe_dataset_id, output_name)
    if not _grid_export_is_current(output_path, dataset_path, interval, export_format):
        _generate_grid_export_file(output_path, dataset_path, interval, export_format)
        _write_grid_export_metadata(
            output_path,
            {
                "name": output_name,
                "kind": "Generated Grid Export",
                "dataset_id": safe_dataset_id,
                "dataset_name": str(st.get("dataset_name") or display_name),
                "dataset_type": str(st.get("dataset_type") or ""),
                "source_path": str(dataset_path.resolve()),
                "source_mtime_ns": str(dataset_path.stat().st_mtime_ns),
                "interval": float(interval),
                "format": export_format,
                "point_count": point_count,
                "created_at": _now_iso(),
            },
        )
        _invalidate_project_files_cache(safe_project_id)

    return FileResponse(
        str(output_path),
        media_type="text/csv" if export_format == "csv" else "application/dxf",
        filename=output_name,
        content_disposition_type="attachment",
    )

@router.get("/api/datasets/{project_id}/{dataset_name}/bounds")
def get_dataset_bounds(project_id: str, dataset_name: str, request: Request) -> dict[str, list[float] | None]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_name = dataset_name.strip()
    if not safe_dataset_name or "/" in safe_dataset_name or "\\" in safe_dataset_name or ".." in safe_dataset_name:
        raise HTTPException(status_code=400, detail="Invalid dataset_name")
    for st in _project_dataset_statuses(safe_project_id):
        dataset_id = str(st.get("dataset_id") or "")
        status_name = str(st.get("dataset_name") or "")
        tile_folder = str(st.get("tile_folder") or "")
        cog_rel_path = str(st.get("cog_rel_path") or "")
        candidates = {
            dataset_id,
            status_name,
            Path(status_name).stem,
            tile_folder,
            Path(cog_rel_path).stem,
        }
        if safe_dataset_name in candidates:
            bounds_text = str(st.get("bounds_wgs84") or "")
            if bounds_text:
                try:
                    bounds = json.loads(bounds_text)
                    if isinstance(bounds, list) and len(bounds) == 4:
                        return {"bounds": [float(value) for value in bounds]}
                except (TypeError, ValueError, json.JSONDecodeError):
                    pass
            cog_path_text = str(st.get("cog_path") or "")
            if not cog_path_text and cog_rel_path:
                cog_path_text = str((Path(LOCAL_DATA_PATH) / cog_rel_path).resolve())
            if cog_path_text:
                try:
                    import rasterio
                    from rasterio.warp import transform_bounds

                    with rasterio.open(Path(cog_path_text).resolve()) as src:
                        if src.crs:
                            minx, miny, maxx, maxy = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
                            return {"bounds": [float(minx), float(miny), float(maxx), float(maxy)]}
                except Exception as exc:  # noqa: BLE001
                    print(f"Error reading COG bounds: {exc}")
    tiles_dir = _resolve_dataset_tiles_dir(safe_project_id, safe_dataset_name)
    if not tiles_dir:
        return {"bounds": None}
    xml_path = tiles_dir / "tilemapresource.xml"
    if xml_path.exists():
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            bbox = root.find(".//BoundingBox")
            if bbox is None:
                for node in root.iter():
                    if node.tag.endswith("BoundingBox"):
                        bbox = node
                        break
            if bbox is not None:
                minx = bbox.get("minx")
                miny = bbox.get("miny")
                maxx = bbox.get("maxx")
                maxy = bbox.get("maxy")
                if minx and miny and maxx and maxy:
                    return {"bounds": [float(minx), float(miny), float(maxx), float(maxy)]}
        except Exception as exc:  # noqa: BLE001
            print(f"Error reading bounds XML: {exc}")

    # Manual QGIS exports may not include tilemapresource.xml; derive from XYZ indices.
    xyz_bounds = _xyz_bounds_from_tiles_dir(tiles_dir)
    if xyz_bounds:
        return {"bounds": xyz_bounds}
    return {"bounds": None}

@router.post("/api/datasets/{project_id}/metadata")
def update_dataset_metadata(project_id: str, payload: DatasetMetaPayload, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    dataset_id = _safe_dataset_id(payload.dataset_id)
    st = _read_dataset_status(safe_project_id, dataset_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset status not found")
    st["month"] = _normalize_month(payload.month)
    if payload.dataset_type.strip():
        st["dataset_type"] = _normalize_dataset_type(payload.dataset_type, st.get("dataset_name", ""))
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}

@router.put("/api/datasets/{project_id}/{dataset_id}/metadata")
def update_dataset_owner_metadata(
    project_id: str,
    dataset_id: str,
    payload: DatasetOwnerPathMetaPayload,
    request: Request,
) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    if str(user.get("role", "")).lower() != "admin":
        _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_dataset_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(safe_project_id, safe_dataset_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset status not found")
    if payload.height_offset is not None:
        st["height_offset"] = f"{float(payload.height_offset):.3f}".rstrip("0").rstrip(".")
    st["updated_at"] = _now_iso()
    _write_dataset_status(safe_project_id, safe_dataset_id, st)
    _sync_dataset_metadata_to_processing_job(safe_project_id, safe_dataset_id, st)
    _invalidate_project_files_cache(safe_project_id)
    return {"status": "success"}
