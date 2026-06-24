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


@router.get("/api/ortho-cog/bounds")
async def ortho_cog_bounds(request: Request, url: str) -> dict[str, list[float]]:
    _require_user(request)
    cog_path = _secure_local_cog_path(url)
    bounds = await run_in_threadpool(_read_cog_bounds_wgs84, cog_path)
    return {"bounds": bounds}

@router.get("/api/dji-terra/tiles/WebMercatorQuad/{z}/{x}/{y}@{scale}x")
async def dji_terra_dem_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    scale: int,
    url: str,
    rescale: str = "",
):
    _require_user(request)
    del scale
    parsed_rescale = _parse_rescale_pair(rescale)
    if parsed_rescale is None:
        raise HTTPException(status_code=422, detail="rescale=min,max is required for DJI Terra DEM tiles.")
    cog_path = _secure_local_cog_path(url)
    try:
        tile_bytes = await run_in_threadpool(_render_dji_terra_tile, cog_path, z, x, y, parsed_rescale)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        expected_empty_tile = any(
            text in message
            for text in (
                "outside bounds",
                "outside image bounds",
                "does not overlap",
                "empty",
                "no data",
                "nodata",
            )
        )
        headers = {"Cache-Control": "public, max-age=3600"}
        if not expected_empty_tile:
            headers["X-Tile-Error"] = str(exc)[:200]
        return Response(content=TRANSPARENT_PNG_TILE, media_type="image/png", headers=headers)

    return Response(
        content=tile_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )

@router.get("/api/ortho-cog/tiles/WebMercatorQuad/{z}/{x}/{y}@{scale}x")
async def ortho_cog_tile(
    request: Request,
    z: int,
    x: int,
    y: int,
    scale: int,
    url: str,
):
    _require_user(request)
    del scale
    cog_path = _secure_local_cog_path(url)
    try:
        tile_bytes = await run_in_threadpool(_render_ortho_cog_tile, cog_path, z, x, y)
    except Exception as exc:  # noqa: BLE001
        message = str(exc).lower()
        expected_empty_tile = any(
            text in message
            for text in (
                "outside bounds",
                "outside image bounds",
                "does not overlap",
                "empty",
                "no data",
                "nodata",
            )
        )
        headers = {"Cache-Control": "public, max-age=3600", "X-Ortho-Renderer": ORTHO_RENDERER_VERSION}
        if not expected_empty_tile:
            headers["X-Tile-Error"] = str(exc)[:200]
        return Response(content=TRANSPARENT_PNG_TILE, media_type="image/png", headers=headers)

    return Response(
        content=tile_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600", "X-Ortho-Renderer": ORTHO_RENDERER_VERSION},
    )
