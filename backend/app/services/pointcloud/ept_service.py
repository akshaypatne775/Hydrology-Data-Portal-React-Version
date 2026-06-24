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


def _ept_error_needs_las_bbox_repair(message: str) -> bool:
    lowered = (message or "").lower()
    return any(
        needle in lowered
        for needle in (
            "outside bounding box",
            "valid bounding box",
            "repair the bounding box",
            "chunker_countsort_laszip",
        )
    )

def _repair_las_bounding_box(input_las: Path, repaired_las: Path) -> None:
    """
    Rewrite LAS/LAZ with header min/max recalculated from actual point coordinates.
    This fixes converter failures caused by stale LAS bounding boxes.
    """
    input_size = input_las.stat().st_size
    repaired_las.parent.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(repaired_las.parent)
    required_space = input_size + DISK_HEADROOM_BYTES
    if usage.free < required_space:
        raise RuntimeError(
            "Not enough free disk space to repair this point cloud before conversion. "
            f"Need at least {required_space} bytes free including safety headroom."
        )

    mins = np.array([np.inf, np.inf, np.inf], dtype=np.float64)
    maxs = np.array([-np.inf, -np.inf, -np.inf], dtype=np.float64)
    point_count = 0
    with laspy.open(str(input_las)) as reader:
        header = reader.header.copy()
        for points in reader.chunk_iterator(2_000_000):
            if len(points) == 0:
                continue
            xyz_min = np.array(
                [
                    float(np.nanmin(points.x)),
                    float(np.nanmin(points.y)),
                    float(np.nanmin(points.z)),
                ],
                dtype=np.float64,
            )
            xyz_max = np.array(
                [
                    float(np.nanmax(points.x)),
                    float(np.nanmax(points.y)),
                    float(np.nanmax(points.z)),
                ],
                dtype=np.float64,
            )
            mins = np.minimum(mins, xyz_min)
            maxs = np.maximum(maxs, xyz_max)
            point_count += len(points)

    if point_count <= 0 or not np.all(np.isfinite(mins)) or not np.all(np.isfinite(maxs)):
        raise RuntimeError("LAS bounding box repair failed because no valid points were found.")

    header.mins = mins
    header.maxs = maxs
    if repaired_las.exists():
        repaired_las.unlink(missing_ok=True)
    with laspy.open(str(input_las)) as reader, laspy.open(str(repaired_las), mode="w", header=header) as writer:
        for points in reader.chunk_iterator(1_000_000):
            writer.write_points(points)

def _looks_like_lon_lat_bounds(mins: np.ndarray, maxs: np.ndarray) -> bool:
    return (
        -180 <= float(mins[0]) <= 180
        and -180 <= float(maxs[0]) <= 180
        and -90 <= float(mins[1]) <= 90
        and -90 <= float(maxs[1]) <= 90
        and float(maxs[0] - mins[0]) <= 10
        and float(maxs[1] - mins[1]) <= 10
    )

def _utm_epsg_for_lon_lat(lon: float, lat: float) -> int:
    zone = int(math.floor((lon + 180.0) / 6.0) + 1)
    zone = max(1, min(60, zone))
    return (32600 if lat >= 0 else 32700) + zone

