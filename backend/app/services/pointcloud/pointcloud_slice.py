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


def _rotation_matrix_xyz(rx: float, ry: float, rz: float) -> np.ndarray:
    sx, cx = math.sin(rx), math.cos(rx)
    sy, cy = math.sin(ry), math.cos(ry)
    sz, cz = math.sin(rz), math.cos(rz)
    matrix_x = np.array([[1.0, 0.0, 0.0], [0.0, cx, -sx], [0.0, sx, cx]], dtype=float)
    matrix_y = np.array([[cy, 0.0, sy], [0.0, 1.0, 0.0], [-sy, 0.0, cy]], dtype=float)
    matrix_z = np.array([[cz, -sz, 0.0], [sz, cz, 0.0], [0.0, 0.0, 1.0]], dtype=float)
    return matrix_z @ matrix_y @ matrix_x

def _finite_vector(values: list[float], expected: int, name: str) -> np.ndarray:
    if len(values) != expected:
        raise ValueError(f"{name} must contain {expected} numbers")
    vector = np.array([float(value) for value in values], dtype=float)
    if not np.all(np.isfinite(vector)):
        raise ValueError(f"{name} contains invalid numbers")
    return vector

def _point_record_value(points, dimension_names: set[str], name: str, mask: np.ndarray, default: int = 0) -> np.ndarray:
    if name not in dimension_names:
        return np.full(int(mask.sum()), default)
    return np.asarray(getattr(points, name))[mask]

