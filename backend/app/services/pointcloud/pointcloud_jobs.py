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

import numpy as np
from fastapi import Request

from app.core.config import *
from app.models.pointclouds import *
from app.models.datasets import *
from app.services.catalog_service import mirror_processing_job, delete_asset_artifacts, upsert_asset
from app.services.raster import convert_tif_to_cog
from app.core.database import get_db_connection

# Deferred local imports from main.py
def _update_dataset_status(*args, **kwargs):
    from app.main import update_dataset_status
    return update_dataset_status(*args, **kwargs)

def _get_project_dirs(*args, **kwargs):
    from app.main import get_project_dirs
    return get_project_dirs(*args, **kwargs)

def _calculate_folder_size(*args, **kwargs):
    from app.main import calculate_folder_size
    return calculate_folder_size(*args, **kwargs)

def _format_size_bytes(*args, **kwargs):
    from app.main import _format_size_bytes
    return _format_size_bytes(*args, **kwargs)


async def process_pointcloud_background(
    final_path: Path,
    output_dir: Path,
    project_id: str | None = None,
    job_id: str | None = None,
    file_name: str | None = None,
) -> None:
    """
    Background conversion worker for COPC point clouds (PDAL writers.copc only).
    """
    if output_dir.is_dir():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    err_path = output_dir / ".conversion_error.txt"
    if err_path.exists():
        err_path.unlink(missing_ok=True)

    try:
        conversion = await run_in_threadpool(process_pointcloud, str(final_path), str(output_dir), output_dir.name)
        converter_label = conversion.get("converter", "Point Cloud")
        viewer_type = conversion.get("asset_type", "copc")
        if project_id and job_id:
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name or final_path.name,
                    "status": "Completed",
                    "updated_at": _now_iso(),
                    "result_url": _pointcloud_viewer_url("", project_id, output_dir.name, file_name or final_path.name, viewer_type),
                    "converter": converter_label,
                    "viewer_type": viewer_type,
                },
            )
            _invalidate_project_files_cache(project_id)
    except Exception as exc:  # noqa: BLE001
        msg = str(exc)
        print("Point cloud conversion failed:", msg)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        if project_id and job_id:
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name or final_path.name,
                    "status": "Failed",
                    "error": msg[:8000],
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(project_id)

def process_pointcloud(
    input_las: str,
    output_dir: str,
    dataset_name: str,
    progress_callback: Callable[[dict[str, object]], None] | None = None,
) -> dict[str, str]:
    """Convert LAS/LAZ to COPC with PDAL writers.copc (no EPT/Untwine fallback)."""
    safe_dataset_name = _ept_dataset_name(dataset_name)
    output_path = Path(output_dir)
    if output_path.is_dir():
        shutil.rmtree(output_path, ignore_errors=True)
    output_path.mkdir(parents=True, exist_ok=True)

    prepared_input: Path | None = None
    prepared_note = ""
    repaired_input: Path | None = None
    try:
        if progress_callback:
            progress_callback({"stage": "Preparing LAS/LAZ source", "progress_percent": 12, "eta_seconds": ""})
        input_path = Path(input_las)
        prepared_input, prepared_note = _prepare_las_for_ept(
            input_path,
            output_path.parent / "_prepared_inputs",
            safe_dataset_name,
        )
        converter_input = str(prepared_input or input_path)
        try:
            if progress_callback:
                progress_callback({"stage": "Converting to COPC", "progress_percent": 55, "eta_seconds": ""})
            converter_label = _run_copc_converter_once(converter_input, output_path)
        except RuntimeError as exc:
            repair_source = Path(converter_input)
            if repair_source.suffix.lower() in {".las", ".laz"} and _ept_error_needs_las_bbox_repair(str(exc)):
                if progress_callback:
                    progress_callback({"stage": "Repairing LAS bounds", "progress_percent": 35, "eta_seconds": ""})
                repair_dir = output_path.parent / "_repaired_inputs"
                repaired_input = repair_dir / f"{safe_dataset_name}.bbox-repaired.las"
                _repair_las_bounding_box(repair_source, repaired_input)
                if output_path.is_dir():
                    shutil.rmtree(output_path, ignore_errors=True)
                output_path.mkdir(parents=True, exist_ok=True)
                if progress_callback:
                    progress_callback({"stage": "Retrying COPC conversion", "progress_percent": 62, "eta_seconds": ""})
                converter_label = _run_copc_converter_once(str(repaired_input), output_path)
                (output_path / ".repair_note.txt").write_text(
                    "LAS bounding box header was repaired automatically before COPC conversion.",
                    encoding="utf-8",
                )
            else:
                raise

        if prepared_note:
            (output_path / ".crs_note.txt").write_text(prepared_note, encoding="utf-8")
        if progress_callback:
            progress_callback({"stage": "Finalizing point cloud viewer", "progress_percent": 92, "eta_seconds": ""})
        (output_path / ".viewer_type.txt").write_text("copc", encoding="utf-8")
        (output_path / ".converter.txt").write_text(converter_label, encoding="utf-8")
        return {
            "asset_type": "copc",
            "asset_path": str(output_path / "output.copc.laz"),
            "asset_name": "output.copc.laz",
            "converter": converter_label,
            "viewer_dataset_name": output_path.name,
        }
    finally:
        if repaired_input is not None:
            try:
                repaired_input.unlink(missing_ok=True)
                if repaired_input.parent.is_dir() and not any(repaired_input.parent.iterdir()):
                    repaired_input.parent.rmdir()
            except OSError:
                pass
        if prepared_input is not None:
            try:
                prepared_input.unlink(missing_ok=True)
                if prepared_input.parent.is_dir() and not any(prepared_input.parent.iterdir()):
                    prepared_input.parent.rmdir()
            except OSError:
                pass

def process_contours_background(
    project_id: str,
    dataset_id: str,
    input_tif: str,
    output_geojson: str,
    interval: float,
    dataset_name: str,
) -> None:
    out_path = Path(output_geojson)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    command = (
        f'call "{OSGEO4W_BAT}" gdal_contour -a elev -i {interval:g} '
        f'"{input_tif}" "{output_geojson}"'
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            shell=True,
            executable=os.environ.get("COMSPEC", "cmd.exe"),
        )
        if result.returncode != 0:
            raise RuntimeError((result.stderr or result.stdout or "").strip() or "gdal_contour failed")
        rel = out_path.resolve().relative_to(Path(LOCAL_DATA_PATH).resolve()).as_posix()
        _write_dataset_status(
            project_id,
            dataset_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": dataset_name,
                "tile_folder": "",
                "dataset_type": "vector",
                "layer_type": "Vector",
                "raw_rel_path": rel,
                "vector_rel_path": rel,
            },
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "vector",
                "file_name": dataset_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": f"/data/{rel}",
            },
        )
    except Exception as exc:  # noqa: BLE001
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "vector",
                "file_name": dataset_name,
                "status": "Failed",
                "error": str(exc)[:8000],
                "updated_at": _now_iso(),
            },
        )
    finally:
        _invalidate_project_files_cache(project_id)
