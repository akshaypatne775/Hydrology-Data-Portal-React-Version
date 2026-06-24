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


def _copc_ept_compat_dir(copc_file: Path) -> Path:
    return copc_file.parent.with_name(f"{copc_file.parent.name}__ept_viewer")

def _process_copc_ept_compat_job(
    project_id: str,
    dataset_id: str,
    copc_file: str,
    output_dir: str,
    display_name: str,
) -> None:
    if POTREE_NATIVE_COPC_ENABLED:
        return
    source = Path(copc_file)
    output = Path(output_dir)
    try:
        converter = _run_ept_converter_once(str(source), output)
        (output / ".source_name.txt").write_text(display_name, encoding="utf-8")
        (output / ".viewer_type.txt").write_text("ept", encoding="utf-8")
        (output / ".converter.txt").write_text(f"{converter} from COPC", encoding="utf-8")
        rel = output.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
        source_rel = source.relative_to(Path(LOCAL_DATA_PATH)).as_posix()
        status = _read_dataset_status(project_id, dataset_id) or {}
        status.update(
            {
                "status": "Web-Ready",
                "updated_at": _now_iso(),
                "dataset_id": dataset_id,
                "dataset_name": display_name,
                "display_name": display_name,
                "source_name": display_name,
                "dataset_type": "pointcloud",
                "layer_type": "pointcloud",
                "viewer_type": "ept",
                "tile_folder": output.name,
                "tiles_rel_path": rel,
                "source_asset_rel_path": source_rel,
            }
        )
        _write_dataset_status(project_id, dataset_id, status)
        _write_dataset_manifest(
            output,
            {
                **status,
                "asset_name": "ept.json",
                "source_asset_rel_path": source_rel,
            },
        )
        _write_dataset_manifest(
            source.parent,
            {
                "project_id": project_id,
                "dataset_id": dataset_id,
                "display_name": display_name,
                "source_name": display_name,
                "dataset_type": "pointcloud",
                "viewer_type": "copc",
                "asset_name": source.name,
                "viewer_dataset_name": output.name,
            },
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "pointcloud",
                "file_name": display_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": _ept_viewer_url("", project_id, output.name, display_name),
                "viewer_type": "ept",
                "source_asset_rel_path": source_rel,
            },
        )
    except Exception as exc:  # noqa: BLE001
        message = str(exc)
        _write_portal_error_log(
            "copc_ept_compatibility",
            message,
            project_id=project_id,
            dataset_id=dataset_id,
            file_name=display_name,
            copc_file=str(source),
        )
        status = _read_dataset_status(project_id, dataset_id) or {}
        status.update(
            {
                "status": "Failed",
                "updated_at": _now_iso(),
                "error": message[:8000],
                "stage": "COPC viewer preparation failed",
            }
        )
        _write_dataset_status(project_id, dataset_id, status)
        _upsert_processing_job(
            project_id,
            {
                "job_id": dataset_id,
                "kind": "pointcloud",
                "file_name": display_name,
                "status": "Failed",
                "updated_at": _now_iso(),
                "error": message[:8000],
            },
        )
    finally:
        queue_marker = output.parent / f".{output.name}.queued"
        try:
            queue_marker.unlink(missing_ok=True)
        except OSError:
            pass
        _invalidate_project_files_cache(project_id)

def _run_copc_converter_once(input_las: str, output_path: Path) -> str:
    """
    Convert LAS/LAZ to a single Cloud Optimized Point Cloud asset.
    COPC is the primary fast-streaming output for Potree native COPC viewing.
    """
    def reset_output_dir() -> None:
        if output_path.is_file():
            output_path.unlink(missing_ok=True)
        if output_path.is_dir():
            shutil.rmtree(output_path, ignore_errors=True)
        output_path.mkdir(parents=True, exist_ok=True)

    input_path = Path(input_las)
    if not input_path.is_file():
        raise RuntimeError(f"COPC conversion failed: input LAS/LAZ file was not found: {input_path}")

    pdal_exe = _resolve_converter_executable(PDAL_EXE)
    if not pdal_exe:
        raise RuntimeError(
            "COPC conversion failed: pdal executable was not found. "
            "Set PDAL_EXE or install PDAL before starting the backend."
        )
    if not _pdal_has_driver(pdal_exe, "writers.copc"):
        raise RuntimeError(
            "COPC conversion failed: the configured PDAL executable does not expose writers.copc. "
            f"PDAL_EXE={pdal_exe}. Install a PDAL build with COPC writer support."
        )

    reset_output_dir()
    output_file = output_path / "output.copc.laz"
    command = [pdal_exe, "translate", str(input_path), str(output_file), "--writer", "writers.copc"]
    print(f"Running PDAL COPC conversion: {command}")
    env = os.environ.copy()
    env["PATH"] = str(Path(pdal_exe).parent) + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=None, env=env)
    except FileNotFoundError as exc:
        reset_output_dir()
        raise RuntimeError(f"COPC conversion failed: pdal executable was not found at {pdal_exe}") from exc
    except Exception as exc:  # noqa: BLE001
        reset_output_dir()
        raise RuntimeError(f"COPC conversion failed while running PDAL: {exc}") from exc

    if result.returncode != 0:
        reset_output_dir()
        message = result.stderr.strip() or result.stdout.strip() or f"pdal exited with code {result.returncode}"
        print(f"PDAL COPC conversion failed for {input_path}: {message}")
        raise RuntimeError(f"PDAL COPC conversion failed:\n{message}")

    if not output_file.is_file() or output_file.stat().st_size <= 0:
        reset_output_dir()
        message = result.stderr.strip() or result.stdout.strip() or "PDAL completed but output.copc.laz was not created."
        raise RuntimeError(f"PDAL COPC conversion failed:\n{message}")

    return "PDAL COPC"

def _copc_asset_in_dir(dataset_dir: Path) -> Path | None:
    if not dataset_dir.is_dir():
        return None
    preferred = dataset_dir / "output.copc.laz"
    if preferred.is_file() and preferred.stat().st_size > 0:
        return preferred
    candidates = sorted(
        (path for path in dataset_dir.glob("*.copc.laz") if path.is_file() and path.stat().st_size > 0),
        key=lambda path: (path.stat().st_mtime, path.name.lower()),
        reverse=True,
    )
    return candidates[0] if candidates else None

def _best_copc_asset(project_id: str, dataset_name: str) -> tuple[str, Path] | None:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    project_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project
    for dataset_dir in (
        _ept_dataset_dir(safe_project, safe_dataset),
        _legacy_ept_pointcloud_dataset_dir(safe_project, safe_dataset),
        _legacy_ept_dataset_dir(safe_project, safe_dataset),
    ):
        asset = _copc_asset_in_dir(dataset_dir)
        if asset:
            return asset.relative_to(project_root).as_posix(), asset
    return None