def _run_pointcloud_slice_export(project_id: str, dataset_id: str, job_id: str, payload: dict[str, object]) -> None:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_ept_folder_name(dataset_id)
    export_dir = _pointcloud_slice_exports_root(safe_project_id, safe_dataset_id) / job_id
    out_csv = export_dir / "clipped_points.csv"
    out_las = export_dir / "clipped_points.las"
    export_dir.mkdir(parents=True, exist_ok=True)
    started_at = _now_iso()
    try:
        box_payload = payload.get("box") if isinstance(payload, dict) else {}
        if not isinstance(box_payload, dict):
            raise ValueError("Invalid section box payload")
        center = _finite_vector(list(box_payload.get("center") or []), 3, "center")
        rotation_values = list(box_payload.get("rotation") or [0.0, 0.0, 0.0])
        if len(rotation_values) < 3:
            rotation_values = rotation_values + [0.0] * (3 - len(rotation_values))
        rotation = _finite_vector(rotation_values[:3], 3, "rotation")
        dimensions = np.maximum(_finite_vector(list(box_payload.get("dimensions") or []), 3, "dimensions"), 0.001)
        half = dimensions / 2.0
        rotation_matrix = _rotation_matrix_xyz(float(rotation[0]), float(rotation[1]), float(rotation[2]))
        source = _resolve_pointcloud_slice_source(safe_project_id, safe_dataset_id)

        total_written = 0
        clipped_min: np.ndarray | None = None
        clipped_max: np.ndarray | None = None
        hull_sample_chunks: list[np.ndarray] = []
        hull_sample_limit = 50000
        hull_sample_count = 0
        section_box_volume_m3 = float(np.prod(dimensions))
        with laspy.open(str(source)) as reader, out_csv.open("w", newline="", encoding="utf-8") as handle, laspy.open(str(out_las), mode="w", header=reader.header) as las_writer:
            writer = csv.writer(handle)
            writer.writerow(["x", "y", "z", "elevation", "intensity", "classification", "r", "g", "b"])
            for points in reader.chunk_iterator(400_000):
                coords = np.column_stack((np.asarray(points.x), np.asarray(points.y), np.asarray(points.z)))
                local = (coords - center) @ rotation_matrix
                mask = (
                    (np.abs(local[:, 0]) <= half[0])
                    & (np.abs(local[:, 1]) <= half[1])
                    & (np.abs(local[:, 2]) <= half[2])
                )
                if not np.any(mask):
                    continue
                clipped_points = points[mask]
                las_writer.write_points(clipped_points)
                dimension_names = set(points.point_format.dimension_names)
                xs = coords[:, 0][mask]
                ys = coords[:, 1][mask]
                zs = coords[:, 2][mask]
                selected_coords = np.column_stack((xs, ys, zs))
                chunk_min = selected_coords.min(axis=0)
                chunk_max = selected_coords.max(axis=0)
                clipped_min = chunk_min if clipped_min is None else np.minimum(clipped_min, chunk_min)
                clipped_max = chunk_max if clipped_max is None else np.maximum(clipped_max, chunk_max)
                if hull_sample_count < hull_sample_limit:
                    remaining = hull_sample_limit - hull_sample_count
                    if len(selected_coords) > remaining:
                        stride = max(1, int(np.ceil(len(selected_coords) / remaining)))
                        sample = selected_coords[::stride][:remaining]
                    else:
                        sample = selected_coords
                    if len(sample):
                        hull_sample_chunks.append(sample.astype(float, copy=False))
                        hull_sample_count += int(len(sample))
                intensities = _point_record_value(points, dimension_names, "intensity", mask)
                classes = _point_record_value(points, dimension_names, "classification", mask)
                reds = _point_record_value(points, dimension_names, "red", mask)
                greens = _point_record_value(points, dimension_names, "green", mask)
                blues = _point_record_value(points, dimension_names, "blue", mask)
                for row in zip(xs, ys, zs, zs, intensities, classes, reds, greens, blues):
                    writer.writerow(row)
                total_written += int(mask.sum())

        clipped_bbox_volume_m3 = 0.0
        clipped_hull_volume_m3 = 0.0
        clipped_hull_method = "not_computed"
        clipped_bounds: list[float] = []
        if clipped_min is not None and clipped_max is not None:
            clipped_span = np.maximum(clipped_max - clipped_min, 0.0)
            clipped_bbox_volume_m3 = float(np.prod(clipped_span))
            clipped_bounds = [float(value) for value in [*clipped_min.tolist(), *clipped_max.tolist()]]
        if hull_sample_chunks and hull_sample_count >= 4:
            try:
                from scipy.spatial import ConvexHull  # type: ignore

                hull_points = np.vstack(hull_sample_chunks)
                if hull_points.shape[0] >= 4:
                    clipped_hull_volume_m3 = float(ConvexHull(hull_points).volume)
                    clipped_hull_method = "sampled_convex_hull"
            except Exception:
                clipped_hull_volume_m3 = clipped_bbox_volume_m3
                clipped_hull_method = "axis_aligned_bbox_fallback"

        metadata = {
            "job_id": job_id,
            "project_id": safe_project_id,
            "dataset_id": safe_dataset_id,
            "source": str(source),
            "rows": total_written,
            "csv": str(out_csv),
            "las": str(out_las),
            "section_box_volume_m3": section_box_volume_m3,
            "clipped_bbox_volume_m3": clipped_bbox_volume_m3,
            "clipped_hull_volume_m3": clipped_hull_volume_m3,
            "clipped_hull_method": clipped_hull_method,
            "clipped_bounds": clipped_bounds,
            "started_at": started_at,
            "completed_at": _now_iso(),
            "box": box_payload,
        }
        (export_dir / "slice_export.json").write_text(json.dumps(metadata, ensure_ascii=True, indent=2), encoding="utf-8")
        download_url = f"/api/data/projects/{safe_project_id}/exports/pointcloud_slices/{quote(safe_dataset_id, safe='')}/{job_id}/clipped_points.csv"
        download_url_las = f"/api/data/projects/{safe_project_id}/exports/pointcloud_slices/{quote(safe_dataset_id, safe='')}/{job_id}/clipped_points.las"
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud_slice_export",
                "file_name": str(payload.get("name") or "Slice Export"),
                "status": "Completed",
                "stage": f"Exported {total_written} clipped points | Box volume {section_box_volume_m3:.2f} m3",
                "progress_percent": "100",
                "download_url": download_url,
                "download_url_las": download_url_las,
                "section_box_volume_m3": f"{section_box_volume_m3:.3f}",
                "clipped_bbox_volume_m3": f"{clipped_bbox_volume_m3:.3f}",
                "clipped_hull_volume_m3": f"{clipped_hull_volume_m3:.3f}",
                "clipped_hull_method": clipped_hull_method,
                "updated_at": _now_iso(),
            },
        )
    except Exception as exc:
        error_path = export_dir / "slice_export_error.txt"
        error_path.write_text(str(exc), encoding="utf-8")
        _upsert_processing_job(
            safe_project_id,
            {
                "job_id": job_id,
                "kind": "pointcloud_slice_export",
                "file_name": str(payload.get("name") or "Slice Export") if isinstance(payload, dict) else "Slice Export",
                "status": "Failed",
                "stage": str(exc)[:800],
                "progress_percent": "100",
                "updated_at": _now_iso(),
            },
        )
    finally:
        _invalidate_project_files_cache(safe_project_id)
