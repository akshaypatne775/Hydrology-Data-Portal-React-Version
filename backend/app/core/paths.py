import os
import sys
import time
import math
import json
import uuid
import shutil
import struct
import base64
import hashlib
import asyncio
import logging
import subprocess
from pathlib import Path
from datetime import datetime, timezone
import numpy as np
from fastapi import HTTPException, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from app.core.config import *
from app.core.database import *
from app.models.datasets import *


def _titiler_tile_url_template(
    base_url: str,
    cog_path: str,
    layer_type: str = "",
    rescale_min: str = "",
    rescale_max: str = "",
) -> str:
    params = {"url": cog_path.replace("\\", "/")}
    normalized_layer_type = layer_type.strip().lower().replace(" ", "")
    if normalized_layer_type in {"ortho", "orthomosaic"}:
        return (
            f"{base_url.rstrip('/')}/api/ortho-cog/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    if layer_type in {"DTM", "DSM"} and rescale_min and rescale_max:
        params["rescale"] = f"{rescale_min},{rescale_max}"
        return (
            f"{base_url.rstrip('/')}/api/dji-terra/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    return (
        f"{base_url.rstrip('/')}/api/titiler/tiles/WebMercatorQuad/"
        f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
    )

def _secure_local_cog_path(raw_url: str) -> Path:
    clean_source = _rebase_project_data_path(raw_url)
    if not clean_source:
        raise HTTPException(status_code=400, detail="Missing COG path.")
    target = Path(os.path.abspath(clean_source)).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if target != local_root and not target.is_relative_to(local_root):
        raise HTTPException(status_code=403, detail="COG path is outside project storage.")
    if not target.is_file():
        raise HTTPException(status_code=404, detail="COG file not found.")
    return target

def _primary_copc_dir_for_ept_folder(ept_dir: Path) -> Path | None:
    if not ept_dir.name.endswith("__ept_viewer"):
        return None
    copc_dir = ept_dir.parent / ept_dir.name[: -len("__ept_viewer")]
    return copc_dir if _copc_asset_in_dir(copc_dir) is not None else None

def _should_skip_ept_listing_for_native_copc(ept_dir: Path) -> bool:
    if not POTREE_NATIVE_COPC_ENABLED:
        return False
    if ept_dir.name.endswith("__ept_viewer"):
        return True
    if _copc_asset_in_dir(ept_dir) is not None:
        return True
    primary = _primary_copc_dir_for_ept_folder(ept_dir)
    return primary is not None

def _conversion_cache_file() -> Path:
    return Path(LOCAL_DATA_PATH) / "pointclouds" / "_upload_cache.json"

def _read_conversion_cache() -> dict[str, str]:
    cache_path = _conversion_cache_file()
    if not cache_path.is_file():
        return {}
    try:
        raw = cache_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, json.JSONDecodeError):
        return {}
    return {}

def _write_conversion_cache(data: dict[str, str]) -> None:
    cache_path = _conversion_cache_file()
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass

def _dataset_type_folder(dataset_type: str) -> str:
    normalized = _normalize_dataset_type(dataset_type, "")
    if normalized in {"ortho", "dtm", "dsm", "pointcloud", "csv", "3dmodel", "vector", "cad"}:
        return normalized
    return "other"

def _ept_dataset_name(name: str) -> str:
    stem = Path(name).stem if Path(name).suffix else name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-")
    return _safe_tileset_id(cleaned[:120] or "pointcloud")

def _project_processed_root(project_id: str) -> Path:
    return Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id) / "processed"

def _project_exports_root(project_id: str) -> Path:
    return Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id) / "exports"

def _project_pointcloud_root(project_id: str) -> Path:
    return _project_exports_root(project_id) / "pointclouds"

def _legacy_project_pointcloud_root(project_id: str) -> Path:
    return _project_processed_root(project_id) / "pointclouds"

def _ept_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _project_pointcloud_root(project_id) / _safe_ept_folder_name(dataset_name)

def _legacy_ept_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _project_processed_root(project_id) / _safe_ept_folder_name(dataset_name)

def _legacy_ept_pointcloud_dataset_dir(project_id: str, dataset_name: str) -> Path:
    return _legacy_project_pointcloud_root(project_id) / _safe_ept_folder_name(dataset_name)

