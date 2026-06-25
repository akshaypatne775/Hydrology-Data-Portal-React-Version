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


def _validate_grid_export_request(dataset_path: Path, interval: float) -> tuple[int, int, int]:
    if not math.isfinite(interval) or interval <= 0:
        raise HTTPException(status_code=400, detail="Grid interval must be greater than zero.")
    rasterio, _ = _require_rasterio()
    with rasterio.open(str(dataset_path)) as dataset:
        if dataset.count < 1:
            raise HTTPException(status_code=400, detail="Raster has no elevation band.")
        if dataset.crs and dataset.crs.is_geographic:
            raise HTTPException(
                status_code=400,
                detail="Grid export needs a projected CRS in meters. Please use the projected DTM/DSM.",
            )
        width = abs(float(dataset.bounds.right) - float(dataset.bounds.left))
        height = abs(float(dataset.bounds.top) - float(dataset.bounds.bottom))
    x_count = int(math.floor(width / interval)) + 1
    y_count = int(math.floor(height / interval)) + 1
    point_count = max(0, x_count) * max(0, y_count)
    return x_count, y_count, point_count

def _csv_grid_generator(dataset_path: Path, interval: float):
    rasterio, _ = _require_rasterio()
    with rasterio.open(str(dataset_path)) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["X", "Y", "Z"])
        yield output.getvalue()

        for y in _grid_coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch: list[tuple[float, float]] = []
            for x in _grid_coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))
                if len(batch) >= batch_size:
                    yield _csv_grid_rows(dataset, nodata, batch)
                    batch = []
            if batch:
                yield _csv_grid_rows(dataset, nodata, batch)

def _csv_grid_rows(dataset, nodata, batch: list[tuple[float, float]]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
        z = _grid_sample_value(sample, nodata)
        if z is not None:
            writer.writerow([coord[0], coord[1], z])
    return output.getvalue()

def _dxf_grid_generator(dataset_path: Path, interval: float):
    rasterio, _ = _require_rasterio()
    yield "0\nSECTION\n2\nHEADER\n9\n$INSUNITS\n70\n6\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n"
    with rasterio.open(str(dataset_path)) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000
        for y in _grid_coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch: list[tuple[float, float]] = []
            for x in _grid_coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))
                if len(batch) >= batch_size:
                    yield _dxf_grid_rows(dataset, nodata, batch)
                    batch = []
            if batch:
                yield _dxf_grid_rows(dataset, nodata, batch)
    yield "0\nENDSEC\n0\nEOF\n"

def _dxf_grid_rows(dataset, nodata, batch: list[tuple[float, float]]) -> str:
    parts: list[str] = []
    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
        z = _grid_sample_value(sample, nodata)
        if z is not None:
            parts.append(f"0\nPOINT\n8\nDROID_GRID\n10\n{coord[0]}\n20\n{coord[1]}\n30\n{z}\n")
    return "".join(parts)

def _grid_export_is_current(output_path: Path, dataset_path: Path, interval: float, export_format: str) -> bool:
    if not output_path.is_file() or output_path.stat().st_size <= 0:
        return False
    metadata_path = output_path.with_suffix(f"{output_path.suffix}.json")
    if not metadata_path.is_file():
        return output_path.stat().st_mtime_ns >= dataset_path.stat().st_mtime_ns
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return False
    return (
        str(metadata.get("source_path") or "") == str(dataset_path.resolve())
        and str(metadata.get("source_mtime_ns") or "") == str(dataset_path.stat().st_mtime_ns)
        and str(metadata.get("format") or "").lower() == export_format
        and math.isclose(float(metadata.get("interval") or 0), float(interval), rel_tol=0.0, abs_tol=1e-9)
    )
