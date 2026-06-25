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


def _normalize_spatial_feature_geojson(raw: dict[str, object]) -> tuple[dict[str, object], str]:
    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="Invalid GeoJSON")
    geo_type = str(raw.get("type") or "")
    if geo_type == "Feature":
        geometry = raw.get("geometry")
        if not isinstance(geometry, dict):
            raise HTTPException(status_code=400, detail="GeoJSON Feature geometry is required")
        feature = dict(raw)
        props = feature.get("properties")
        feature["properties"] = props if isinstance(props, dict) else {}
        geometry_type = str(geometry.get("type") or "")
    else:
        geometry = raw
        geometry_type = geo_type
        feature = {"type": "Feature", "properties": {}, "geometry": geometry}
    if geometry_type not in {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}:
        raise HTTPException(status_code=400, detail="Unsupported geometry type")
    return feature, geometry_type

def _can_manage_spatial_feature(user: dict[str, object], feature_owner_user_id: int) -> bool:
    return str(user.get("role", "")).lower() == "admin" or int(user["id"]) == int(feature_owner_user_id)

def _spatial_row_to_dict(row: sqlite3.Row, user: dict[str, object] | None = None) -> dict[str, object]:
    try:
        geojson_data = json.loads(str(row["geojson"]))
    except (TypeError, json.JSONDecodeError):
        geojson_data = {"type": "Feature", "properties": {}, "geometry": None}
    owner_user_id = int(row["owner_user_id"])
    can_manage = _can_manage_spatial_feature(user, owner_user_id) if user else True
    return {
        "id": str(row["id"]),
        "project_id": str(row["project_id"]),
        "layer_id": str(row["layer_id"]),
        "owner_user_id": owner_user_id,
        "geometry_type": str(row["geometry_type"]),
        "geojson": geojson_data,
        "plot_id": str(row["plot_id"] or ""),
        "owner_name": str(row["owner_name"] or ""),
        "structure_type": str(row["structure_type"] or "Unassigned"),
        "fill_color": str(row["fill_color"] or "#f59e0b"),
        "stroke_color": str(row["stroke_color"] or "#f59e0b"),
        "source_type": str(row["source_type"] or ""),
        "can_edit": can_manage,
        "can_delete": can_manage,
        "created_at": str(row["created_at"] or ""),
        "updated_at": str(row["updated_at"] or ""),
    }

def _ensure_spatial_layer(
    connection: sqlite3.Connection,
    project_id: str,
    user_id: int,
    layer_id: str,
    layer_name: str,
    source_type: str,
) -> str:
    if layer_id:
        safe_layer_id = _safe_spatial_id(layer_id, "layer_id")
        row = connection.execute(
            "SELECT id FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial layer not found")
        return safe_layer_id

    clean_name = (layer_name or "Drawn Shapes").strip()[:180] or "Drawn Shapes"
    existing = connection.execute(
        """
        SELECT id FROM spatial_layers
        WHERE project_id = ? AND name = ? AND source_type = ?
        ORDER BY created_at ASC
        LIMIT 1
        """,
        (project_id, clean_name, source_type),
    ).fetchone()
    if existing:
        return str(existing["id"])

    new_layer_id = f"layer_{secrets.token_hex(8)}"
    now = _now_iso()
    connection.execute(
        """
        INSERT INTO spatial_layers (
            id, project_id, owner_user_id, name, source_type, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (new_layer_id, project_id, user_id, clean_name, source_type, now, now),
    )
    return new_layer_id

def _insert_spatial_feature(
    connection: sqlite3.Connection,
    project_id: str,
    user_id: int,
    layer_id: str,
    geojson_data: dict[str, object],
    plot_id: str,
    owner_name: str,
    structure_type: str,
    source_type: str,
    user_context: dict[str, object] | None = None,
) -> dict[str, object]:
    feature, geometry_type = _normalize_spatial_feature_geojson(geojson_data)
    clean_structure = normalize_structure_type(structure_type)
    colors = style_for_structure(clean_structure)
    now = _now_iso()
    feature_id = f"spatial_{secrets.token_hex(8)}"
    properties = feature.get("properties")
    if not isinstance(properties, dict):
        properties = {}
    properties.update(
        {
            "plotId": plot_id,
            "ownerName": owner_name,
            "structureType": clean_structure,
        },
    )
    feature["properties"] = properties
    connection.execute(
        """
        INSERT INTO spatial_features (
            id, project_id, layer_id, owner_user_id, geometry_type, geojson,
            plot_id, owner_name, structure_type, fill_color, stroke_color,
            source_type, created_at, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            feature_id,
            project_id,
            layer_id,
            user_id,
            geometry_type,
            json.dumps(feature, ensure_ascii=True),
            plot_id[:120],
            owner_name[:180],
            clean_structure,
            colors["fill_color"],
            colors["stroke_color"],
            source_type,
            now,
            now,
        ),
    )
    row = connection.execute(
        "SELECT * FROM spatial_features WHERE id = ?",
        (feature_id,),
    ).fetchone()
    return _spatial_row_to_dict(row, user_context)