def _ept_asset_quality(dataset_dir: Path) -> int:
    ept_json = dataset_dir / "ept.json"
    if not ept_json.is_file() or (dataset_dir / ".conversion_error.txt").is_file():
        return -1
    hierarchy_dir = dataset_dir / "ept-hierarchy"
    data_dir = dataset_dir / "ept-data"
    if not hierarchy_dir.is_dir() or not data_dir.is_dir():
        return -1
    try:
        hierarchy_count = sum(1 for _ in hierarchy_dir.glob("*.json"))
        data_count = sum(1 for p in data_dir.rglob("*") if p.is_file())
        ept_size = ept_json.stat().st_size
    except OSError:
        return -1
    if hierarchy_count < 1 or data_count < 1 or ept_size <= 0:
        return -1
    score = 100
    score += min(hierarchy_count, 5000) * 20
    score += min(data_count, 50000)
    score += min(ept_size // 1024, 50)
    return score

def _ept_asset_candidates(project_id: str, dataset_name: str) -> list[tuple[str, Path]]:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    return [
        (f"exports/pointclouds/{safe_dataset}/ept.json", _ept_dataset_dir(safe_project, safe_dataset)),
        (
            f"processed/pointclouds/{safe_dataset}/ept.json",
            _legacy_ept_pointcloud_dataset_dir(safe_project, safe_dataset),
        ),
        (f"processed/{safe_dataset}/ept.json", _legacy_ept_dataset_dir(safe_project, safe_dataset)),
    ]

def _best_ept_asset(project_id: str, dataset_name: str) -> tuple[str, Path] | None:
    best: tuple[int, int, str, Path] | None = None
    for index, (rel_path, dataset_dir) in enumerate(_ept_asset_candidates(project_id, dataset_name)):
        quality = _ept_asset_quality(dataset_dir)
        if quality < 0:
            continue
        # Prefer newer export output only when quality is equal; otherwise choose
        # the richer hierarchy because the Potree/EPT loader streams it more reliably.
        rank = (quality, -index, rel_path, dataset_dir)
        if best is None or rank > best:
            best = rank
    if best is None:
        return None
    return best[2], best[3]

def _ept_json_url(base_url: str, project_id: str, dataset_name: str) -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    best = _best_ept_asset(safe_project, safe_dataset)
    rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/ept.json"
    return (
        f"{base_url.rstrip('/')}/api/data/projects/{safe_project}/{quote(rel_path, safe='/')}"
    )

def _copc_url(base_url: str, project_id: str, dataset_name: str, asset_rel_path: str = "") -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    rel_path = asset_rel_path.replace("\\", "/").lstrip("/")
    if not rel_path:
        best = _best_copc_asset(safe_project, safe_dataset)
        rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/output.copc.laz"
    return f"{base_url.rstrip('/')}/api/data/projects/{safe_project}/{quote(rel_path, safe='/')}"

def _copc_viewer_url(
    base_url: str,
    project_id: str,
    dataset_name: str,
    display_name: str = "",
    asset_rel_path: str = "",
) -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    copc_path = _copc_url("", safe_project, safe_dataset, asset_rel_path)
    query = urlencode(
        {
            "copc": copc_path,
            "project": safe_project,
            "dataset": safe_dataset,
            "name": display_name or safe_dataset,
        }
    )
    return f"/droid-ept-viewer/index.html?{query}"

def _ept_viewer_url(base_url: str, project_id: str, dataset_name: str, display_name: str = "") -> str:
    safe_project = _safe_project_id(project_id)
    safe_dataset = _safe_ept_folder_name(dataset_name)
    best = _best_ept_asset(safe_project, safe_dataset)
    rel_path = best[0] if best else f"exports/pointclouds/{safe_dataset}/ept.json"
    ept_path = f"/api/data/projects/{safe_project}/{rel_path}"
    query = urlencode(
        {
            "ept": ept_path,
            "project": safe_project,
            "dataset": safe_dataset,
            "name": display_name or safe_dataset,
        }
    )
    return f"/droid-ept-viewer/index.html?{query}"

def _pointcloud_viewer_url(
    base_url: str,
    project_id: str,
    dataset_name: str,
    display_name: str = "",
    viewer_type: str = "copc",
) -> str:
    return _copc_viewer_url(base_url, project_id, dataset_name, display_name)

def _dataset_dir(project_id: str, dataset_id: str) -> Path:
    """Job metadata (.status.json) for a raster upload; tiles live under projects/.../processed/."""
    return Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs" / dataset_id

def _dataset_status_file(project_id: str, dataset_id: str) -> Path:
    return _dataset_dir(project_id, dataset_id) / ".status.json"

def _dataset_manifest_name() -> str:
    return ".droid_dataset.json"

def _manifest_target_for(path: Path) -> Path:
    return path / _dataset_manifest_name() if path.is_dir() else path.parent / _dataset_manifest_name()

def _processing_jobs_file() -> Path:
    return Path(LOCAL_DATA_PATH) / "processing_jobs.json"

def _read_processing_jobs() -> dict[str, list[dict[str, str]]]:
    jobs_path = _processing_jobs_file()
    if not jobs_path.is_file():
        return {}
    try:
        raw = jobs_path.read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            normalized: dict[str, list[dict[str, str]]] = {}
            for project_id, jobs in data.items():
                if isinstance(jobs, list):
                    normalized[str(project_id)] = [
                        {str(k): str(v) for k, v in job.items()}
                        for job in jobs
                        if isinstance(job, dict)
                    ]
            return normalized
    except (OSError, json.JSONDecodeError):
        return {}
    return {}

def _invalidate_project_files_cache(project_id: str) -> None:
    _PROJECT_FILES_CACHE.pop(project_id, None)

def _get_cached_project_files(project_id: str) -> list[dict[str, str]] | None:
    entry = _PROJECT_FILES_CACHE.get(project_id)
    if not entry:
        return None
    ts, data = entry
    if time.time() - ts > PROJECT_FILES_CACHE_TTL_SECONDS:
        _PROJECT_FILES_CACHE.pop(project_id, None)
        return None
    return data

def _set_cached_project_files(project_id: str, files: list[dict[str, str]]) -> None:
    _PROJECT_FILES_CACHE[project_id] = (time.time(), files)

def _looks_like_cesium_tileset_json(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() != ".json":
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    asset = data.get("asset")
    root = data.get("root")
    if not isinstance(asset, dict) or not isinstance(root, dict):
        return False
    has_tiles = any(key in root for key in ("children", "content", "contents"))
    return has_tiles and ("geometricError" in data or "geometricError" in root)

def _find_tileset_json(folder: Path) -> Path | None:
    if not folder.is_dir():
        return None
    direct = folder / "tileset.json"
    if _looks_like_cesium_tileset_json(direct):
        return direct

    candidates: dict[str, Path] = {}
    for pattern in ("*.json", "*/*.json", "*/*/*.json"):
        for candidate in folder.glob(pattern):
            candidates[str(candidate.resolve())] = candidate

    def sort_key(path: Path) -> tuple[int, int, str]:
        name = path.name.lower()
        priority = 0 if name == "tileset.json" else 1 if any(token in name for token in ("production", "scene", "root")) else 2
        return (priority, len(path.relative_to(folder).parts), name)

    for candidate in sorted(candidates.values(), key=sort_key):
        if _looks_like_cesium_tileset_json(candidate):
            return candidate
    return None

def _ensure_tileset_alias(tileset_path: Path) -> Path:
    alias = tileset_path.parent / "tileset.json"
    if tileset_path.name.lower() == "tileset.json":
        return tileset_path
    if not alias.exists():
        shutil.copyfile(tileset_path, alias)
    return alias

def _contains_pointcloud_viewer_asset(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for pattern in (
        "ept.json",
        "*/ept.json",
        "*/*/ept.json",
        "*.copc.laz",
        "*/*.copc.laz",
        "*/*/*.copc.laz",
    ):
        if any(folder.glob(pattern)):
            return True
    return False

def _project_copc_assets(project_id: str) -> list[Path]:
    safe_project_id = _safe_project_id(project_id)
    processed_root = _project_processed_root(safe_project_id)
    roots = (
        _project_pointcloud_root(safe_project_id),
        _legacy_project_pointcloud_root(safe_project_id),
        processed_root,
    )
    assets: dict[str, Path] = {}
    for root in roots:
        if not root.is_dir():
            continue
        for asset in root.rglob("*.copc.laz"):
            if asset.is_file() and asset.stat().st_size > 0:
                assets[str(asset.resolve()).lower()] = asset.resolve()
    return sorted(assets.values(), key=lambda path: (path.stat().st_mtime, path.name.lower()), reverse=True)

def _is_3d_model_dataset(folder: Path) -> bool:
    if _contains_pointcloud_viewer_asset(folder):
        return False
    return _find_tileset_json(folder) is not None

def _candidate_processed_tile_dirs(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted([p for p in processed_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if _is_valid_tile_dataset(child):
            candidates.append(child)
            continue
        for nested in sorted([p for p in child.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            if _is_valid_tile_dataset(nested):
                candidates.append(nested)
    return candidates

def _candidate_processed_cog_files(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    files: dict[str, Path] = {}
    for pattern in ("*.cog.tif", "*.cog.tiff", "*_cog.tif", "*_cog.tiff"):
        for path in processed_root.rglob(pattern):
            if path.is_file():
                files[path.resolve().as_posix()] = path
    return sorted(files.values(), key=lambda p: p.name.lower())

def _candidate_processed_model_dirs(processed_root: Path) -> list[Path]:
    if not processed_root.is_dir():
        return []
    candidates: list[Path] = []
    for child in sorted([p for p in processed_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        if _contains_pointcloud_viewer_asset(child):
            continue
        tileset = _find_tileset_json(child)
        if tileset:
            candidates.append(_ensure_tileset_alias(tileset).parent)
            continue
        for nested in sorted([p for p in child.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
            if _contains_pointcloud_viewer_asset(nested):
                continue
            tileset = _find_tileset_json(nested)
            if tileset:
                candidates.append(_ensure_tileset_alias(tileset).parent)
    return candidates

def _display_model_folder_name(model_root: Path, processed_root: Path) -> str:
    if model_root.name.lower() in {"scene", "data", "tiles"} and model_root.parent != processed_root:
        return model_root.parent.name
    return model_root.name

def _safe_extract_zip(zip_path: Path, extract_root: Path) -> None:
    extract_root.mkdir(parents=True, exist_ok=True)
    root = extract_root.resolve()
    try:
        with zipfile.ZipFile(zip_path, "r") as zip_ref:
            for member in zip_ref.infolist():
                name = member.filename.replace("\\", "/")
                if not name or name.startswith("/") or name.startswith("../") or "/../" in name:
                    raise HTTPException(status_code=400, detail="ZIP contains unsafe paths")
                target = (extract_root / name).resolve()
                if not target.is_relative_to(root):
                    raise HTTPException(status_code=400, detail="ZIP contains unsafe paths")
            zip_ref.extractall(extract_root)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Invalid ZIP file") from exc

def _find_extracted_tileset_root(extract_root: Path) -> Path:
    tileset = _find_tileset_json(extract_root)
    if not tileset:
        raise HTTPException(
            status_code=400,
            detail="ZIP does not contain a Cesium root tileset JSON. Expected tileset.json or a root JSON such as Production_*.json.",
        )
    return _ensure_tileset_alias(tileset).parent

def _write_processing_jobs(data: dict[str, list[dict[str, str]]]) -> None:
    jobs_path = _processing_jobs_file()
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        jobs_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass

def _upsert_processing_job(project_id: str, job: dict[str, str]) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    existing = next((item for item in current if item.get("job_id") == job.get("job_id")), None)
    if isinstance(existing, dict):
        merged = dict(existing)
        merged.update(job)
        job = merged
    current = [item for item in current if item.get("job_id") != job.get("job_id")]
    current.insert(0, job)
    jobs[project_id] = current[:200]
    _write_processing_jobs(jobs)
    catalog_service.mirror_processing_job(project_id, job)
    status = str(job.get("status") or "").strip().lower()
    if status in {"processing", "uploading", "uploaded", "queued", "running", "pending", "converting cog"}:
        _invalidate_project_files_cache(project_id)

def _remove_project_processing_jobs(project_id: str) -> None:
    jobs = _read_processing_jobs()
    if project_id in jobs:
        del jobs[project_id]
        _write_processing_jobs(jobs)

def _pointcloud_slice_exports_root(project_id: str, dataset_id: str) -> Path:
    return _project_exports_root(project_id) / "pointcloud_slices" / _safe_ept_folder_name(dataset_id)

def _read_text_marker(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

def _pointcloud_raw_candidates(project_id: str, dataset_id: str) -> list[Path]:
    safe_project_id = _safe_project_id(project_id)
    safe_dataset_id = _safe_ept_folder_name(dataset_id)
    project_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id
    raw_root = project_root / "raw"
    candidates: list[Path] = []

    def add_candidate(path: Path | None) -> None:
        if not path:
            return
        try:
            resolved = path.resolve()
        except OSError:
            return
        if resolved not in candidates and resolved.suffix.lower() in {".las", ".laz"}:
            candidates.append(resolved)

    status_ids = {safe_dataset_id}
    for job in _read_processing_jobs().get(safe_project_id, []):
        if not isinstance(job, dict):
            continue
        if str(job.get("job_id") or "") == safe_dataset_id:
            file_name = str(job.get("file_name") or "").strip()
            if file_name:
                add_candidate(raw_root / f"{safe_project_id}__{file_name}")
                add_candidate(raw_root / file_name)
            for key in ("dataset_id", "ept_dataset_id", "file_name"):
                value = str(job.get(key) or "").strip()
                if value:
                    status_ids.add(value)

    for status_id in list(status_ids):
        try:
            status = _read_dataset_status(safe_project_id, _safe_dataset_id(status_id))
        except HTTPException:
            status = None
        if status:
            raw_rel = str(status.get("raw_rel_path") or "").strip()
            if raw_rel:
                add_candidate(Path(LOCAL_DATA_PATH) / raw_rel)
            dataset_name = str(status.get("dataset_name") or "").strip()
            if dataset_name:
                add_candidate(raw_root / f"{safe_project_id}__{dataset_name}")
                add_candidate(raw_root / dataset_name)

    for dataset_dir in (
        _ept_dataset_dir(safe_project_id, safe_dataset_id),
        _legacy_ept_pointcloud_dataset_dir(safe_project_id, safe_dataset_id),
        _legacy_ept_dataset_dir(safe_project_id, safe_dataset_id),
    ):
        source_name = _read_text_marker(dataset_dir / ".source_name.txt")
        if source_name:
            add_candidate(raw_root / f"{safe_project_id}__{source_name}")
            add_candidate(raw_root / source_name)
        add_candidate(dataset_dir / "output.copc.laz")

    if raw_root.is_dir():
        normalized_target = _safe_export_stem(safe_dataset_id).lower()
        for source in raw_root.glob("*"):
            if source.suffix.lower() not in {".las", ".laz"}:
                continue
            stem = _safe_export_stem(source.stem).lower()
            if normalized_target in stem or stem in normalized_target:
                add_candidate(source)

    return candidates

def _resolve_pointcloud_slice_source(project_id: str, dataset_id: str) -> Path:
    project_root = (Path(LOCAL_DATA_PATH) / "projects" / _safe_project_id(project_id)).resolve()
    for candidate in _pointcloud_raw_candidates(project_id, dataset_id):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if not resolved.is_file():
            continue
        if project_root not in resolved.parents:
            continue
        return resolved
    raise FileNotFoundError("No LAS/LAZ source found for this point cloud. Reprocess or keep the raw upload available for clipped CSV export.")

def _ring_score(points: list[list[float]]) -> float:
    if len(points) < 3:
        return 0
    score = 0.0
    closed = points + [points[0]]
    for idx in range(len(points)):
        lat_a, lng_a = closed[idx]
        lat_b, lng_b = closed[idx + 1]
        score += lng_a * lat_b - lng_b * lat_a
    return abs(score)

def _normalize_crop_points(points: list[list[float]]) -> list[list[float]]:
    normalized: list[list[float]] = []
    for pair in points:
        if not isinstance(pair, list) or len(pair) < 2:
            continue
        try:
            lat = float(pair[0])
            lng = float(pair[1])
        except (TypeError, ValueError):
            continue
        if not (-90 <= lat <= 90 and -180 <= lng <= 180):
            continue
        normalized.append([lat, lng])
    if len(normalized) >= 2 and normalized[0] == normalized[-1]:
        normalized.pop()
    if len(normalized) < 3:
        raise HTTPException(status_code=400, detail="At least 3 valid points required")
    return normalized

def _extract_kml_points(kml_text: str) -> list[list[float]]:
    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid KML: {exc}") from exc
    candidates: list[list[list[float]]] = []
    for node in root.iter():
        if node.tag.endswith("coordinates") and node.text and node.text.strip():
            points: list[list[float]] = []
            for token in node.text.strip().split():
                parts = token.split(",")
                if len(parts) < 2:
                    continue
                try:
                    lon = float(parts[0])
                    lat = float(parts[1])
                except ValueError:
                    continue
                points.append([lat, lon])
            if len(points) >= 3:
                try:
                    candidates.append(_normalize_crop_points(points))
                except HTTPException:
                    continue
    if not candidates:
        raise HTTPException(status_code=400, detail="KML coordinates not found")
    return max(candidates, key=_ring_score)

def _save_crop_mask(project_id: str, tile_folder: str, source: str, points: list[list[float]]) -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO dataset_crop_masks (project_id, tile_folder, source, points_json, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(project_id, tile_folder)
            DO UPDATE SET source=excluded.source, points_json=excluded.points_json, updated_at=excluded.updated_at
            """,
            (
                project_id,
                tile_folder,
                source,
                json.dumps(points, ensure_ascii=True),
                _now_iso(),
            ),
        )
        connection.commit()

def _get_crop_mask(project_id: str, tile_folder: str) -> dict[str, str] | None:
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT source, points_json, updated_at
            FROM dataset_crop_masks
            WHERE project_id = ? AND tile_folder = ?
            """,
            (project_id, tile_folder),
        ).fetchone()
    if not row:
        return None
    return {
        "source": str(row["source"]),
        "points_json": str(row["points_json"]),
        "updated_at": str(row["updated_at"]),
    }

def _is_valid_tile_dataset(folder: Path) -> bool:
    """
    Accept either:
    - classical tiled-raster metadata (`tilemapresource.xml`), OR
    - plain XYZ output where only zoom folders + PNG tiles exist.
    """
    if not folder.is_dir():
        return False
    if (folder / "tilemapresource.xml").is_file():
        return True
    zoom_dirs = [d for d in folder.iterdir() if d.is_dir() and d.name.isdigit()]
    if not zoom_dirs:
        return False
    for zdir in zoom_dirs:
        if any(p.is_file() and p.suffix.lower() == ".png" for p in zdir.rglob("*.png")):
            return True
    return False

def _read_raster_manual_metadata(file_path: Path, dataset_type: str = "") -> dict[str, str]:
    suffix = file_path.suffix.lower()
    if suffix not in (".tif", ".tiff"):
        return {}
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform_bounds
    except Exception:
        return {}
    try:
        with rasterio.open(str(file_path)) as src:
            out: dict[str, str] = {}
            if src.crs:
                out["source_crs"] = str(src.crs)
                authority = src.crs.to_authority()
                if authority and authority[0] and authority[1]:
                    out["detected_epsg"] = f"{authority[0]}:{authority[1]}"
                bounds = transform_bounds(src.crs, "EPSG:4326", *src.bounds, densify_pts=21)
                clean_bounds = [
                    max(-180.0, float(bounds[0])),
                    max(-85.05112878, float(bounds[1])),
                    min(180.0, float(bounds[2])),
                    min(85.05112878, float(bounds[3])),
                ]
                if all(math.isfinite(value) for value in clean_bounds):
                    out["bounds_wgs84"] = json.dumps(clean_bounds)
            normalized_type = _normalize_dataset_type(dataset_type, file_path.name)
            rescale = _sample_raster_percentiles(src, normalized_type)
            if rescale:
                out["rescale_min"] = str(rescale[0])
                out["rescale_max"] = str(rescale[1])
            return out
    except Exception:
        return {}

def _backfill_processed_sizes() -> None:
    projects_root = Path(LOCAL_DATA_PATH) / "projects"
    if not projects_root.is_dir():
        return
    for project_dir in projects_root.iterdir():
        if not project_dir.is_dir():
            continue
        project_id = project_dir.name
        changed_any = False
        for st in _project_dataset_statuses(project_id):
            dataset_id = str(st.get("dataset_id") or "").strip()
            if not dataset_id or str(st.get("processed_size") or "").strip():
                continue
            rel = str(st.get("tiles_rel_path") or st.get("model_rel_path") or st.get("vector_rel_path") or "").strip()
            path = Path(LOCAL_DATA_PATH) / rel if rel else None
            if not path or not path.exists():
                tile_folder = str(st.get("tile_folder") or "").strip()
                if tile_folder:
                    _, processed_root = get_project_dirs(project_id)
                    path = processed_root / tile_folder
            if not path or not path.exists():
                continue
            size_bytes = calculate_folder_size(path)
            st["processed_size_bytes"] = str(size_bytes)
            st["processed_size"] = _format_size_bytes(size_bytes)
            _write_dataset_status(project_id, dataset_id, st)
            changed_any = True
        if changed_any:
            _invalidate_project_files_cache(project_id)

def _resolve_dataset_tiles_dir(project_id: str, dataset_name: str) -> Path | None:
    processed_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "processed"
    direct_candidates = [processed_root / dataset_name]
    for dtype in ("ortho", "dtm", "dsm", "other"):
        direct_candidates.append(processed_root / dtype / dataset_name)
    for direct in direct_candidates:
        if _is_valid_tile_dataset(direct):
            return direct
        if direct.is_dir():
            return direct

    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs"
    if not jobs_root.is_dir():
        return None

    target_variants = {
        dataset_name.strip(),
        f"{dataset_name.strip()}.tif",
        f"{dataset_name.strip()}.tiff",
    }
    for job_dir in jobs_root.iterdir():
        if not job_dir.is_dir():
            continue
        status_path = job_dir / ".status.json"
        if not status_path.is_file():
            continue
        try:
            loaded = json.loads(status_path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict):
                continue
        except (OSError, json.JSONDecodeError, TypeError):
            continue
        name = str(loaded.get("dataset_name", "")).strip()
        tile_folder = str(loaded.get("tile_folder", "")).strip()
        if name in target_variants:
            tiles_rel_path = str(loaded.get("tiles_rel_path", "")).strip()
            candidates: list[Path] = []
            if tiles_rel_path:
                candidates.append(Path(LOCAL_DATA_PATH) / tiles_rel_path)
            if tile_folder:
                candidates.append(processed_root / tile_folder)
                for dtype in ("ortho", "dtm", "dsm", "other"):
                    candidates.append(processed_root / dtype / tile_folder)
            for resolved in candidates:
                if _is_valid_tile_dataset(resolved):
                    return resolved
                if resolved.is_dir():
                    return resolved
    return None

def _analysis_cache_dir(project_id: str) -> Path:
    path = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_analysis_cache"
    path.mkdir(parents=True, exist_ok=True)
    return path

def _cache_path(project_id: str, kind: str, payload: object) -> Path:
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode("utf-8")).hexdigest()
    return _analysis_cache_dir(project_id) / f"{kind}-{digest[:24]}.json"

def _read_cache(path: Path) -> dict[str, object] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError, TypeError):
        return None

def _write_cache(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")

def _project_dataset_statuses(project_id: str) -> list[dict[str, str]]:
    jobs_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "_dataset_jobs"
    if not jobs_root.is_dir():
        return []
    rows: list[dict[str, str]] = []
    for job_dir in sorted([p for p in jobs_root.iterdir() if p.is_dir()], key=lambda p: p.name.lower()):
        st = _read_dataset_status(project_id, job_dir.name)
        if not st:
            continue
        rows.append({**st, "dataset_id": st.get("dataset_id") or job_dir.name})
    return rows

def _dataset_status_by_id(project_id: str, dataset_id: str) -> dict[str, str]:
    safe_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(project_id, safe_id)
    if not st:
        raise HTTPException(status_code=404, detail="Dataset not found")
    st["dataset_id"] = st.get("dataset_id") or safe_id
    return st

def _dataset_source_path(project_id: str, dataset_id: str) -> Path:
    st = _dataset_status_by_id(project_id, dataset_id)
    raw_rel = (st.get("raw_rel_path") or "").strip()
    if raw_rel:
        path = (Path(LOCAL_DATA_PATH) / raw_rel).resolve()
        if path.is_file():
            return path
    tile_folder = (st.get("tile_folder") or "").strip()
    raw_dir, _ = get_project_dirs(project_id)
    for ext in (".tif", ".tiff", ".csv"):
        candidate = raw_dir / f"{tile_folder}{ext}"
        if tile_folder and candidate.is_file():
            return candidate.resolve()
    raise HTTPException(status_code=404, detail="Source file not found")

def _grid_coordinate_range(start: float, stop: float, step: float, descending: bool = False):
    value = float(start)
    stop_value = float(stop)
    step_value = abs(float(step))
    if descending:
        while value >= stop_value:
            yield value
            value -= step_value
    else:
        while value <= stop_value:
            yield value
            value += step_value

def _grid_sample_value(sample, nodata) -> float | None:
    value = sample[0]
    if np.ma.is_masked(value):
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    if nodata is not None and np.isclose(value, float(nodata)):
        return None
    return value

def _grid_export_raster_path(project_id: str, dataset_id: str) -> tuple[Path, dict[str, str]]:
    st = _dataset_status_by_id(project_id, dataset_id)
    layer_type = st.get("layer_type") or _raster_layer_type(
        st.get("dataset_type", ""),
        st.get("dataset_name", dataset_id),
    )
    if layer_type not in {"DTM", "DSM"}:
        raise HTTPException(status_code=400, detail="Grid export is available only for DTM/DSM datasets.")

    local_root = Path(LOCAL_DATA_PATH).resolve()
    for key in ("cog_path", "raw_rel_path"):
        value = (st.get(key) or "").strip()
        if not value:
            continue
        path = Path(value).resolve() if key == "cog_path" else (local_root / value).resolve()
        if path.is_file() and (path == local_root or path.is_relative_to(local_root)):
            return path, st

    cog_rel = (st.get("cog_rel_path") or "").strip()
    if cog_rel:
        path = (local_root / cog_rel).resolve()
        if path.is_file() and path.is_relative_to(local_root):
            return path, st

    return _dataset_source_path(project_id, dataset_id), st

def _grid_export_output_path(
    project_id: str,
    dataset_id: str,
    output_name: str,
) -> Path:
    export_root = Path(LOCAL_DATA_PATH) / "projects" / project_id / "exports" / "grid" / dataset_id
    export_root.mkdir(parents=True, exist_ok=True)
    return export_root / os.path.basename(output_name)

def _write_grid_export_metadata(
    output_path: Path,
    payload: dict[str, str | int | float],
) -> None:
    metadata_path = output_path.with_suffix(f"{output_path.suffix}.json")
    try:
        metadata_path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass

def _generate_grid_export_file(
    output_path: Path,
    dataset_path: Path,
    interval: float,
    export_format: str,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(f"{output_path.name}.tmp")
    generator = _csv_grid_generator(dataset_path, interval) if export_format == "csv" else _dxf_grid_generator(dataset_path, interval)
    try:
        with tmp_path.open("w", encoding="utf-8", newline="") as handle:
            for chunk in generator:
                if chunk:
                    handle.write(chunk)
        tmp_path.replace(output_path)
    finally:
        tmp_path.unlink(missing_ok=True)

def _require_rasterio():
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform as rio_transform  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=501,
            detail="Raster analysis requires rasterio. Install rasterio in backend environment.",
        ) from exc
    return rasterio, rio_transform

def _read_volume_csv(path: Path) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            month = str(row.get("month") or row.get("Month") or row.get("date") or "").strip()
            label = str(row.get("label") or row.get("Label") or month or path.stem).strip()
            def num(*keys: str) -> float:
                for key in keys:
                    val = row.get(key)
                    if val not in (None, ""):
                        try:
                            return float(str(val).replace(",", ""))
                        except ValueError:
                            continue
                return 0.0
            rows.append({
                "month": month,
                "label": label,
                "volume": num("volume", "Volume", "net", "Net"),
                "cut": num("cut", "Cut"),
                "fill": num("fill", "Fill"),
                "net": num("net", "Net", "volume", "Volume"),
                "area": num("area", "Area"),
                "source": "csv",
            })
    return rows

def _admin_dataset_status_by_key(project_id: str, dataset_key: str) -> tuple[str, dict[str, str]]:
    raw_key = dataset_key.replace("\\", "/").strip().strip("/")
    clean_key = os.path.basename(raw_key)
    if not clean_key or clean_key in {".", ".."}:
        raise HTTPException(status_code=400, detail="Invalid dataset_name")
    decoded = clean_key
    normalized_decoded = _safe_export_stem(decoded).lower()
    for st in _project_dataset_statuses(project_id):
        dataset_id = str(st.get("dataset_id") or "")
        dataset_name = str(st.get("dataset_name") or "")
        tile_folder = str(st.get("tile_folder") or "")
        raw_rel = str(st.get("raw_rel_path") or "")
        candidates = {
            dataset_id,
            dataset_name,
            Path(dataset_name).stem,
            tile_folder,
            Path(tile_folder).name,
            Path(raw_rel).name,
            Path(raw_rel).stem,
        }
        normalized_candidates = {_safe_export_stem(candidate).lower() for candidate in candidates if candidate}
        if decoded in candidates or normalized_decoded in normalized_candidates:
            return dataset_id, st
    raise HTTPException(status_code=404, detail="Dataset not found")

def _remove_processing_job(project_id: str, dataset_id: str) -> None:
    jobs = _read_processing_jobs()
    current = jobs.get(project_id, [])
    jobs[project_id] = [item for item in current if str(item.get("job_id")) != dataset_id]
    _write_processing_jobs(jobs)
    catalog_service.remove_asset_db(project_id, dataset_id)

def _safe_remove_dataset_path(path: Path) -> int:
    local_root = Path(LOCAL_DATA_PATH).resolve()
    target = path.resolve()
    if target == local_root or not target.is_relative_to(local_root):
        raise HTTPException(status_code=400, detail="Invalid dataset target path")
    if not target.exists():
        return 0
    if target.is_dir():
        shutil.rmtree(target)
        return 1
    target.unlink(missing_ok=True)
    return 1

def _safe_rename_dataset_path(path: Path, display_name: str) -> Path:
    local_root = Path(LOCAL_DATA_PATH).resolve()
    target = path.resolve()
    if not target.exists() or target == local_root or not target.is_relative_to(local_root):
        return path
    clean_stem = _safe_export_stem(display_name).strip("-_ .")[:120] or "dataset"
    if target.is_file():
        prefix = ""
        name = target.name
        if "__" in name:
            prefix = name.split("__", 1)[0] + "__"
        next_path = target.with_name(f"{prefix}{clean_stem}{target.suffix}")
    else:
        next_path = target.with_name(clean_stem)
    if next_path.resolve() == target:
        return target
    if next_path.exists():
        next_path = target.with_name(f"{next_path.stem}-{secrets.token_hex(4)}{next_path.suffix}")
    target.rename(next_path)
    return next_path

def _dataset_status_matches_rel(project_id: str, st: dict[str, str], rel_path: str) -> bool:
    rel_path = rel_path.replace("\\", "/").strip("/")
    candidates = [
        str(st.get("raw_rel_path") or ""),
        str(st.get("tiles_rel_path") or ""),
        str(st.get("tileset_rel_path") or ""),
        str(st.get("vector_rel_path") or ""),
        str(st.get("model_rel_path") or ""),
        str(st.get("cog_rel_path") or ""),
    ]
    tile_folder = str(st.get("tile_folder") or "").strip()
    if tile_folder:
        _, processed_root = get_project_dirs(project_id)
        candidates.append((processed_root / tile_folder).relative_to(Path(LOCAL_DATA_PATH)).as_posix())
        dtype = str(st.get("dataset_type") or "").strip()
        typed_root = processed_root / _dataset_type_folder(dtype) / tile_folder
        candidates.append(typed_root.relative_to(Path(LOCAL_DATA_PATH)).as_posix())
    for candidate in candidates:
        clean = candidate.replace("\\", "/").strip("/")
        if not clean:
            continue
        if rel_path == clean or rel_path.startswith(f"{clean}/") or clean.startswith(f"{rel_path}/"):
            return True
    return False

def _find_dataset_status_for_rel(project_id: str, rel_path: str) -> tuple[str, dict[str, str]] | None:
    for st in _project_dataset_statuses(project_id):
        dataset_id = str(st.get("dataset_id") or "").strip()
        if dataset_id and _dataset_status_matches_rel(project_id, st, rel_path):
            return dataset_id, st
    return None

def _dataset_extra_response_fields(st: dict[str, str]) -> dict[str, str]:
    cog_rel_path = str(st.get("cog_rel_path") or "")
    cog_path = str(st.get("cog_path") or "")
    if cog_rel_path:
        cog_path = str((Path(LOCAL_DATA_PATH) / cog_rel_path).resolve())
    elif cog_path:
        cog_path = _rebase_project_data_path(cog_path)
    return {
        "processed_size": str(st.get("processed_size") or ""),
        "upload_date": str(st.get("upload_date") or st.get("date") or st.get("created_at") or ""),
        "height_offset": str(st.get("height_offset") or ""),
        "stage": str(st.get("stage") or ""),
        "progress_percent": str(st.get("progress_percent") or ""),
        "eta_seconds": str(st.get("eta_seconds") or ""),
        "cog_path": cog_path,
        "cog_rel_path": cog_rel_path,
        "rescale_min": str(st.get("rescale_min") or ""),
        "rescale_max": str(st.get("rescale_max") or ""),
        "bounds_wgs84": str(st.get("bounds_wgs84") or ""),
        "source_crs": str(st.get("source_crs") or ""),
        "detected_epsg": str(st.get("detected_epsg") or ""),
        "manual_epsg": str(st.get("manual_epsg") or ""),
        "applied_epsg": str(st.get("applied_epsg") or ""),
    }

def _canonical_file_row(row: dict[str, str]) -> dict[str, str]:
    dataset_id = str(row.get("dataset_id") or "").strip()
    rel_path = str(row.get("rel_path") or row.get("raw_rel_path") or row.get("cog_rel_path") or "").strip()
    display_name = str(row.get("display_name") or row.get("name") or dataset_id or Path(rel_path).name).strip()
    viewer_url = str(row.get("viewer_url") or row.get("layer_url") or row.get("file_url") or "").strip()
    canonical_key = str(row.get("canonical_key") or dataset_id or rel_path or display_name).strip()
    row["display_name"] = display_name
    row["name"] = str(row.get("name") or display_name)
    row["viewer_url"] = viewer_url
    row["asset_status"] = str(row.get("asset_status") or row.get("status") or "").strip()
    row["canonical_key"] = canonical_key
    row["source_rel_path"] = str(row.get("source_rel_path") or row.get("raw_rel_path") or rel_path)
    return row

def _ensure_project_file_access(request: Request, project_id: str) -> dict[str, str | int]:
    user = _require_user(request)
    if str(user.get("role", "")).lower() != "admin":
        _ensure_project_owner(int(user["id"]), project_id)
    return user

def _safe_project_file_response_path(project_id: str, file_path: str) -> Path:
    safe_project_id = _safe_project_id(project_id)
    base_dir = (Path(LOCAL_DATA_PATH) / "projects" / safe_project_id).resolve()
    cleaned_path = file_path.replace("\\", "/").lstrip("/")
    if "\x00" in cleaned_path:
        raise HTTPException(status_code=400, detail="Invalid file path")
    target_path = (base_dir / cleaned_path).resolve()
    base_abs = os.path.abspath(str(base_dir))
    target_abs = os.path.abspath(str(target_path))
    if target_abs != base_abs and not target_abs.startswith(base_abs + os.sep):
        raise HTTPException(status_code=400, detail="Invalid file path")
    if target_path.is_dir():
        copc_candidate = _copc_asset_in_dir(target_path)
        if copc_candidate is not None:
            target_path = copc_candidate
    if not target_path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    return target_path

def _copc_range_response(target_path: Path, request: Request) -> Response:
    file_size = target_path.stat().st_size
    range_header = request.headers.get("range")
    common_headers = {
        "Accept-Ranges": "bytes",
        "Cache-Control": "private, max-age=86400",
        "Content-Type": "application/octet-stream",
    }
    if not range_header:
        response = FileResponse(str(target_path), media_type="application/octet-stream")
        response.headers.update(common_headers)
        return response

    match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header.strip())
    if not match:
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})

    start_raw, end_raw = match.groups()
    if start_raw == "" and end_raw == "":
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
    if start_raw == "":
        suffix_length = int(end_raw)
        if suffix_length <= 0:
            raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
        start = max(file_size - suffix_length, 0)
        end = file_size - 1
    else:
        start = int(start_raw)
        end = int(end_raw) if end_raw else file_size - 1

    if start >= file_size or start < 0 or end < start:
        raise HTTPException(status_code=416, detail="Invalid range", headers={"Content-Range": f"bytes */{file_size}"})
    end = min(end, file_size - 1)
    content_length = end - start + 1

    def iter_file() -> bytes:
        with open(target_path, "rb") as handle:
            handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                remaining -= len(chunk)
                yield chunk

    headers = {
        **common_headers,
        "Content-Range": f"bytes {start}-{end}/{file_size}",
        "Content-Length": str(content_length),
    }
    return StreamingResponse(iter_file(), status_code=206, headers=headers, media_type="application/octet-stream")

def _serve_project_data_file(project_id: str, file_path: str, request: Request) -> Response:
    safe_project_id = _safe_project_id(project_id)
    _ensure_project_file_access(request, safe_project_id)
    target_path = _safe_project_file_response_path(safe_project_id, file_path)
    cleaned_request_path = file_path.replace("\\", "/").lower().lstrip("/")
    if cleaned_request_path.startswith("raw/") and target_path.suffix.lower() in {".las", ".laz"}:
        raise HTTPException(status_code=404, detail="Raw point cloud download is not available")
    if target_path.name.lower().endswith(".copc.laz"):
        return _copc_range_response(target_path, request)
    response = FileResponse(str(target_path))
    if cleaned_request_path.startswith("processed/"):
        response.headers["Cache-Control"] = "private, max-age=86400"
    return response

def _serve_pointcloud_data_file(project_id: str, file_path: str, request: Request) -> FileResponse:
    _safe_project_id(project_id)
    _require_user(request)
    raise HTTPException(
        status_code=410,
        detail="Legacy point cloud file serving is disabled. Use the Droid EPT viewer endpoint.",
    )

def _secure_dataset_file(project_id: str, dataset_id: str, report_only: bool = False) -> tuple[Path, dict[str, str]]:
    safe_dataset_id = _safe_dataset_id(dataset_id)
    st = _read_dataset_status(project_id, safe_dataset_id)
    if not st:
        if report_only:
            reports_dir = Path(LOCAL_DATA_PATH) / "reports" / project_id
            if reports_dir.is_dir():
                for report in reports_dir.rglob("*.pdf"):
                    report_id = re.sub(r"[^A-Za-z0-9._-]+", "-", report.stem).strip("-")[:180]
                    if report_id == safe_dataset_id:
                        return report.resolve(), {"dataset_name": report.name, "dataset_type": "reports"}
        raise HTTPException(status_code=404, detail="File not found")
    rel = str(st.get("report_rel_path") or st.get("raw_rel_path") or "").strip()
    if not rel:
        raise HTTPException(status_code=404, detail="File not found")
    if not report_only and str(st.get("dataset_type") or "").lower() == "pointcloud":
        raise HTTPException(status_code=404, detail="Raw point cloud download is not available. Open the processed 3D viewer instead.")
    if report_only and str(st.get("dataset_type") or "").lower() != "reports":
        raise HTTPException(status_code=404, detail="Report not found")
    if ".." in rel or rel.startswith("/") or rel.startswith("\\"):
        raise HTTPException(status_code=400, detail="Invalid file path")
    path = (Path(LOCAL_DATA_PATH) / rel).resolve()
    local_root = Path(LOCAL_DATA_PATH).resolve()
    if local_root not in path.parents or not path.is_file():
        raise HTTPException(status_code=404, detail="File not found")
    if report_only and path.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail="Report not found")
    return path, st

def _dedupe_pointcloud_file_rows(files: list[dict[str, str]], project_id: str) -> list[dict[str, str]]:
    def canonical_pointcloud_key(value: object) -> str:
        text = str(value or "").strip()
        if not text:
            return ""
        try:
            text = unquote(text)
        except Exception:
            pass
        text = Path(text.replace("\\", "/")).name
        stem = Path(text).stem.lower()
        stem = stem.replace(project_id.lower(), "")
        stem = re.sub(r"^(?:ept|copc|pointcloud|point-cloud|pc)(?=[0-9._\-\s])[\W_]*", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[\W_]*(?:ept|copc|pointcloud|point-cloud|pc)$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[-_][a-f0-9]{8,}$", "", stem, flags=re.IGNORECASE)
        stem = re.sub(r"[^a-z0-9]+", "", stem)
        return stem

    def pointcloud_keys(*values: object) -> set[str]:
        keys: set[str] = set()
        for value in values:
            key = canonical_pointcloud_key(value)
            if key:
                keys.add(key)
            stem = Path(str(value or "")).stem.lower()
            if stem:
                keys.add(stem)
        return keys

    def _is_pointcloud_catalog_row(row: dict[str, str]) -> bool:
        signature = " ".join(
            str(row.get(key) or "").lower()
            for key in ("kind", "type", "layer_type", "dataset_type", "name", "viewer_type")
        )
        if "pointcloud" in signature or "point cloud" in signature:
            return True
        name = str(row.get("name") or row.get("display_name") or "").lower()
        rel = str(row.get("rel_path") or row.get("raw_rel_path") or row.get("source_rel_path") or "").lower()
        if name.endswith((".las", ".laz", ".copc.laz")) or rel.endswith((".las", ".laz", ".copc.laz")):
            return True
        viewer_url = str(row.get("viewer_url") or row.get("layer_url") or "").lower()
        if "/droid-ept-viewer/" in viewer_url or viewer_url.endswith("/ept.json") or "copc=" in viewer_url:
            return True
        return str(row.get("viewer_type") or "").lower() == "copc"

    def pointcloud_row_rank(row: dict[str, str]) -> int:
        viewer_url = str(row.get("viewer_url") or row.get("layer_url") or "").strip().lower()
        if str(row.get("viewer_type") or "").lower() == "copc":
            return 0
        if viewer_url and ("copc=" in viewer_url or viewer_url.endswith(".copc.laz")):
            return 0
        status = str(row.get("status") or "").strip().lower()
        if status in {"processing", "uploaded", "queued", "running"}:
            return 1
        name = str(row.get("name") or row.get("display_name") or "").lower()
        rel = str(row.get("rel_path") or row.get("raw_rel_path") or "").lower()
        if (name.endswith((".las", ".laz")) or rel.endswith((".las", ".laz"))) and not viewer_url:
            return 9
        return 2

    canonical_files: list[dict[str, str]] = []
    pointcloud_groups: list[dict[str, object]] = []
    ignored_identity_keys = {"ept", "copc", "pointcloud", "point-cloud", "pc", "output", "index", "las", "laz"}
    for index, file_row in enumerate(files):
        row = _canonical_file_row(file_row)
        if not _is_pointcloud_catalog_row(row):
            canonical_files.append(row)
            continue
        keys = {
            key
            for key in pointcloud_keys(
                row.get("canonical_key"),
                row.get("display_name"),
                row.get("name"),
                row.get("dataset_id"),
                row.get("source_rel_path"),
                row.get("raw_rel_path"),
                row.get("rel_path"),
            )
            if len(key) >= 3 and key not in ignored_identity_keys
        }
        matching = [group for group in pointcloud_groups if keys.intersection(group["keys"])]
        if not matching:
            pointcloud_groups.append({"keys": set(keys), "rows": [(index, row)]})
            continue
        primary = matching[0]
        primary["keys"].update(keys)
        primary["rows"].append((index, row))
        for extra in matching[1:]:
            primary["keys"].update(extra["keys"])
            primary["rows"].extend(extra["rows"])
            pointcloud_groups.remove(extra)

    for group in pointcloud_groups:
        ranked_rows = sorted(
            group["rows"],
            key=lambda item: (pointcloud_row_rank(item[1]), item[0]),
        )
        winner = dict(ranked_rows[0][1])
        for _, candidate in ranked_rows[1:]:
            for field, value in candidate.items():
                if not str(winner.get(field) or "").strip() and str(value or "").strip():
                    winner[field] = value
        if pointcloud_row_rank(winner) >= 9 and any(pointcloud_row_rank(row) <= 2 for _, row in ranked_rows):
            continue
        if str(winner.get("viewer_url") or "").strip():
            winner["status"] = "WEB-READY"
            winner["asset_status"] = "WEB-READY"
            winner["layer_type"] = "pointcloud"
            winner["dataset_type"] = "pointcloud"
        canonical_files.append(_canonical_file_row(winner))
    return canonical_files

def _purge_catalog_dataset(project_id: str, dataset_key: str) -> dict[str, int | str]:
    safe_project_id = _safe_project_id(project_id)
    clean_key = str(dataset_key or "").strip()
    if not clean_key:
        raise HTTPException(status_code=400, detail="Invalid dataset key")
    removed = 0
    if catalog_service.catalog_db_enabled():
        removed += catalog_service.delete_assets_by_key(
            safe_project_id,
            clean_key,
            local_data_path=LOCAL_DATA_PATH,
        )
    jobs = _read_processing_jobs()
    current = jobs.get(safe_project_id, [])
    normalized_key = _safe_export_stem(clean_key).lower()
    next_jobs: list[dict[str, str]] = []
    for job in current:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("job_id") or "")
        file_name = str(job.get("file_name") or "")
        job_stem = _safe_export_stem(Path(file_name).stem or file_name).lower()
        job_id_stem = _safe_export_stem(job_id).lower()
        if (
            clean_key in {job_id, file_name}
            or normalized_key in {job_stem, job_id_stem}
            or normalized_key in job_id.lower()
            or normalized_key in file_name.lower()
        ):
            catalog_service.remove_asset_db(safe_project_id, job_id)
            continue
        next_jobs.append(job)
    if len(next_jobs) != len(current):
        jobs[safe_project_id] = next_jobs
        _write_processing_jobs(jobs)

    candidate_names = {clean_key, Path(clean_key).stem, os.path.basename(clean_key)}
    for name in candidate_names:
        if not name:
            continue
        for resolver in (
            _ept_dataset_dir,
            _legacy_ept_pointcloud_dataset_dir,
            _legacy_ept_dataset_dir,
        ):
            candidate = resolver(safe_project_id, name)
            if candidate.exists():
                removed += _safe_remove_dataset_path(candidate)
            compat = candidate.with_name(f"{candidate.name}__ept_viewer")
            if compat.exists():
                removed += _safe_remove_dataset_path(compat)
        raw_root = Path(LOCAL_DATA_PATH) / "projects" / safe_project_id / "raw"
        for raw_candidate in (
            raw_root / name,
            raw_root / f"{safe_project_id}__{name}",
            raw_root / f"{safe_project_id}__{Path(name).name}",
        ):
            if raw_candidate.exists():
                removed += _safe_remove_dataset_path(raw_candidate)

    try:
        dataset_id, st = _admin_dataset_status_by_key(safe_project_id, clean_key)
        removed += _delete_dataset_artifacts(safe_project_id, dataset_id, st)
    except HTTPException:
        pass

    _invalidate_project_files_cache(safe_project_id)
    if catalog_service.catalog_db_enabled():
        catalog_service.prune_missing_assets(safe_project_id, LOCAL_DATA_PATH)
        catalog_service.bump_revision(safe_project_id)
    return {"status": "success", "removed_paths": removed}
