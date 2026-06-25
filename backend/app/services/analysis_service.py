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


def _sample_raster(dataset_path: Path, lat: float, lng: float) -> float:
    rasterio, rio_transform = _require_rasterio()
    with rasterio.open(str(dataset_path)) as src:
        xs, ys = rio_transform("EPSG:4326", src.crs, [lng], [lat]) if src.crs else ([lng], [lat])
        row, col = src.index(xs[0], ys[0])
        if row < 0 or col < 0 or row >= src.height or col >= src.width:
            raise HTTPException(status_code=400, detail="Point is outside raster bounds")
        value = next(src.sample([(xs[0], ys[0])], masked=True))[0]
        if getattr(value, "mask", False):
            raise HTTPException(status_code=404, detail="No elevation value at this point")
        if value is None or not math.isfinite(float(value)):
            raise HTTPException(status_code=404, detail="No elevation value at this point")
        return float(value)

def _interpolate_profile_points(points: list[list[float]], samples: int) -> list[dict[str, float]]:
    clean = _normalize_crop_points(points) if len(points) >= 3 else []
    if not clean:
        clean = []
        for pair in points:
            if len(pair) >= 2:
                clean.append([float(pair[0]), float(pair[1])])
    if len(clean) < 2:
        raise HTTPException(status_code=400, detail="At least 2 profile points required")
    segment_lengths: list[float] = []
    total = 0.0
    for idx in range(1, len(clean)):
        a = LLatLng(clean[idx - 1][0], clean[idx - 1][1])
        b = LLatLng(clean[idx][0], clean[idx][1])
        dist = a.distance_to(b)
        segment_lengths.append(dist)
        total += dist
    count = max(2, min(int(samples or 120), 500))
    targets = [total * i / (count - 1) for i in range(count)]
    out: list[dict[str, float]] = []
    seg_start_dist = 0.0
    seg_idx = 0
    for target in targets:
        while seg_idx < len(segment_lengths) - 1 and target > seg_start_dist + segment_lengths[seg_idx]:
            seg_start_dist += segment_lengths[seg_idx]
            seg_idx += 1
        seg_len = segment_lengths[seg_idx] or 1.0
        t = (target - seg_start_dist) / seg_len
        lat_a, lng_a = clean[seg_idx]
        lat_b, lng_b = clean[seg_idx + 1]
        out.append({
            "lat": lat_a + (lat_b - lat_a) * t,
            "lng": lng_a + (lng_b - lng_a) * t,
            "distance_m": target,
        })
    return out

def _profile_summary(values: list[dict[str, object]], corridor_width_m: float) -> dict[str, float | None]:
    valid = [
        {
            "distance_m": float(row["distance_m"]),
            "elevation": float(row["elevation"]),
        }
        for row in values
        if row.get("elevation") is not None
    ]
    if not valid:
        return {
            "length_m": None,
            "min_elevation": None,
            "max_elevation": None,
            "avg_elevation": None,
            "start_elevation": None,
            "end_elevation": None,
            "elevation_change": None,
            "elevation_gain": None,
            "elevation_loss": None,
            "volume_above_min_m3": None,
            "corridor_width_m": max(0.1, min(float(corridor_width_m or 1.0), 1000.0)),
        }

    elevations = [row["elevation"] for row in valid]
    min_elev = min(elevations)
    gain = 0.0
    loss = 0.0
    volume_above_min = 0.0
    width = max(0.1, min(float(corridor_width_m or 1.0), 1000.0))
    for prev, curr in zip(valid, valid[1:]):
        diff = curr["elevation"] - prev["elevation"]
        if diff > 0:
            gain += diff
        else:
            loss += abs(diff)
        segment_len = max(0.0, curr["distance_m"] - prev["distance_m"])
        avg_height_above_min = ((prev["elevation"] - min_elev) + (curr["elevation"] - min_elev)) / 2
        volume_above_min += segment_len * width * avg_height_above_min

    return {
        "length_m": max(row["distance_m"] for row in valid),
        "min_elevation": min_elev,
        "max_elevation": max(elevations),
        "avg_elevation": sum(elevations) / len(elevations),
        "start_elevation": valid[0]["elevation"],
        "end_elevation": valid[-1]["elevation"],
        "elevation_change": valid[-1]["elevation"] - valid[0]["elevation"],
        "elevation_gain": gain,
        "elevation_loss": loss,
        "volume_above_min_m3": volume_above_min,
        "corridor_width_m": width,
    }

def _circle_points(center: list[float], radius_m: float, segments: int = 96) -> list[list[float]]:
    if len(center) < 2:
        raise HTTPException(status_code=400, detail="Circle center is required")
    lat = float(center[0])
    lng = float(center[1])
    radius = max(0.1, float(radius_m))
    lat_rad = math.radians(lat)
    meters_per_deg_lat = 111320.0
    meters_per_deg_lng = max(1.0, 111320.0 * math.cos(lat_rad))
    points: list[list[float]] = []
    for idx in range(max(16, segments)):
        angle = (2 * math.pi * idx) / max(16, segments)
        points.append([
            lat + (math.sin(angle) * radius) / meters_per_deg_lat,
            lng + (math.cos(angle) * radius) / meters_per_deg_lng,
        ])
    return points

