import ast
import os
import shutil

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

SERVICES = {
    "services/pointcloud/ept_service.py": [
        "_prepare_las_for_ept", "_run_ept_converter_once", "process_pointcloud_ept_job",
        "_ept_error_needs_las_bbox_repair", "_repair_las_bounding_box",
        "_looks_like_lon_lat_bounds", "_utm_epsg_for_lon_lat"
    ],
    "services/pointcloud/copc_service.py": [
        "_run_copc_converter_once", "_process_copc_ept_compat_job",
        "_copc_ept_compat_dir", "_best_copc_asset", "_copc_asset_in_dir"
    ],
    "services/pointcloud/pointcloud_jobs.py": [
        "process_pointcloud", "process_pointcloud_background", "process_contours_background"
    ],
    "services/pointcloud/pointcloud_slice.py": [
        "_run_pointcloud_slice_export", "_rotation_matrix_xyz", "_finite_vector", "_point_record_value"
    ],
    "services/pointcloud/pdal_tools.py": [
        "_resolve_converter_executable", "_pdal_has_driver"
    ]
}

lines_to_remove = set()
extracted_code = {k: [] for k in SERVICES.keys()}

func_to_module = {}
for mod, funcs in SERVICES.items():
    for f in funcs:
        func_to_module[f] = mod

for node in tree.body:
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

os.makedirs("app/services/pointcloud", exist_ok=True)
open("app/services/pointcloud/__init__.py", "w").close()

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

"""

for mod, blocks in extracted_code.items():
    if not blocks:
        continue
    file_path = f"app/{mod}"
    # replace calls to update_dataset_status with _update_dataset_status
    content = "\n\n".join(blocks) + "\n"
    content = content.replace("update_dataset_status(", "_update_dataset_status(")
    content = content.replace("get_project_dirs(", "_get_project_dirs(")
    content = content.replace("calculate_folder_size(", "_calculate_folder_size(")
    content = content.replace("_format_size_bytes(", "_format_size_bytes(")
    
    with open(file_path, "w", encoding="utf-8") as f:
        f.write(imports + "\n" + content)

new_main_lines = [line for i, line in enumerate(source_lines) if i not in lines_to_remove]

import_lines = [
    "\n# --- POINT CLOUD SERVICES ---",
    "from app.services.pointcloud.ept_service import _prepare_las_for_ept, _run_ept_converter_once, process_pointcloud_ept_job, _ept_error_needs_las_bbox_repair, _repair_las_bounding_box, _looks_like_lon_lat_bounds, _utm_epsg_for_lon_lat",
    "from app.services.pointcloud.copc_service import _run_copc_converter_once, _process_copc_ept_compat_job, _copc_ept_compat_dir, _best_copc_asset, _copc_asset_in_dir",
    "from app.services.pointcloud.pointcloud_jobs import process_pointcloud, process_pointcloud_background, process_contours_background",
    "from app.services.pointcloud.pointcloud_slice import _run_pointcloud_slice_export, _rotation_matrix_xyz, _finite_vector, _point_record_value",
    "from app.services.pointcloud.pdal_tools import _resolve_converter_executable, _pdal_has_driver",
    ""
]

insert_idx = 0
for i, line in enumerate(new_main_lines):
    if line.startswith("from app.services.raster import"):
        insert_idx = i + 1
        break

new_main_lines = new_main_lines[:insert_idx] + import_lines + new_main_lines[insert_idx:]

with open(source_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_main_lines) + "\n")

print(f"Extracted Phase 4 Point Cloud Services")