def _prepare_las_for_ept(input_las: Path, prepared_dir: Path, dataset_name: str) -> tuple[Path | None, str]:
    """
    Potree's bundled EPT loader is most reliable with projected meter
    coordinates. The working QGIS reference EPT uses UTM, while many drone
    LAS files arrive as EPSG:4326 lon/lat degrees. Reproject geographic LAS
    inputs to local UTM before Untwine builds the EPT hierarchy.
    """
    if not POINTCLOUD_EPT_PROJECT_GEOGRAPHIC or input_las.suffix.lower() not in {".las", ".laz"}:
        return None, ""

    try:
        from pyproj import CRS, Transformer
    except Exception as exc:
        raise RuntimeError(
            "Point cloud CRS normalization needs pyproj. Install backend dependency pyproj or set "
            "POINTCLOUD_EPT_PROJECT_GEOGRAPHIC=false to disable geographic-to-UTM preprocessing."
        ) from exc

    try:
        with laspy.open(str(input_las)) as reader:
            header = reader.header.copy()
            source_crs = CRS.from_user_input(POINTCLOUD_SRS_IN) if POINTCLOUD_SRS_IN else header.parse_crs()
            header_mins = np.array(header.mins, dtype=np.float64)
            header_maxs = np.array(header.maxs, dtype=np.float64)
            if source_crs is None and _looks_like_lon_lat_bounds(header_mins, header_maxs):
                source_crs = CRS.from_epsg(4326)
            if source_crs is None or not source_crs.is_geographic:
                return None, ""

            lon_center = float((header_mins[0] + header_maxs[0]) / 2.0)
            lat_center = float((header_mins[1] + header_maxs[1]) / 2.0)
            target_epsg = int(POINTCLOUD_EPT_TARGET_EPSG) if POINTCLOUD_EPT_TARGET_EPSG else _utm_epsg_for_lon_lat(lon_center, lat_center)
            target_crs = CRS.from_epsg(target_epsg)
            transformer = Transformer.from_crs(source_crs, target_crs, always_xy=True)

            mins = np.array([np.inf, np.inf, np.inf], dtype=np.float64)
            maxs = np.array([-np.inf, -np.inf, -np.inf], dtype=np.float64)
            point_count = 0
            for points in reader.chunk_iterator(1_000_000):
                if len(points) == 0:
                    continue
                px = np.asarray(points.x, dtype=np.float64).copy()
                py = np.asarray(points.y, dtype=np.float64).copy()
                pz = np.asarray(points.z, dtype=np.float64).copy()
                tx, ty = transformer.transform(px, py)
                tz = pz
                xyz_min = np.array([float(np.nanmin(tx)), float(np.nanmin(ty)), float(np.nanmin(tz))], dtype=np.float64)
                xyz_max = np.array([float(np.nanmax(tx)), float(np.nanmax(ty)), float(np.nanmax(tz))], dtype=np.float64)
                mins = np.minimum(mins, xyz_min)
                maxs = np.maximum(maxs, xyz_max)
                point_count += len(points)

        if point_count <= 0 or not np.all(np.isfinite(mins)) or not np.all(np.isfinite(maxs)):
            raise RuntimeError("Point cloud UTM preparation failed because no valid points were found.")

        prepared_dir.mkdir(parents=True, exist_ok=True)
        prepared_las = prepared_dir / f"{_ept_dataset_name(dataset_name)}.utm-epsg-{target_epsg}.las"
        if prepared_las.exists():
            prepared_las.unlink(missing_ok=True)

        with laspy.open(str(input_las)) as reader:
            header = reader.header.copy()
            header.scales = np.array([0.001, 0.001, 0.001], dtype=np.float64)
            header.offsets = np.floor(mins).astype(np.float64)
            header.mins = mins
            header.maxs = maxs
            header.add_crs(target_crs)
            with laspy.open(str(prepared_las), mode="w", header=header) as writer:
                for points in reader.chunk_iterator(1_000_000):
                    if len(points) == 0:
                        continue
                    px = np.asarray(points.x, dtype=np.float64).copy()
                    py = np.asarray(points.y, dtype=np.float64).copy()
                    pz = np.asarray(points.z, dtype=np.float64).copy()
                    tx, ty = transformer.transform(px, py)
                    tz = pz
                    out_points = points.copy()
                    out_points.array["X"] = np.rint((tx - header.offsets[0]) / header.scales[0]).astype(np.int32)
                    out_points.array["Y"] = np.rint((ty - header.offsets[1]) / header.scales[1]).astype(np.int32)
                    out_points.array["Z"] = np.rint((tz - header.offsets[2]) / header.scales[2]).astype(np.int32)
                    writer.write_points(out_points)

        note = f"Input geographic CRS was reprojected to EPSG:{target_epsg} before EPT conversion."
        return prepared_las, note
    except laspy.errors.LaspyException as exc:
        raise RuntimeError(f"Point cloud CRS preparation failed: {exc}") from exc

