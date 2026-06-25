import ast
import os
import shutil

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

SERVICES = {
    "core/security.py": [
        "_hash_password", "_verify_password", "_sign_session_token", "_unsign_session_token", "_token_hash"
    ],
    "core/middleware.py": [
        "Debug404Middleware", "ActivityTrackingMiddleware", "ProtectedDataPathMiddleware"
    ],
    "dependencies.py": [
        "_require_user", "_get_optional_user", "_require_admin", "verify_admin", 
        "_require_upload_user", "_client_ip_for_limit", "_enforce_rate_limit"
    ],
    "services/auth_service.py": [
        "_create_pending_user", "_approval_url", "_send_owner_sms", "_send_email"
    ],
    "services/project_service.py": [
        "get_project_dirs", "get_project_dataset_type_dirs", "_delete_project_storage", 
        "_ensure_project_owner", "_is_admin_user_id"
    ],
    "services/dataset_service.py": [
        "_infer_dataset_type", "_normalize_dataset_type", "_raster_layer_type", 
        "_read_dataset_status", "_write_dataset_status", "_read_dataset_manifest", "_write_dataset_manifest",
        "_status_manifest_payload", "_write_status_manifests", "_sync_dataset_metadata_to_processing_job",
        "_delete_dataset_artifacts", "admin_delete_dataset_by_name", "_deep_rename_dataset_artifacts"
    ],
    "services/upload_service.py": [
        "_upload_session_dir", "_dataset_upload_session_dir", "_ensure_disk_space_for_bytes",
        "_merge_upload_chunks"
    ],
    "services/analysis_service.py": [
        "_sample_raster", "_interpolate_profile_points", "_profile_summary", 
        "_volume_for_raster", "_dtm_volume_between", "_circle_points", "_pixel_area_m2"
    ],
    "services/grid_export_service.py": [
        "_csv_grid_generator", "_dxf_grid_generator", "_generate_grid_export_background",
        "_csv_grid_rows", "_dxf_grid_rows", "_validate_grid_export_request", "_grid_export_is_current"
    ],
    "services/spatial_feature_service.py": [
        "_ensure_spatial_layer", "_insert_spatial_feature", "_spatial_row_to_dict", 
        "_normalize_spatial_feature_geojson", "_can_manage_spatial_feature"
    ],
    "services/bulk_import_service.py": [
        "_browse_server_folder", "_bulk_scan_files", "_admin_manual_bulk_import_background", 
        "_prepare_admin_manual_bulk_import", "_queue_admin_manual_bulk_import"
    ]
}

lines_to_remove = set()
extracted_code = {k: [] for k in SERVICES.keys()}

func_to_module = {}
for mod, funcs in SERVICES.items():
    for f in funcs:
        func_to_module[f] = mod

for node in tree.body:
    # Classes
    if isinstance(node, ast.ClassDef):
        name = node.name
        if name in func_to_module:
            mod = func_to_module[name]
            start_line = node.lineno - 1
            if hasattr(node, "decorator_list") and node.decorator_list:
                start_line = min(d.lineno - 1 for d in node.decorator_list)
            while start_line > 0 and source_lines[start_line - 1].strip().startswith("#"):
                start_line -= 1
            end_line = node.end_lineno
            code_block = "\n".join(source_lines[start_line:end_line])
            extracted_code[mod].append(code_block)
            for i in range(start_line, end_line):
                lines_to_remove.add(i)
                
    # Functions
    elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        name = getattr(node, "name", None)
        if name in func_to_module:
            mod = func_to_module[name]
            start_line = node.lineno - 1
            if hasattr(node, "decorator_list") and node.decorator_list:
                start_line = min(d.lineno - 1 for d in node.decorator_list)
            while start_line > 0 and source_lines[start_line - 1].strip().startswith("#"):
                start_line -= 1
            end_line = node.end_lineno
            code_block = "\n".join(source_lines[start_line:end_line])
            extracted_code[mod].append(code_block)
            for i in range(start_line, end_line):
                lines_to_remove.add(i)

imports = """import os
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

"""

for mod, blocks in extracted_code.items():
    if not blocks:
        continue
    file_path = f"app/{mod}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    if not os.path.exists(os.path.dirname(file_path) + "/__init__.py"):
        open(os.path.dirname(file_path) + "/__init__.py", "w").close()
    
    content = "\n\n".join(blocks) + "\n"
    if mod != "services/project_service.py":
        content = content.replace("get_project_dirs(", "_get_project_dirs(")
    if mod != "services/dataset_service.py":
        content = content.replace("_read_dataset_status(", "_read_dataset_status(")
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(imports + "\n" + content)

new_main_lines = [line for i, line in enumerate(source_lines) if i not in lines_to_remove]

import_lines = [
    "\n# --- PHASE 5 EXTRACTED SERVICES ---"
]
for mod, funcs in SERVICES.items():
    module_path = "app." + mod.replace("/", ".").replace(".py", "")
    import_lines.append(f"from {module_path} import ({', '.join(funcs)})")

insert_idx = 0
for i, line in enumerate(new_main_lines):
    if line.startswith("# --- POINT CLOUD SERVICES ---"):
        insert_idx = i
        break

new_main_lines = new_main_lines[:insert_idx] + import_lines + new_main_lines[insert_idx:]

with open(source_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_main_lines) + "\n")

print(f"Extracted Phase 5 Services")
