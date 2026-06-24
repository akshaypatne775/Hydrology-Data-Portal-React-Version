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


def _browse_server_folder(initial_path: str = "", kind: str = "", mode: str = "folder") -> str:
    if os.name != "nt":
        raise HTTPException(
            status_code=501,
            detail="Server folder picker works only when the backend runs on Windows.",
        )
    picker_mode = str(mode or "folder").strip().lower()
    if picker_mode not in {"folder", "file"}:
        picker_mode = "folder"
    initial = str(initial_path or "").strip().strip('"')
    if initial:
        initial_path_obj = Path(initial).expanduser()
        if initial_path_obj.is_file():
            initial = str(initial_path_obj.parent.resolve())
        elif initial_path_obj.is_dir():
            initial = str(initial_path_obj.resolve())
        elif initial_path_obj.parent.is_dir():
            initial = str(initial_path_obj.parent.resolve())
        else:
            initial = ""
    out_file = Path(tempfile.gettempdir()) / f"droid-folder-pick-{secrets.token_hex(8)}.txt"
    out_file_escaped = str(out_file).replace("'", "''")
    initial_line = ""
    if initial:
        initial_escaped = initial.replace("'", "''")
        initial_line = (
            f"$d.SelectedPath = '{initial_escaped}'"
            if picker_mode == "folder"
            else f"$d.InitialDirectory = '{initial_escaped}'"
        )
    file_dialog_block = ""
    if picker_mode == "file":
        filter_text = _picker_filter_for_kind(kind).replace("'", "''")
        file_dialog_block = f"""
$d = New-Object System.Windows.Forms.OpenFileDialog
$d.CheckFileExists = $true
$d.Multiselect = $false
$d.Filter = '{filter_text}'
{initial_line}
$r = $d.ShowDialog($owner)
$owner.Dispose()
if ($r -eq [System.Windows.Forms.DialogResult]::OK -and $d.FileName) {{
    Set-Content -LiteralPath '{out_file_escaped}' -Value ([System.IO.Path]::GetDirectoryName($d.FileName)) -Encoding UTF8 -NoNewline
}}
"""
    ps_script = f"""
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true
$owner.ShowInTaskbar = $true
$owner.Text = 'Droid Bulk Import'
$owner.StartPosition = 'CenterScreen'
$owner.WindowState = 'Minimized'
$owner.Show()
$owner.Activate() | Out-Null
"""
    if picker_mode == "file":
        ps_script += file_dialog_block
    else:
        ps_script += f"""
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = 'Select bulk import source folder on this server'
$d.ShowNewFolderButton = $true
{initial_line}
$r = $d.ShowDialog($owner)
$owner.Dispose()
if ($r -eq [System.Windows.Forms.DialogResult]::OK -and $d.SelectedPath) {{
    Set-Content -LiteralPath '{out_file_escaped}' -Value $d.SelectedPath -Encoding UTF8 -NoNewline
}}
"""
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-Command", ps_script],
            timeout=600,
        )
    except subprocess.TimeoutExpired as exc:
        out_file.unlink(missing_ok=True)
        raise HTTPException(status_code=408, detail="Folder picker timed out") from exc
    try:
        if proc.returncode != 0 and not out_file.is_file():
            raise HTTPException(status_code=500, detail="Folder picker failed to open on this Windows machine")
        if not out_file.is_file():
            raise HTTPException(status_code=400, detail="No folder selected")
        folder = out_file.read_text(encoding="utf-8").strip()
        if not folder:
            raise HTTPException(status_code=400, detail="No folder selected")
        resolved = Path(folder).expanduser().resolve()
        if not resolved.is_dir():
            raise HTTPException(status_code=400, detail="Selected folder is not accessible")
        return str(resolved)
    finally:
        out_file.unlink(missing_ok=True)