def _run_ept_converter_once(input_las: str, output_path: Path) -> str:
    """
    Convert LAS/LAZ to Entwine Point Tile (EPT) with Untwine only.
    The generated ept.json and ept-hierarchy are written directly inside
    output_path, matching the React viewer URL for this dataset.
    """
    def reset_output_dir() -> None:
        if output_path.is_file():
            output_path.unlink(missing_ok=True)
        if output_path.is_dir():
            shutil.rmtree(output_path, ignore_errors=True)
        output_path.mkdir(parents=True, exist_ok=True)

    input_path = Path(input_las)
    if not input_path.is_file():
        raise RuntimeError(f"EPT conversion failed: input LAS/LAZ file was not found: {input_path}")

    untwine = _resolve_converter_executable(UNTWINE_EXE)
    if not untwine:
        raise RuntimeError(
            "EPT conversion failed: untwine.exe was not found. "
            f"Expected UNTWINE_EXE at: {UNTWINE_EXE}. "
            "Install QGIS/Untwine or set the UNTWINE_EXE environment variable before starting the backend. "
            "Entwine fallback is intentionally disabled."
        )

    reset_output_dir()
    command = [untwine, "-i", str(input_path), "-o", str(output_path)]
    print(f"Running Untwine EPT conversion: {command}")
    env = os.environ.copy()
    untwine_path = Path(untwine)
    path_entries = [str(untwine_path.parent)]
    qgis_root = None
    for parent in untwine_path.parents:
        if parent.name.lower().startswith("qgis "):
            qgis_root = parent
            break
    if qgis_root:
        for candidate in (
            qgis_root / "bin",
            qgis_root / "apps" / "qgis-ltr",
            qgis_root / "apps" / "Qt5" / "bin",
        ):
            if candidate.is_dir():
                path_entries.append(str(candidate))
    env["PATH"] = os.pathsep.join(dict.fromkeys(path_entries)) + os.pathsep + env.get("PATH", "")
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=None, env=env)
    except FileNotFoundError as exc:
        reset_output_dir()
        raise RuntimeError(f"EPT conversion failed: untwine.exe was not found at {untwine}") from exc
    except Exception as exc:  # noqa: BLE001
        reset_output_dir()
        raise RuntimeError(f"EPT conversion failed while running Untwine: {exc}") from exc

    if result.returncode != 0:
        reset_output_dir()
        message = result.stderr.strip() or result.stdout.strip() or f"untwine exited with code {result.returncode}"
        print(f"Untwine EPT conversion failed for {input_path}: {message}")
        raise RuntimeError(f"Untwine EPT conversion failed:\n{message}")

    if not (output_path / "ept.json").is_file():
        reset_output_dir()
        message = result.stderr.strip() or result.stdout.strip() or "Untwine completed but ept.json was not created."
        raise RuntimeError(f"Untwine EPT conversion failed:\n{message}")

    return "Untwine"

