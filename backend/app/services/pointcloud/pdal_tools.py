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


def _resolve_converter_executable(executable: str) -> str | None:
    executable = (executable or "").strip()
    if not executable:
        return None
    candidate = Path(executable)
    if candidate.is_file():
        return str(candidate)
    resolved = shutil.which(executable)
    return resolved or None

def _pdal_has_driver(pdal_exe: str, driver_name: str) -> bool:
    try:
        result = subprocess.run([pdal_exe, "--drivers"], capture_output=True, text=True, timeout=30)
    except Exception:
        return False
    if result.returncode != 0:
        return False
    output = f"{result.stdout}\n{result.stderr}".lower()
    return driver_name.lower() in output
