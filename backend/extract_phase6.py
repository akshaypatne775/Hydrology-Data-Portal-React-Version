import ast
import os

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

# 101 routes mapped to their files
ROUTERS = {
    'raster_tiles': ["ortho_cog_bounds", "dji_terra_dem_tile", "ortho_cog_tile"],
    'pointclouds': ["pointcloud_status", "process_pointcloud_request", "secure_pointcloud_data_file", "secure_legacy_pointcloud_data_file"],
    'projects': ["project_stats", "survair_stats", "get_projects", "create_project", "update_project", "get_camera_views", "save_camera_view", "delete_camera_view"],
    'auth': ["auth_signup", "auth_request_admin", "auth_login", "auth_logout", "approve_access_request", "auth_me"],
    'admin_users': ["admin_user_activity", "admin_user_projects", "admin_approve_user", "admin_assign_user_role", "admin_reset_user_password", "admin_set_user_catalog_access", "admin_set_user_upload_access", "admin_set_user_location_required", "admin_set_user_hidden_tabs", "admin_disapprove_user", "admin_delete_user", "admin_advanced_delete_user"],
    'admin_projects': ["admin_all_projects", "admin_resync_project_datasets", "admin_cleanup_stale_project_jobs", "admin_manual_bulk_import_for_project", "admin_get_project_override", "admin_patch_project_override", "admin_delete_project", "admin_update_dataset_metadata_by_name", "admin_force_delete_project_file"],
    'admin_import': ["admin_locate_folder", "admin_manual_bulk_import"],
    'media': ["media"],
    'issues': ["get_issues", "create_issue"],
    'spatial': ["get_spatial_layers", "create_spatial_feature", "update_spatial_feature", "delete_spatial_feature", "delete_spatial_layer", "import_spatial_layer"],
    'uploads': ["upload_chunk", "complete_upload", "upload_dataset_chunk", "complete_dataset_upload", "upload_dataset"],
    'datasets': ["process_dataset", "generate_contours", "sync_manual_datasets", "open_manual_dataset_folder", "get_crop_mask", "save_crop_mask_from_kml", "save_crop_mask_from_draw", "dataset_metadata", "dataset_status", "export_dataset_grid", "get_dataset_bounds", "update_dataset_metadata", "update_dataset_owner_metadata"],
    'admin_catalog': ["admin_update_dataset_metadata", "admin_rename_dataset_by_id", "admin_reconcile_catalog"],
    'analysis': ["analysis_elevation", "analysis_profile", "analysis_cross_section", "analysis_volume", "compare_datasets", "compare_volume", "compare_refresh_if_changed"],
    'jobs': ["project_jobs"],
    'proxy': ["proxy_tiles", "proxy_info"],
    'files': ["view_project_report", "download_project_report", "download_project_dataset_raw", "project_catalog_revision", "project_files", "delete_catalog_asset", "bulk_delete_project_datasets", "delete_project_file", "secure_project_data_file", "secure_legacy_project_data_file", "secure_legacy_tiles_file", "start_pointcloud_slice_export"],
    'system': ["health", "api_version", "client_error_log", "run_flood_engine"]
}

# Invert mapping
func_to_router = {}
for router, funcs in ROUTERS.items():
    for f in funcs:
        func_to_router[f] = router

lines_to_remove = set()
extracted_code = {k: [] for k in ROUTERS.keys()}

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = node.name
        if name in func_to_router:
            mod = func_to_router[name]
            start_line = node.lineno - 1
            if hasattr(node, "decorator_list") and node.decorator_list:
                start_line = min(d.lineno - 1 for d in node.decorator_list)
            while start_line > 0 and source_lines[start_line - 1].strip().startswith("#"):
                start_line -= 1
            end_line = node.end_lineno
            
            # replace @app. with @router.
            block_lines = source_lines[start_line:end_line]
            for i, line in enumerate(block_lines):
                if line.strip().startswith("@app."):
                    block_lines[i] = line.replace("@app.", "@router.", 1)
            
            extracted_code[mod].append("\n".join(block_lines))
            for i in range(start_line, end_line):
                lines_to_remove.add(i)

imports = """import os
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

"""

os.makedirs("app/routers", exist_ok=True)
open("app/routers/__init__.py", "w").close()

for router_name, blocks in extracted_code.items():
    if not blocks:
        continue
    file_path = f"app/routers/{router_name}.py"
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(imports + "\n" + "\n\n".join(blocks) + "\n")
    print(f"Created {file_path}")

new_main_lines = [line for i, line in enumerate(source_lines) if i not in lines_to_remove]

import_lines = ["\n# --- PHASE 6 ROUTERS ---"]
for router_name in ROUTERS.keys():
    import_lines.append(f"from app.routers import {router_name}")

# Now inject the include_router calls right after cog_tiler = TilerFactory()
for i, line in enumerate(new_main_lines):
    if "cog_tiler = TilerFactory()" in line:
        insert_idx = i + 1
        break

includes = []
for router_name in ROUTERS.keys():
    includes.append(f"app.include_router({router_name}.router)")

new_main_lines = new_main_lines[:insert_idx] + import_lines + includes + new_main_lines[insert_idx:]

with open(source_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_main_lines) + "\n")

print(f"Extracted Phase 6 Routers and removed {len(lines_to_remove)} lines from main.py")