def process_pointcloud_ept_job(
    input_las: str,
    output_dir: str,
    dataset_name: str,
    project_id: str,
    job_id: str,
    file_name: str,
    source_hash: str = "",
) -> None:
    output_path = Path(output_dir)
    err_path = output_path / ".conversion_error.txt"
    try:
        _upsert_processing_job(
            project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud",
                "file_name": file_name,
                "status": "Processing",
                "stage": "Starting COPC conversion",
                "progress_percent": "15",
                "eta_seconds": "",
                "updated_at": _now_iso(),
            },
        )

        def update_progress(payload: dict[str, object]) -> None:
            progress_percent = str(payload.get("progress_percent", ""))
            stage = str(payload.get("stage") or "Processing point cloud")
            eta_seconds = str(payload.get("eta_seconds") or "")
            _upsert_processing_job(
                project_id,
                {
                    "job_id": job_id,
                    "kind": "pointcloud",
                    "file_name": file_name,
                    "status": "Processing",
                    "stage": stage,
                    "progress_percent": progress_percent,
                    "eta_seconds": eta_seconds,
                    "updated_at": _now_iso(),
                },
            )
            _invalidate_project_files_cache(project_id)

        conversion = process_pointcloud(input_las, output_dir, dataset_name, update_progress)
        converter_label = conversion.get("converter", "Point Cloud")
        viewer_type = conversion.get("asset_type", "copc")
        viewer_dataset_name = conversion.get("viewer_dataset_name", dataset_name)
        viewer_output_path = Path(conversion.get("asset_path", output_dir)).parent
        (output_path / ".source_name.txt").write_text(file_name, encoding="utf-8")
        (viewer_output_path / ".source_name.txt").write_text(file_name, encoding="utf-8")
        if source_hash:
            (output_path / ".source_hash.txt").write_text(source_hash, encoding="utf-8")
            (viewer_output_path / ".source_hash.txt").write_text(source_hash, encoding="utf-8")
        try:
            asset_name = conversion.get("asset_name") or ("output.copc.laz" if viewer_type == "copc" else "ept.json")
            manifest_payload = {
                "project_id": project_id,
                "dataset_id": job_id,
                "display_name": file_name,
                "dataset_name": file_name,
                "dataset_type": "pointcloud",
                "source_name": file_name,
                "viewer_type": viewer_type,
                "asset_name": asset_name,
                "converter": converter_label,
                "viewer_dataset_name": viewer_dataset_name,
            }
            _write_dataset_manifest(viewer_output_path, manifest_payload)
            _write_dataset_manifest(
                output_path,
                {
                    **manifest_payload,
                    "viewer_type": "copc" if (output_path / "output.copc.laz").is_file() else viewer_type,
                },
            )
        except Exception:
            pass
        raw_rel_path = (
            str(Path(input_las).relative_to(Path(LOCAL_DATA_PATH)).as_posix())
            if Path(input_las).is_file()
            else ""
        )
        copc_rel_path = (
            str((viewer_output_path / "output.copc.laz").relative_to(Path(LOCAL_DATA_PATH)).as_posix())
            if viewer_type == "copc" and (viewer_output_path / "output.copc.laz").is_file()
            else ""
        )
        source_size_bytes = Path(input_las).stat().st_size if Path(input_las).is_file() else 0
        processed_path = (
            viewer_output_path / "output.copc.laz"
            if viewer_type == "copc" and (viewer_output_path / "output.copc.laz").is_file()
            else viewer_output_path
        )
        processed_size_bytes = (
            processed_path.stat().st_size
            if processed_path.is_file()
            else _calculate_folder_size(processed_path)
        )
        viewer_url = _pointcloud_viewer_url("", project_id, viewer_dataset_name, file_name, viewer_type)
        _write_dataset_status(
            project_id,
            job_id,
            {
                "status": "WEB-READY",
                "updated_at": _now_iso(),
                "dataset_id": job_id,
                "dataset_name": file_name,
                "display_name": file_name,
                "dataset_type": "pointcloud",
                "layer_type": "pointcloud",
                "viewer_type": viewer_type,
                "viewer_url": viewer_url,
                "layer_url": viewer_url,
                "result_url": viewer_url,
                "raw_rel_path": raw_rel_path,
                "copc_rel_path": copc_rel_path,
                "size_bytes": str(source_size_bytes or processed_size_bytes),
                "processed_size_bytes": str(processed_size_bytes),
                "processed_size": _format_size_bytes(processed_size_bytes),
                "converter": converter_label,
            },
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud",
                "file_name": file_name,
                "status": "Completed",
                "updated_at": _now_iso(),
                "result_url": viewer_url,
                "converter": converter_label,
                "viewer_type": viewer_type,
                "content_hash": source_hash,
                "raw_rel_path": raw_rel_path,
                "copc_rel_path": copc_rel_path,
                "size_bytes": str(source_size_bytes or processed_size_bytes),
                "processed_size": _format_size_bytes(processed_size_bytes),
                "processed_size_bytes": str(processed_size_bytes),
            },
        )
    except Exception as exc:
        msg = str(exc)
        output_path.mkdir(parents=True, exist_ok=True)
        try:
            err_path.write_text(msg, encoding="utf-8")
        except OSError:
            pass
        _write_portal_error_log(
            "pointcloud_conversion",
            msg,
            project_id=project_id,
            dataset_id=job_id,
            file_name=file_name,
            output_dir=str(output_path),
        )
        _upsert_processing_job(
            project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud",
                "file_name": file_name,
                "status": "Failed",
                "error": msg[:8000],
                "updated_at": _now_iso(),
            },
        )
    finally:
        _invalidate_project_files_cache(project_id)
