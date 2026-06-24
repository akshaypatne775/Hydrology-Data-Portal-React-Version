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


@router.get("/api/projects/{project_id}/spatial-layers")
def get_spatial_layers(project_id: str, request: Request) -> dict[str, list[dict[str, object]]]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        layer_rows = connection.execute(
            """
            SELECT id, project_id, name, source_type, created_at, updated_at
            FROM spatial_layers
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (safe_project_id,),
        ).fetchall()
        feature_rows = connection.execute(
            """
            SELECT *
            FROM spatial_features
            WHERE project_id = ?
            ORDER BY created_at ASC
            """,
            (safe_project_id,),
        ).fetchall()

    features_by_layer: dict[str, list[dict[str, object]]] = {}
    for row in feature_rows:
        feature = _spatial_row_to_dict(row, user)
        features_by_layer.setdefault(str(feature["layer_id"]), []).append(feature)

    layers: list[dict[str, object]] = []
    for row in layer_rows:
        layer_id = str(row["id"])
        layers.append(
            {
                "id": layer_id,
                "project_id": str(row["project_id"]),
                "name": str(row["name"]),
                "source_type": str(row["source_type"]),
                "created_at": str(row["created_at"]),
                "updated_at": str(row["updated_at"]),
                "features": features_by_layer.get(layer_id, []),
            },
        )
    return {"layers": layers}

@router.post("/api/projects/{project_id}/spatial-features")
def create_spatial_feature(
    project_id: str,
    payload: SpatialFeaturePayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    source_type = (payload.source_type or "drawn").strip()[:40] or "drawn"
    with get_db_connection() as connection:
        layer_id = _ensure_spatial_layer(
            connection,
            safe_project_id,
            int(user["id"]),
            payload.layer_id,
            payload.layer_name,
            source_type,
        )
        feature = _insert_spatial_feature(
            connection,
            safe_project_id,
            int(user["id"]),
            layer_id,
            payload.geojson,
            (payload.plot_id or "").strip(),
            (payload.owner_name or "").strip(),
            payload.structure_type,
            source_type,
            user,
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), layer_id),
        )
        connection.commit()
    return {"feature": feature}

@router.put("/api/projects/{project_id}/spatial-features/{feature_id}")
def update_spatial_feature(
    project_id: str,
    feature_id: str,
    payload: SpatialFeaturePatchPayload,
    request: Request,
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_feature_id = _safe_spatial_id(feature_id, "feature_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT * FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial feature not found")
        if not _can_manage_spatial_feature(user, int(row["owner_user_id"])):
            raise HTTPException(status_code=403, detail="Only the creator or an admin can edit this spatial feature")

        current = _spatial_row_to_dict(row, user)
        geojson_data = payload.geojson if payload.geojson is not None else current["geojson"]
        feature, geometry_type = _normalize_spatial_feature_geojson(geojson_data)
        plot_id = (payload.plot_id if payload.plot_id is not None else str(current["plot_id"])).strip()
        owner_name = (payload.owner_name if payload.owner_name is not None else str(current["owner_name"])).strip()
        structure_type = normalize_structure_type(
            payload.structure_type if payload.structure_type is not None else str(current["structure_type"]),
        )
        colors = style_for_structure(structure_type)
        properties = feature.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        properties.update(
            {
                "plotId": plot_id,
                "ownerName": owner_name,
                "structureType": structure_type,
            },
        )
        feature["properties"] = properties
        now = _now_iso()
        connection.execute(
            """
            UPDATE spatial_features
            SET geometry_type = ?, geojson = ?, plot_id = ?, owner_name = ?,
                structure_type = ?, fill_color = ?, stroke_color = ?, updated_at = ?
            WHERE id = ? AND project_id = ?
            """,
            (
                geometry_type,
                json.dumps(feature, ensure_ascii=True),
                plot_id[:120],
                owner_name[:180],
                structure_type,
                colors["fill_color"],
                colors["stroke_color"],
                now,
                safe_feature_id,
                safe_project_id,
            ),
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (now, str(row["layer_id"])),
        )
        connection.commit()
        updated = connection.execute(
            "SELECT * FROM spatial_features WHERE id = ?",
            (safe_feature_id,),
        ).fetchone()
    return {"feature": _spatial_row_to_dict(updated, user)}

@router.delete("/api/projects/{project_id}/spatial-features/{feature_id}")
def delete_spatial_feature(project_id: str, feature_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_feature_id = _safe_spatial_id(feature_id, "feature_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT layer_id, owner_user_id FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial feature not found")
        if not _can_manage_spatial_feature(user, int(row["owner_user_id"])):
            raise HTTPException(status_code=403, detail="Only the creator or an admin can delete this spatial feature")
        connection.execute(
            "DELETE FROM spatial_features WHERE id = ? AND project_id = ?",
            (safe_feature_id, safe_project_id),
        )
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), str(row["layer_id"])),
        )
        connection.commit()
    return {"status": "success"}

@router.delete("/api/projects/{project_id}/spatial-layers/{layer_id}")
def delete_spatial_layer(project_id: str, layer_id: str, request: Request) -> dict[str, str]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    safe_layer_id = _safe_spatial_id(layer_id, "layer_id")
    _ensure_project_owner(int(user["id"]), safe_project_id)
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT id, owner_user_id FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Spatial layer not found")
        if not _can_manage_spatial_feature(user, int(row["owner_user_id"])):
            raise HTTPException(status_code=403, detail="Only the creator or an admin can delete this spatial layer")
        connection.execute(
            "DELETE FROM spatial_features WHERE layer_id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        )
        connection.execute(
            "DELETE FROM spatial_layers WHERE id = ? AND project_id = ?",
            (safe_layer_id, safe_project_id),
        )
        connection.commit()
    return {"status": "success"}

@router.post("/api/projects/{project_id}/spatial-import")
async def import_spatial_layer(
    project_id: str,
    request: Request,
    file: UploadFile = File(...),
) -> dict[str, object]:
    user = _require_user(request)
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_owner(int(user["id"]), safe_project_id)
    safe_name = os.path.basename((file.filename or "").strip())
    if not safe_name or safe_name in {".", ".."} or "/" in safe_name or "\\" in safe_name:
        raise HTTPException(status_code=400, detail="Invalid filename")
    suffix = Path(safe_name).suffix.lower()
    if suffix not in {".kml", ".xml", ".geojson", ".json", ".shp", ".zip"}:
        raise HTTPException(status_code=400, detail="Only .kml, .geojson, .shp, or zipped shapefiles are supported")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / safe_name
        try:
            with open(tmp_path, "wb") as out_f:
                shutil.copyfileobj(file.file, out_f, length=MERGE_COPY_BUFFER_BYTES)
        except OSError as exc:
            raise HTTPException(status_code=500, detail=f"Failed to store import: {exc}") from exc
        finally:
            await file.close()

        text = None
        if suffix in {".kml", ".xml", ".geojson", ".json"}:
            text = tmp_path.read_text(encoding="utf-8", errors="ignore")
        features = parse_spatial_upload(tmp_path, suffix, text)

    source_type = "imported-shp" if suffix in {".shp", ".zip"} else "imported-kml" if suffix in {".kml", ".xml"} else "imported-geojson"
    layer_name = Path(safe_name).stem[:180] or "Imported Layer"
    with get_db_connection() as connection:
        layer_id = _ensure_spatial_layer(
            connection,
            safe_project_id,
            int(user["id"]),
            "",
            layer_name,
            source_type,
        )
        inserted = [
            _insert_spatial_feature(
                connection,
                safe_project_id,
                int(user["id"]),
                layer_id,
                feature,
                "",
                "",
                "Unassigned",
                source_type,
                user,
            )
            for feature in features
        ]
        connection.execute(
            "UPDATE spatial_layers SET updated_at = ? WHERE id = ?",
            (_now_iso(), layer_id),
        )
        connection.commit()

    return {
        "layer": {
            "id": layer_id,
            "project_id": safe_project_id,
            "name": layer_name,
            "source_type": source_type,
            "features": inserted,
        },
        "imported_count": len(inserted),
    }