def _pixel_area_m2(src) -> float:
    if src.crs and getattr(src.crs, "is_geographic", False):
        center_lat = (src.bounds.top + src.bounds.bottom) / 2
        meters_per_deg_lng = 111320.0 * math.cos(math.radians(center_lat))
        return abs(src.transform.a * meters_per_deg_lng * src.transform.e * 111320.0)
    return abs(src.transform.a * src.transform.e)

def _volume_for_raster(path: Path, points: list[list[float]], base_elevation: float | None) -> dict[str, object]:
    rasterio, rio_transform = _require_rasterio()
    from rasterio.features import geometry_mask  # type: ignore
    import numpy as np  # type: ignore

    with rasterio.open(str(path)) as src:
        arr = src.read(1, masked=True).astype("float64")
        valid = ~np.ma.getmaskarray(arr)
        scope = "overall"
        if points:
            clean = _normalize_crop_points(points)
            lngs = [p[1] for p in clean]
            lats = [p[0] for p in clean]
            xs, ys = rio_transform("EPSG:4326", src.crs, lngs, lats) if src.crs else (lngs, lats)
            geom = {"type": "Polygon", "coordinates": [[list(pair) for pair in zip(xs, ys)]]}
            inside = geometry_mask([geom], out_shape=(src.height, src.width), transform=src.transform, invert=True)
            valid = valid & inside
            scope = "selection"
        if not np.any(valid):
            raise HTTPException(status_code=404, detail="No valid DTM cells found for volume")

        values = np.asarray(arr.filled(np.nan))[valid]
        values = values[np.isfinite(values)]
        if values.size == 0:
            raise HTTPException(status_code=404, detail="No valid DTM elevation values found for volume")

        base = float(base_elevation) if base_elevation is not None else float(np.min(values))
        pixel_area = _pixel_area_m2(src)
        heights = values - base
        fill = float(np.sum(np.where(heights > 0, heights, 0)) * pixel_area)
        cut = float(np.sum(np.where(heights < 0, -heights, 0)) * pixel_area)
        net = fill - cut
        area_m2 = float(values.size * pixel_area)
        min_elev = float(np.min(values))
        max_elev = float(np.max(values))
        avg_elev = float(np.mean(values))
        bins = []
        if max_elev > min_elev:
            hist, edges = np.histogram(values, bins=min(12, max(3, int(math.sqrt(values.size) // 2))))
            for idx, count in enumerate(hist):
                low = float(edges[idx])
                high = float(edges[idx + 1])
                mid_height = max(((low + high) / 2) - base, 0)
                bins.append({
                    "label": f"{low:.2f}-{high:.2f} m",
                    "volume": float(count * pixel_area * mid_height),
                })
    return {
        "scope": scope,
        "base_elevation": base,
        "min_elevation": min_elev,
        "max_elevation": max_elev,
        "avg_elevation": avg_elev,
        "area_m2": area_m2,
        "fill_volume_m3": fill,
        "cut_volume_m3": cut,
        "net_volume_m3": net,
        "cell_count": int(values.size),
        "bins": bins,
        "unit": "m3",
    }

def _dtm_volume_between(project_id: str, prev_id: str, next_id: str) -> dict[str, object]:
    rasterio, _ = _require_rasterio()
    import numpy as np  # type: ignore
    prev_path = _dataset_source_path(project_id, prev_id)
    next_path = _dataset_source_path(project_id, next_id)
    cache_payload = {
        "kind": "dtm_volume",
        "prev": _file_fingerprint(prev_path),
        "next": _file_fingerprint(next_path),
    }
    cache = _cache_path(project_id, "volume", cache_payload)
    cached = _read_cache(cache)
    if cached:
        return cached
    with rasterio.open(str(prev_path)) as a, rasterio.open(str(next_path)) as b:
        if a.width != b.width or a.height != b.height or a.transform != b.transform:
            raise HTTPException(status_code=400, detail="DTM rasters must have matching grid for volume fallback")
        arr_a = a.read(1, masked=True).astype("float64")
        arr_b = b.read(1, masked=True).astype("float64")
        diff = arr_b - arr_a
        valid = ~diff.mask if hasattr(diff, "mask") else np.isfinite(diff)
        pixel_area = abs(a.transform.a * a.transform.e)
        cut = float(np.sum(np.where(diff < 0, -diff, 0)[valid]) * pixel_area)
        fill = float(np.sum(np.where(diff > 0, diff, 0)[valid]) * pixel_area)
        net = fill - cut
        area = float(np.sum(valid) * pixel_area)
    result: dict[str, object] = {
        "month": "",
        "label": f"{prev_id} to {next_id}",
        "volume": net,
        "cut": cut,
        "fill": fill,
        "net": net,
        "area": area,
        "source": "dtm",
    }
    _write_cache(cache, result)
    return result