def _bulk_scan_files(source_dir: Path, kind: str) -> list[Path]:
    safe_kind = str(kind or "").strip().lower()
    if safe_kind == "las":
        exts = {".las", ".laz"}
    elif safe_kind in {"ortho", "dtm", "dsm"}:
        exts = {".tif", ".tiff"}
    else:
        return []
    files = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in exts]
    return sorted(files, key=lambda p: p.name.lower())

async def _admin_manual_bulk_import_background(
    *,
    project_id: str,
    tasks: list[AdminManualBulkImportTask],
    max_parallel: int,
) -> None:
    safe_project_id = _safe_project_id(project_id)
    semaphore = asyncio.Semaphore(max(1, min(int(max_parallel or 2), 6)))

    async def run_one_pointcloud(source_file: Path) -> None:
        async with semaphore:
            raw_dir, _ = _get_project_dirs(safe_project_id)
            safe_name = _safe_pointcloud_basename(source_file.name)
            dataset_id = _safe_tileset_id(f"{re.sub(r'[^A-Za-z0-9._-]+', '-', Path(safe_name).stem)[:40]}-{secrets.token_hex(6)}")
            raw_target = raw_dir / f"{safe_project_id}__{dataset_id}__{safe_name}"
            output_dir = _ept_dataset_dir(safe_project_id, dataset_id)
            output_dir.mkdir(parents=True, exist_ok=True)

            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "pointcloud",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Copying source file into project",
                    "progress_percent": "8",
                    "eta_seconds": "",
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)

            try:
                shutil.copy2(source_file, raw_target)
            except OSError as exc:
                _upsert_processing_job(
                    safe_project_id,
                    {
                        "job_id": dataset_id,
                        "kind": "pointcloud",
                        "file_name": safe_name,
                        "status": "Failed",
                        "stage": "Copy failed",
                        "progress_percent": "100",
                        "error": f"Copy failed: {exc}"[:8000],
                        "updated_at": _now_iso(),
                    },
                )
                _invalidate_project_files_cache(safe_project_id)
                return

            raw_rel = raw_target.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "pointcloud",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Source copied, starting COPC conversion",
                    "progress_percent": "12",
                    "eta_seconds": "",
                    "raw_rel_path": raw_rel,
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)
            await asyncio.to_thread(
                process_pointcloud_ept_job,
                str(raw_target),
                str(output_dir),
                dataset_id,
                safe_project_id,
                dataset_id,
                safe_name,
                "",
            )

    async def run_one_raster(source_file: Path, dataset_type: str) -> None:
        async with semaphore:
            normalized_type = _normalize_dataset_type(dataset_type, source_file.name)
            raw_dir, processed_dir = get_project_dataset_type_dirs(safe_project_id, normalized_type)
            safe_name = _safe_dataset_upload_basename(source_file.name)
            ext = source_file.suffix.lower()
            dataset_stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(safe_name).stem).strip("-") or "dataset"
            dataset_id = _safe_dataset_id(f"{dataset_stem[:40]}-{secrets.token_hex(6)}")
            tile_output_folder = _safe_dataset_id(f"{dataset_stem[:56]}-{secrets.token_hex(4)}")
            input_path = raw_dir / f"{tile_output_folder}{ext}"
            output_tile_dir = processed_dir / tile_output_folder
            output_tile_dir.mkdir(parents=True, exist_ok=True)

            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "dataset",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Copying source raster into project",
                    "progress_percent": "8",
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)

            try:
                shutil.copy2(source_file, input_path)
            except OSError as exc:
                _upsert_processing_job(
                    safe_project_id,
                    {
                        "job_id": dataset_id,
                        "kind": "dataset",
                        "file_name": safe_name,
                        "status": "Failed",
                        "stage": "Copy failed",
                        "progress_percent": "100",
                        "error": f"Copy failed: {exc}"[:8000],
                        "updated_at": _now_iso(),
                    },
                )
                _invalidate_project_files_cache(safe_project_id)
                return

            raw_rel = input_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
            pending_cog_path = (output_tile_dir / f"{tile_output_folder}.cog.tif").resolve()
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
                    "month": "",
                    "created_at": "",
                    "raw_rel_path": raw_rel,
                    "processed_size_bytes": str(input_path.stat().st_size),
                    "processed_size": _format_size_bytes(input_path.stat().st_size),
                    "cog_path": str(pending_cog_path),
                    "manual_epsg": "",
                    "applied_epsg": "",
                },
            )
            _upsert_processing_job(
                safe_project_id,
                {
                    "job_id": dataset_id,
                    "kind": "dataset",
                    "file_name": safe_name,
                    "status": "Processing",
                    "stage": "Source copied, starting COG conversion",
                    "progress_percent": "12",
                    "raw_rel_path": raw_rel,
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(safe_project_id)
            await process_dataset_background(
                safe_project_id,
                dataset_id,
                str(input_path),
                safe_name,
                str(output_tile_dir),
                tile_output_folder,
                "",
            )

    try:
        scheduled: list[asyncio.Task[None]] = []
        for task in tasks:
            source = Path(str(task.source_folder or "")).expanduser().resolve()
            if not source.is_dir():
                continue
            kind = str(task.kind or "").strip().lower()
            files = _bulk_scan_files(source, kind)
            if not files:
                continue
            if kind == "las":
                for file_path in files:
                    scheduled.append(asyncio.create_task(run_one_pointcloud(file_path)))
            elif kind in {"ortho", "dtm", "dsm"}:
                for file_path in files:
                    scheduled.append(asyncio.create_task(run_one_raster(file_path, kind)))
        if scheduled:
            await asyncio.gather(*scheduled)
    finally:
        _invalidate_project_files_cache(safe_project_id)

def _prepare_admin_manual_bulk_import(
    tasks: list[AdminManualBulkImportTask],
) -> tuple[list[AdminManualBulkImportTask], list[dict[str, object]], int]:
    cleaned: list[AdminManualBulkImportTask] = []
    preview: list[dict[str, object]] = []
    file_count = 0
    for item in tasks:
        kind = str(item.kind or "").strip().lower()
        if kind not in {"las", "ortho", "dtm", "dsm"}:
            continue
        folder = str(item.source_folder or "").strip().strip('"')
        if not folder:
            continue
        source = Path(folder).expanduser()
        if not source.is_dir():
            preview.append(
                {
                    "kind": kind,
                    "source_folder": folder,
                    "status": "missing",
                    "file_count": 0,
                    "message": "Folder not found on server",
                }
            )
            continue
        files = _bulk_scan_files(source.resolve(), kind)
        preview.append(
            {
                "kind": kind,
                "source_folder": str(source.resolve()),
                "status": "ready" if files else "empty",
                "file_count": len(files),
                "message": (
                    f"Found {len(files)} file(s)"
                    if files
                    else f"No matching {kind.upper()} files in folder"
                ),
            }
        )
        if not files:
            continue
        cleaned.append(AdminManualBulkImportTask(source_folder=str(source.resolve()), kind=kind))
        file_count += len(files)
    return cleaned, preview, file_count

def _queue_admin_manual_bulk_import(
    *,
    project_id: str,
    payload: AdminManualBulkImportPayload,
    request: Request,
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    user = verify_admin(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)

    cleaned, preview, file_count = _prepare_admin_manual_bulk_import(payload.tasks or [])
    if not cleaned:
        detail = "No importable files found. Check folder path and selected type."
        if preview:
            detail = "; ".join(
                f"{row['source_folder']} ({row['message']})" for row in preview if isinstance(row.get("message"), str)
            ) or detail
        raise HTTPException(status_code=400, detail=detail)

    background_tasks.add_task(
        _admin_manual_bulk_import_background,
        project_id=safe_project_id,
        tasks=cleaned,
        max_parallel=payload.max_parallel,
    )
    return {
        "status": "success",
        "message": f"Queued {file_count} file(s) from {len(cleaned)} folder task(s).",
        "project_id": safe_project_id,
        "task_count": len(cleaned),
        "file_count": file_count,
        "preview": preview,
    }
