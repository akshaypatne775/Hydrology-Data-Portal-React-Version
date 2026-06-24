from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

from app.core.database import get_db_connection

_READY_STATUSES = {"completed", "web-ready", "web ready", "ready"}
_ACTIVE_STATUSES = {
    "processing",
    "uploading",
    "uploaded",
    "queued",
    "running",
    "pending",
    "failed",
    "error",
    "converting cog",
}


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def catalog_db_enabled() -> bool:
    return _env_true(os.getenv("CATALOG_DB_ENABLED"))


def catalog_json_mirror() -> bool:
    return _env_true(os.getenv("CATALOG_JSON_MIRROR", "true"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _path_size_bytes(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


def _format_size_bytes(size_bytes: int) -> str:
    if size_bytes <= 0:
        return ""
    gb = size_bytes / (1024 * 1024 * 1024)
    if gb >= 1:
        return f"{gb:.2f} GB"
    mb = size_bytes / (1024 * 1024)
    return f"{mb:.0f} MB"


def _json_loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if not value:
        return {}
    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _json_dumps(value: dict[str, Any]) -> str:
    return json.dumps(value or {}, ensure_ascii=True)


def _normalize_status(status: str) -> str:
    text = str(status or "").strip()
    if not text:
        return "Processing"
    lowered = text.lower()
    if lowered in _READY_STATUSES:
        return "WEB-READY"
    if lowered == "failed":
        return "Failed"
    if lowered in {"uploading", "uploaded"}:
        return text.title()
    if lowered in {"processing", "queued", "running", "pending"}:
        return "Processing"
    return text


def _is_ready_status(status: str) -> bool:
    return _normalize_status(status).upper() in {"WEB-READY", "COMPLETED"}


def _catalog_asset_type_fields(asset_type: str, meta: dict[str, Any]) -> dict[str, str]:
    normalized = str(asset_type or meta.get("dataset_type") or "dataset").strip().lower().replace(" ", "")
    layer_type = str(meta.get("layer_type") or "").strip()
    if not layer_type:
        if normalized == "ortho":
            layer_type = "Ortho"
        elif normalized == "dtm":
            layer_type = "DTM"
        elif normalized == "dsm":
            layer_type = "DSM"
        elif normalized == "pointcloud":
            layer_type = "pointcloud"
        elif normalized == "3dmodel":
            layer_type = "3DModel"
        elif normalized == "vector":
            layer_type = "Vector"
        elif normalized == "cad":
            layer_type = "CAD"
        elif normalized == "reports":
            layer_type = "Reports"
        else:
            layer_type = normalized
    kind_map = {
        "ortho": "Ortho",
        "dtm": "DTM",
        "dsm": "DSM",
        "pointcloud": "pointcloud",
        "3dmodel": "3DModel",
        "vector": "Vector",
        "cad": "CAD",
        "reports": "Reports",
        "cog": "cog",
    }
    canonical_type = "3DModel" if normalized == "3dmodel" else normalized
    return {
        "kind": kind_map.get(normalized, str(asset_type or "dataset").title()),
        "type": canonical_type,
        "dataset_type": normalized,
        "layer_type": layer_type,
    }


def _raster_tile_url_template(
    base_url: str,
    cog_path: str,
    layer_type: str,
    rescale_min: str = "",
    rescale_max: str = "",
) -> str:
    params = {"url": cog_path.replace("\\", "/")}
    normalized = layer_type.strip().lower().replace(" ", "")
    if normalized in {"ortho", "orthomosaic"}:
        return (
            f"{base_url.rstrip('/')}/api/ortho-cog/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    if normalized in {"dtm", "dsm", "dem"} and rescale_min and rescale_max:
        params["rescale"] = f"{rescale_min},{rescale_max}"
        return (
            f"{base_url.rstrip('/')}/api/dji-terra/tiles/WebMercatorQuad/"
            f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
        )
    return (
        f"{base_url.rstrip('/')}/api/titiler/tiles/WebMercatorQuad/"
        f"{{z}}/{{x}}/{{y}}@1x?{urlencode(params)}"
    )


def _is_raster_download_url(url: str) -> bool:
    lowered = str(url or "").strip().lower()
    if not lowered:
        return False
    if "/raw/download" in lowered:
        return True
    if any(marker in lowered for marker in ("/api/titiler/", "/api/dji-terra/", "/api/ortho-cog/")):
        return False
    path_only = lowered.split("?", 1)[0]
    return path_only.endswith((".tif", ".tiff"))


def bump_revision(project_id: str) -> int:
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO catalog_project_meta (project_id, catalog_revision, last_reconciled_at)
            VALUES (?, 1, '')
            ON CONFLICT(project_id) DO UPDATE SET catalog_revision = catalog_revision + 1
            """,
            (project_id,),
        )
        row = connection.execute(
            "SELECT catalog_revision FROM catalog_project_meta WHERE project_id = ?",
            (project_id,),
        ).fetchone()
        connection.commit()
    return int(row["catalog_revision"]) if row else 1


def get_revision(project_id: str) -> int:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT catalog_revision FROM catalog_project_meta WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["catalog_revision"]) if row else 0


def _get_asset_row(project_id: str, asset_id: str) -> dict[str, Any] | None:
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT id, project_id, asset_type, display_name, source_name, status, progress,
                   stage, error_message, primary_rel_path, paths_json, viewer_json, meta_json,
                   content_hash, created_at, updated_at
            FROM catalog_assets
            WHERE project_id = ? AND id = ?
            """,
            (project_id, asset_id),
        ).fetchone()
    return dict(row) if row else None


def _find_asset_by_rel_path(project_id: str, rel_path: str) -> dict[str, Any] | None:
    clean = rel_path.replace("\\", "/").lstrip("/")
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, asset_type, display_name, source_name, status, progress,
                   stage, error_message, primary_rel_path, paths_json, viewer_json, meta_json,
                   content_hash, created_at, updated_at
            FROM catalog_assets
            WHERE project_id = ?
            """,
            (project_id,),
        ).fetchall()
    for row in rows:
        asset = dict(row)
        paths = _json_loads(asset.get("paths_json"))
        candidates = {clean, asset.get("primary_rel_path", "")}
        for value in paths.values():
            if isinstance(value, str) and value:
                candidates.add(value.replace("\\", "/").lstrip("/"))
        if clean in candidates:
            return asset
        for value in candidates:
            if value and (clean == value or clean.startswith(value + "/") or value.startswith(clean + "/")):
                return asset
    return None


def upsert_asset(
    project_id: str,
    asset_id: str,
    *,
    asset_type: str = "",
    display_name: str = "",
    source_name: str = "",
    status: str = "Processing",
    progress: int = 0,
    stage: str = "",
    error_message: str = "",
    primary_rel_path: str = "",
    paths: dict[str, Any] | None = None,
    viewer: dict[str, Any] | None = None,
    meta: dict[str, Any] | None = None,
    content_hash: str = "",
) -> None:
    if not catalog_db_enabled() or not project_id or not asset_id:
        return
    now = _now_iso()
    existing = _get_asset_row(project_id, asset_id)
    merged_paths = _json_loads(existing.get("paths_json") if existing else {})
    merged_paths.update(paths or {})
    merged_viewer = _json_loads(existing.get("viewer_json") if existing else {})
    merged_viewer.update(viewer or {})
    merged_meta = _json_loads(existing.get("meta_json") if existing else {})
    merged_meta.update(meta or {})
    normalized_status = _normalize_status(status)
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO catalog_assets (
                id, project_id, asset_type, display_name, source_name, status, progress,
                stage, error_message, primary_rel_path, paths_json, viewer_json, meta_json,
                content_hash, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, id) DO UPDATE SET
                asset_type = excluded.asset_type,
                display_name = excluded.display_name,
                source_name = excluded.source_name,
                status = excluded.status,
                progress = excluded.progress,
                stage = excluded.stage,
                error_message = excluded.error_message,
                primary_rel_path = CASE
                    WHEN excluded.primary_rel_path <> '' THEN excluded.primary_rel_path
                    ELSE catalog_assets.primary_rel_path
                END,
                paths_json = excluded.paths_json,
                viewer_json = excluded.viewer_json,
                meta_json = excluded.meta_json,
                content_hash = CASE
                    WHEN excluded.content_hash <> '' THEN excluded.content_hash
                    ELSE catalog_assets.content_hash
                END,
                updated_at = excluded.updated_at
            """,
            (
                asset_id,
                project_id,
                asset_type or (existing or {}).get("asset_type", ""),
                display_name or (existing or {}).get("display_name", asset_id),
                source_name or (existing or {}).get("source_name", ""),
                normalized_status,
                int(progress),
                stage,
                error_message,
                primary_rel_path or (existing or {}).get("primary_rel_path", ""),
                _json_dumps(merged_paths),
                _json_dumps(merged_viewer),
                _json_dumps(merged_meta),
                content_hash or (existing or {}).get("content_hash", ""),
                (existing or {}).get("created_at") or now,
                now,
            ),
        )
        connection.commit()
    bump_revision(project_id)


def mirror_processing_job(project_id: str, job: dict[str, str]) -> None:
    if not catalog_db_enabled():
        return
    asset_id = str(job.get("job_id") or "").strip()
    if not asset_id:
        return
    status = str(job.get("status") or "Processing")
    paths: dict[str, str] = {}
    for key in ("raw_rel_path", "tiles_rel_path", "tileset_rel_path", "cog_rel_path", "vector_rel_path", "model_rel_path"):
        value = str(job.get(key) or "").strip()
        if value:
            paths[key.replace("_rel_path", "").replace("_path", "")] = value
    for key in ("raw", "cog", "copc", "ept", "tiles", "vector", "model"):
        value = str(job.get(key) or "").strip()
        if value:
            paths[key] = value
    viewer: dict[str, str] = {}
    for key in ("result_url", "viewer_type", "layer_url", "viewer_url", "tileset_url"):
        value = str(job.get(key) or "").strip()
        if value:
            viewer[key] = value
    meta: dict[str, str] = {}
    for key in (
        "height_offset",
        "dataset_type",
        "month",
        "converter",
        "rescale_min",
        "rescale_max",
        "bounds_wgs84",
        "progress_percent",
        "eta_seconds",
        "size_bytes",
        "processed_size",
        "processed_size_bytes",
    ):
        if key in job:
            meta[key] = str(job.get(key) or "")
    primary = (
        str(job.get("cog_rel_path") or "").strip()
        or str(job.get("raw_rel_path") or "").strip()
        or str(paths.get("copc") or "").strip()
        or str(paths.get("ept") or "").strip()
        or str(paths.get("tiles") or "").strip()
    )
    upsert_asset(
        project_id,
        asset_id,
        asset_type=str(job.get("kind") or job.get("dataset_type") or "dataset"),
        display_name=str(job.get("file_name") or asset_id),
        source_name=str(job.get("file_name") or asset_id),
        status=status,
        progress=int(str(job.get("progress_percent") or "0") or "0"),
        stage=str(job.get("stage") or ""),
        error_message=str(job.get("error") or ""),
        primary_rel_path=primary,
        paths=paths,
        viewer=viewer,
        meta=meta,
        content_hash=str(job.get("content_hash") or ""),
    )


def mirror_dataset_status(project_id: str, dataset_id: str, status_payload: dict[str, str]) -> None:
    if not catalog_db_enabled():
        return
    paths: dict[str, str] = {}
    for key in ("raw_rel_path", "tiles_rel_path", "tileset_rel_path", "cog_rel_path", "vector_rel_path", "model_rel_path"):
        value = str(status_payload.get(key) or "").strip()
        if value:
            paths[key.replace("_rel_path", "").replace("_path", "")] = value
    tile_folder = str(status_payload.get("tile_folder") or "").strip()
    if tile_folder:
        paths["tile_folder"] = tile_folder
    viewer: dict[str, str] = {}
    for key in ("layer_url", "viewer_url", "tileset_url", "result_url", "viewer_type"):
        value = str(status_payload.get(key) or "").strip()
        if value:
            viewer[key] = value
    meta = {
        key: str(status_payload.get(key) or "")
        for key in (
            "month",
            "upload_date",
            "date",
            "height_offset",
            "processed_size",
            "processed_size_bytes",
            "size_bytes",
            "rescale_min",
            "rescale_max",
            "bounds_wgs84",
            "source_crs",
            "detected_epsg",
            "manual_epsg",
            "applied_epsg",
            "progress_percent",
            "eta_seconds",
            "converter",
        )
        if status_payload.get(key) is not None
    }
    primary = (
        str(status_payload.get("cog_rel_path") or "").strip()
        or str(status_payload.get("tiles_rel_path") or "").strip()
        or str(status_payload.get("tileset_rel_path") or "").strip()
        or str(status_payload.get("vector_rel_path") or "").strip()
        or str(status_payload.get("model_rel_path") or "").strip()
        or str(status_payload.get("raw_rel_path") or "").strip()
    )
    upsert_asset(
        project_id,
        dataset_id,
        asset_type=str(status_payload.get("dataset_type") or "dataset"),
        display_name=str(status_payload.get("dataset_name") or status_payload.get("display_name") or dataset_id),
        source_name=str(status_payload.get("source_name") or status_payload.get("dataset_name") or dataset_id),
        status=str(status_payload.get("status") or "Processing"),
        progress=int(str(status_payload.get("progress_percent") or "0") or "0"),
        stage=str(status_payload.get("stage") or ""),
        error_message=str(status_payload.get("error") or ""),
        primary_rel_path=primary,
        paths=paths,
        viewer=viewer,
        meta=meta,
    )


def mirror_file_row(project_id: str, row: dict[str, str]) -> None:
    if not catalog_db_enabled():
        return
    asset_id = str(row.get("dataset_id") or row.get("canonical_key") or "").strip()
    if not asset_id:
        rel = str(row.get("rel_path") or row.get("raw_rel_path") or "").strip()
        asset_id = Path(rel).stem if rel else ""
    if not asset_id:
        return
    paths = {
        key: str(row.get(key) or "")
        for key in ("raw_rel_path", "rel_path", "cog_rel_path", "source_rel_path")
        if row.get(key)
    }
    viewer = {
        key: str(row.get(key) or "")
        for key in ("layer_url", "viewer_url", "file_url", "download_url", "tileset_url")
        if row.get(key)
    }
    meta = {
        key: str(row.get(key) or "")
        for key in (
            "month",
            "upload_date",
            "processed_size",
            "height_offset",
            "rescale_min",
            "rescale_max",
            "bounds_wgs84",
            "source_crs",
            "detected_epsg",
            "manual_epsg",
            "applied_epsg",
            "progress_percent",
            "eta_seconds",
            "converter",
            "viewer_type",
            "layer_type",
            "size_bytes",
        )
        if row.get(key) is not None
    }
    upsert_asset(
        project_id,
        asset_id,
        asset_type=str(row.get("dataset_type") or row.get("type") or row.get("kind") or "dataset"),
        display_name=str(row.get("display_name") or row.get("name") or asset_id),
        source_name=str(row.get("source_name") or row.get("name") or asset_id),
        status=str(row.get("status") or row.get("asset_status") or "WEB-READY"),
        progress=int(str(row.get("progress_percent") or "0") or "0"),
        stage=str(row.get("stage") or ""),
        primary_rel_path=str(row.get("rel_path") or row.get("raw_rel_path") or row.get("cog_rel_path") or ""),
        paths=paths,
        viewer=viewer,
        meta=meta,
    )


def remove_asset_db(project_id: str, asset_id: str) -> None:
    if not catalog_db_enabled():
        return
    with get_db_connection() as connection:
        connection.execute(
            "DELETE FROM catalog_assets WHERE project_id = ? AND id = ?",
            (project_id, asset_id),
        )
        connection.commit()
    bump_revision(project_id)


def _safe_remove_path(local_data_path: str, rel_or_abs: str) -> int:
    if not rel_or_abs:
        return 0
    local_root = Path(local_data_path).resolve()
    candidate = Path(rel_or_abs)
    target = candidate if candidate.is_absolute() else (local_root / rel_or_abs.replace("\\", "/").lstrip("/"))
    target = target.resolve()
    if target == local_root or not str(target).startswith(str(local_root)):
        return 0
    if not target.exists():
        return 0
    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
        return 1
    target.unlink(missing_ok=True)
    return 1


def _remove_upload_cache_entry(local_data_path: str, project_id: str, content_hash: str) -> None:
    if not content_hash:
        return
    cache_path = Path(local_data_path) / "pointclouds" / "_upload_cache.json"
    if not cache_path.is_file():
        return
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(data, dict):
        return
    suffix = f":{project_id}:{content_hash}"
    keys = [key for key in data if str(key).endswith(suffix)]
    if not keys:
        return
    for key in keys:
        data.pop(key, None)
    try:
        cache_path.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
    except OSError:
        pass


def _normalize_match_key(value: str) -> str:
    text = re.sub(r"[^a-z0-9]+", "", str(value or "").strip().lower())
    return text


def find_assets_by_key(project_id: str, dataset_key: str) -> list[dict[str, Any]]:
    clean = str(dataset_key or "").strip()
    if not clean:
        return []
    normalized = _normalize_match_key(Path(clean).stem or clean)
    matches: list[dict[str, Any]] = []
    for asset in list_assets(project_id):
        candidates = {
            str(asset.get("id") or ""),
            str(asset.get("display_name") or ""),
            str(asset.get("source_name") or ""),
            Path(str(asset.get("primary_rel_path") or "")).name,
            Path(str(asset.get("primary_rel_path") or "")).stem,
        }
        normalized_candidates = {_normalize_match_key(candidate) for candidate in candidates if candidate}
        if clean in candidates or normalized in normalized_candidates:
            matches.append(asset)
            continue
        if normalized and any(normalized in candidate or candidate in normalized for candidate in normalized_candidates if candidate):
            matches.append(asset)
    return matches


def delete_assets_by_key(
    project_id: str,
    dataset_key: str,
    *,
    local_data_path: str,
) -> int:
    removed = 0
    seen: set[str] = set()
    for asset in find_assets_by_key(project_id, dataset_key):
        asset_id = str(asset.get("id") or "")
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        removed += delete_asset_artifacts(project_id, asset_id, local_data_path=local_data_path)
    if not seen:
        removed += delete_asset_artifacts(project_id, dataset_key, local_data_path=local_data_path)
    return removed


def delete_asset_artifacts(
    project_id: str,
    asset_id: str,
    *,
    local_data_path: str,
) -> int:
    if not asset_id:
        return 0
    asset = _get_asset_row(project_id, asset_id)
    if not asset:
        return 0
    removed = 0
    paths = _json_loads(asset.get("paths_json"))
    for value in paths.values():
        if isinstance(value, str) and value:
            removed += _safe_remove_path(local_data_path, value)
    tile_folder = str(paths.get("tile_folder") or "").strip()
    if tile_folder:
        for rel in (
            f"projects/{project_id}/processed/{tile_folder}",
            f"projects/{project_id}/processed/ortho/{tile_folder}",
            f"projects/{project_id}/processed/dtm/{tile_folder}",
            f"projects/{project_id}/processed/dsm/{tile_folder}",
            f"projects/{project_id}/processed/pointclouds/{tile_folder}",
            f"projects/{project_id}/processed/vectors/{tile_folder}",
            f"projects/{project_id}/processed/models/{tile_folder}",
        ):
            removed += _safe_remove_path(local_data_path, rel)
    primary = str(asset.get("primary_rel_path") or "").strip()
    if primary:
        removed += _safe_remove_path(local_data_path, primary)
        parent = Path(primary).parent.as_posix()
        if parent:
            removed += _safe_remove_path(local_data_path, parent)
            compat = f"{parent}__ept_viewer"
            removed += _safe_remove_path(local_data_path, compat)
    for rel in (
        f"projects/{project_id}/exports/pointclouds/{asset_id}",
        f"projects/{project_id}/exports/pointclouds/{asset_id}__ept_viewer",
        f"projects/{project_id}/processed/pointclouds/{asset_id}",
        f"projects/{project_id}/processed/pointclouds/{asset_id}__ept_viewer",
    ):
        removed += _safe_remove_path(local_data_path, rel)
    display_name = str(asset.get("display_name") or asset.get("source_name") or "").strip()
    if display_name:
        stem = Path(display_name).stem
        for rel in (
            f"projects/{project_id}/raw/{project_id}__{display_name}",
            f"projects/{project_id}/raw/{display_name}",
            f"projects/{project_id}/exports/pointclouds/{stem}",
            f"projects/{project_id}/exports/pointclouds/{stem}__ept_viewer",
        ):
            removed += _safe_remove_path(local_data_path, rel)
    dataset_jobs_dir = Path(local_data_path) / "projects" / project_id / "_dataset_jobs" / asset_id
    if dataset_jobs_dir.exists():
        shutil.rmtree(dataset_jobs_dir, ignore_errors=True)
        removed += 1
    content_hash = str(asset.get("content_hash") or "").strip()
    _remove_upload_cache_entry(local_data_path, project_id, content_hash)
    with get_db_connection() as connection:
        if tile_folder:
            connection.execute(
                "DELETE FROM dataset_crop_masks WHERE project_id = ? AND tile_folder = ?",
                (project_id, tile_folder),
            )
        connection.execute(
            "DELETE FROM catalog_assets WHERE project_id = ? AND id = ?",
            (project_id, asset_id),
        )
        connection.commit()
    bump_revision(project_id)
    return removed


def delete_asset_by_rel_path(
    project_id: str,
    rel_path: str,
    *,
    local_data_path: str,
) -> int:
    asset = _find_asset_by_rel_path(project_id, rel_path)
    if not asset:
        return 0
    return delete_asset_artifacts(project_id, str(asset["id"]), local_data_path=local_data_path)


def list_assets(project_id: str) -> list[dict[str, Any]]:
    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT id, project_id, asset_type, display_name, source_name, status, progress,
                   stage, error_message, primary_rel_path, paths_json, viewer_json, meta_json,
                   content_hash, created_at, updated_at
            FROM catalog_assets
            WHERE project_id = ?
            ORDER BY updated_at DESC
            """,
            (project_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def asset_count(project_id: str) -> int:
    with get_db_connection() as connection:
        row = connection.execute(
            "SELECT COUNT(*) AS count FROM catalog_assets WHERE project_id = ?",
            (project_id,),
        ).fetchone()
    return int(row["count"]) if row else 0


def _parse_iso_timestamp(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            return datetime.fromisoformat(text.replace("Z", "+00:00"))
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _asset_path_candidates(asset: dict[str, Any]) -> list[Path]:
    paths = _json_loads(asset.get("paths_json"))
    candidates: list[str] = []
    primary = str(asset.get("primary_rel_path") or "").strip()
    if primary:
        candidates.append(primary)
    for value in paths.values():
        if isinstance(value, str) and value.strip():
            candidates.append(value.strip())
    unique: list[Path] = []
    seen: set[str] = set()
    for rel in candidates:
        normalized = rel.replace("\\", "/").lstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(Path(normalized))
    return unique


def _local_data_path(local_data_path: str, rel: str) -> Path:
    return (Path(local_data_path).resolve() / str(rel or "").replace("\\", "/").lstrip("/")).resolve()


def _path_is_publishable_file(path: Path) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _dir_has_publishable_marker(path: Path, markers: tuple[str, ...]) -> bool:
    try:
        if _path_is_publishable_file(path):
            return True
        if not path.is_dir():
            return False
        for marker in markers:
            candidate = path / marker
            if _path_is_publishable_file(candidate):
                return True
        for child in path.iterdir():
            if not child.is_file():
                continue
            lowered = child.name.lower()
            if lowered.endswith((".copc.laz", ".las", ".laz", ".tif", ".tiff", ".json")):
                if child.stat().st_size > 0:
                    return True
    except OSError:
        return False
    return False


def _asset_has_disk_evidence(asset: dict[str, Any], local_data_path: str) -> bool:
    local_root = Path(local_data_path).resolve()
    for rel in _asset_path_candidates(asset):
        try:
            target = (local_root / rel).resolve()
            if target.exists():
                return True
        except OSError:
            continue
    return False


def _asset_has_publishable_artifact(asset: dict[str, Any], local_data_path: str) -> bool:
    if not local_data_path:
        return _asset_has_disk_evidence(asset, local_data_path)
    project_id = str(asset.get("project_id") or "").strip()
    asset_id = str(asset.get("id") or "").strip()
    asset_type = str(asset.get("asset_type") or "").lower().replace(" ", "")
    paths = _json_loads(asset.get("paths_json"))
    rel_candidates: list[str] = []
    primary = str(asset.get("primary_rel_path") or "").strip()
    if primary:
        rel_candidates.append(primary)
    for value in paths.values():
        if isinstance(value, str) and value.strip():
            rel_candidates.append(value.strip())
    if asset_id:
        rel_candidates.extend(
            [
                f"projects/{project_id}/processed/pointclouds/{asset_id}/output.copc.laz",
                f"projects/{project_id}/exports/pointclouds/{asset_id}/output.copc.laz",
                f"projects/{project_id}/processed/pointclouds/{asset_id}/ept.json",
            ]
        )
    seen: set[str] = set()
    for rel in rel_candidates:
        normalized = rel.replace("\\", "/").lstrip("/")
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        target = _local_data_path(local_data_path, normalized)
        lowered = normalized.lower()
        if "point" in asset_type or lowered.endswith((".copc.laz", ".las", ".laz")) or paths.get("copc") or paths.get("ept"):
            if lowered.endswith((".copc.laz", ".las", ".laz")) and _path_is_publishable_file(target):
                return True
            if lowered.endswith("ept.json") and _path_is_publishable_file(target):
                return True
            if _dir_has_publishable_marker(target, ("output.copc.laz", "ept.json")):
                return True
            continue
        if asset_type in {"ortho", "dtm", "dsm", "dataset", "cog"} or paths.get("cog") or lowered.endswith((".tif", ".tiff")):
            if lowered.endswith((".tif", ".tiff")) and _path_is_publishable_file(target):
                return True
            if _dir_has_publishable_marker(target, ("tileset.json",)):
                return True
            continue
        if _path_is_publishable_file(target) or _dir_has_publishable_marker(target, ("tileset.json", "output.copc.laz", "ept.json")):
            return True
    return False


def prune_missing_assets(project_id: str, local_data_path: str) -> int:
    if not catalog_db_enabled():
        return 0
    removed = 0
    now = datetime.now(timezone.utc)
    for asset in list_assets(project_id):
        asset_id = str(asset.get("id") or "").strip()
        if not asset_id:
            continue
        status = str(asset.get("status") or "").lower()
        has_paths = _asset_has_disk_evidence(asset, local_data_path)
        has_artifact = _asset_has_publishable_artifact(asset, local_data_path)
        if status in _ACTIVE_STATUSES:
            if has_paths or has_artifact:
                continue
            updated_at = _parse_iso_timestamp(str(asset.get("updated_at") or ""))
            if updated_at is None:
                continue
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            age_seconds = (now - updated_at.astimezone(timezone.utc)).total_seconds()
            if age_seconds < 6 * 3600:
                continue
        elif _is_ready_status(status):
            if has_artifact:
                continue
        else:
            if has_paths or has_artifact:
                continue
        remove_asset_db(project_id, asset_id)
        removed += 1
    if removed:
        bump_revision(project_id)
    return removed


def _build_pointcloud_viewer_url(
    *,
    project_id: str,
    asset_id: str,
    display_name: str,
    paths: dict[str, Any],
    primary_rel_path: str,
    viewer: dict[str, Any],
) -> str:
    existing = str(
        viewer.get("layer_url")
        or viewer.get("viewer_url")
        or viewer.get("result_url")
        or viewer.get("tileset_url")
        or ""
    ).strip()
    if existing and "/droid-ept-viewer/" in existing:
        return existing
    copc_rel = str(paths.get("copc") or primary_rel_path or "").strip().replace("\\", "/").lstrip("/")
    if copc_rel.lower().endswith(".copc.laz"):
        copc_api = copc_rel if copc_rel.startswith("api/") else f"api/data/{copc_rel}"
    elif copc_rel:
        copc_api = f"api/data/{copc_rel.rstrip('/')}/output.copc.laz"
    else:
        copc_api = f"api/data/projects/{project_id}/exports/pointclouds/{asset_id}/output.copc.laz"
    if not copc_api.startswith("/"):
        copc_api = f"/{copc_api}"
    params = urlencode(
        {
            "copc": copc_api,
            "project": project_id,
            "dataset": asset_id or display_name,
            "name": display_name,
        },
        quote_via=quote,
    )
    return f"/droid-ept-viewer/index.html?{params}"


def _asset_to_file_row(asset: dict[str, Any], base_url: str, project_id: str) -> dict[str, str]:
    paths = _json_loads(asset.get("paths_json"))
    viewer = _json_loads(asset.get("viewer_json"))
    meta = _json_loads(asset.get("meta_json"))
    rel_path = str(asset.get("primary_rel_path") or paths.get("cog") or paths.get("copc") or paths.get("ept") or paths.get("raw") or "").strip()
    cog_rel_path = str(paths.get("cog") or meta.get("cog_rel_path") or "").strip()
    display_name = str(asset.get("display_name") or asset.get("id") or Path(rel_path).name)
    asset_type = str(asset.get("asset_type") or meta.get("dataset_type") or "dataset")
    type_fields = _catalog_asset_type_fields(asset_type, meta)
    local_root = os.getenv("LOCAL_DATA_PATH", "")
    cog_path = str(meta.get("cog_path") or "").strip()
    if not cog_path and local_root and cog_rel_path:
        candidate = (Path(local_root) / cog_rel_path).resolve()
        if candidate.is_file():
            cog_path = str(candidate)
    rescale_min = str(meta.get("rescale_min") or "")
    rescale_max = str(meta.get("rescale_max") or "")
    layer_url = str(
        viewer.get("layer_url")
        or viewer.get("viewer_url")
        or viewer.get("result_url")
        or viewer.get("tileset_url")
        or ""
    )
    if type_fields["dataset_type"] in {"ortho", "dtm", "dsm"} and cog_path:
        layer_url = _raster_tile_url_template(
            base_url,
            cog_path,
            type_fields["layer_type"],
            rescale_min,
            rescale_max,
        )
    elif _is_raster_download_url(layer_url) and cog_path:
        layer_url = _raster_tile_url_template(
            base_url,
            cog_path,
            type_fields["layer_type"],
            rescale_min,
            rescale_max,
        )
    elif type_fields["dataset_type"] == "pointcloud" or str(asset_type).lower() == "pointcloud":
        layer_url = _build_pointcloud_viewer_url(
            project_id=project_id,
            asset_id=str(asset.get("id") or ""),
            display_name=display_name,
            paths=paths,
            primary_rel_path=rel_path,
            viewer=viewer,
        )
    file_url = str(viewer.get("file_url") or "")
    download_url = str(viewer.get("download_url") or file_url)
    if not file_url and rel_path:
        file_url = f"{base_url.rstrip('/')}/data/{rel_path}"
    if not download_url:
        download_url = file_url
    file_path = str((Path(local_root) / rel_path).resolve()) if rel_path and local_root else ""
    size_bytes = str(meta.get("size_bytes") or "0")
    processed_size = str(meta.get("processed_size") or "")
    if local_root:
        size_candidates = [
            rel_path,
            str(paths.get("copc") or ""),
            str(paths.get("raw") or ""),
            str(paths.get("ept") or ""),
        ]
        if int(size_bytes or "0") <= 0:
            for candidate in size_candidates:
                candidate = str(candidate or "").strip()
                if not candidate:
                    continue
                resolved = (Path(local_root) / candidate).resolve()
                resolved_size = _path_size_bytes(resolved)
                if resolved_size > 0:
                    size_bytes = str(resolved_size)
                    break
        if not processed_size and int(size_bytes or "0") > 0:
            processed_size = _format_size_bytes(int(size_bytes))
    return {
        "dataset_id": str(asset.get("id") or ""),
        "name": display_name,
        "display_name": display_name,
        "kind": type_fields["kind"],
        "type": type_fields["type"],
        "dataset_type": type_fields["dataset_type"],
        "layer_type": type_fields["layer_type"],
        "month": str(meta.get("month") or ""),
        "processed_size": processed_size,
        "upload_date": str(meta.get("upload_date") or meta.get("date") or asset.get("created_at") or ""),
        "height_offset": str(meta.get("height_offset") or ""),
        "cog_path": cog_path,
        "cog_rel_path": cog_rel_path,
        "rescale_min": str(meta.get("rescale_min") or ""),
        "rescale_max": str(meta.get("rescale_max") or ""),
        "bounds_wgs84": str(meta.get("bounds_wgs84") or ""),
        "source_crs": str(meta.get("source_crs") or ""),
        "detected_epsg": str(meta.get("detected_epsg") or ""),
        "manual_epsg": str(meta.get("manual_epsg") or ""),
        "applied_epsg": str(meta.get("applied_epsg") or ""),
        "size_bytes": size_bytes,
        "status": _normalize_status(str(asset.get("status") or "")),
        "asset_status": _normalize_status(str(asset.get("status") or "")),
        "updated_at": str(asset.get("updated_at") or ""),
        "stage": str(asset.get("stage") or ""),
        "progress_percent": str(asset.get("progress") or meta.get("progress_percent") or ""),
        "eta_seconds": str(meta.get("eta_seconds") or ""),
        "file_url": file_url,
        "download_url": download_url,
        "layer_url": layer_url,
        "viewer_url": layer_url,
        "copc_url": str(paths.get("copc") or viewer.get("copc_url") or ""),
        "ept_url": str(paths.get("ept") or viewer.get("ept_url") or ""),
        "file_path": file_path,
        "rel_path": rel_path,
        "raw_rel_path": str(paths.get("raw") or ""),
        "source_rel_path": str(paths.get("raw") or rel_path),
        "viewer_type": str(viewer.get("viewer_type") or meta.get("viewer_type") or ""),
        "canonical_key": str(asset.get("id") or rel_path),
    }


def _asset_to_job_row(asset: dict[str, Any]) -> dict[str, str]:
    meta = _json_loads(asset.get("meta_json"))
    viewer = _json_loads(asset.get("viewer_json"))
    error = str(asset.get("error_message") or "")
    stage = str(asset.get("stage") or "")
    if not stage and error:
        stage = error[:240]
    return {
        "job_id": str(asset.get("id") or ""),
        "kind": str(asset.get("asset_type") or "dataset"),
        "file_name": str(asset.get("display_name") or asset.get("id") or ""),
        "status": str(asset.get("status") or "Processing"),
        "updated_at": str(asset.get("updated_at") or ""),
        "stage": stage,
        "progress_percent": str(asset.get("progress") or meta.get("progress_percent") or ""),
        "eta_seconds": str(meta.get("eta_seconds") or ""),
        "error": error,
        "result_url": str(viewer.get("result_url") or viewer.get("viewer_url") or ""),
        "viewer_type": str(viewer.get("viewer_type") or meta.get("viewer_type") or ""),
        "dataset_type": str(asset.get("asset_type") or ""),
    }


def list_file_rows(project_id: str, base_url: str, *, local_data_path: str = "") -> list[dict[str, str]]:
    if local_data_path:
        prune_missing_assets(project_id, local_data_path)
    rows: list[dict[str, str]] = []
    visible_non_ready = {
        "raw",
        "uploaded",
        "uploading",
        "processing",
        "queued",
        "running",
        "pending",
        "failed",
        "error",
    }
    for asset in list_assets(project_id):
        status = str(asset.get("status") or "")
        lowered = status.lower()
        if not _is_ready_status(status) and lowered not in visible_non_ready:
            continue
        if local_data_path and _is_ready_status(status) and not _asset_has_publishable_artifact(asset, local_data_path):
            asset_id = str(asset.get("id") or "").strip()
            if asset_id:
                remove_asset_db(project_id, asset_id)
            continue
        rows.append(_asset_to_file_row(asset, base_url, project_id))
    return rows


def list_job_rows(project_id: str, *, local_data_path: str = "") -> list[dict[str, str]]:
    if local_data_path:
        prune_missing_assets(project_id, local_data_path)
    jobs: list[dict[str, str]] = []
    for asset in list_assets(project_id):
        status = str(asset.get("status") or "").lower()
        if _is_ready_status(status):
            continue
        if status in _ACTIVE_STATUSES or status:
            jobs.append(_asset_to_job_row(asset))
    return jobs


def reconcile_from_file_rows(
    project_id: str,
    rows: list[dict[str, str]],
    *,
    local_data_path: str = "",
) -> dict[str, int]:
    inserted = 0
    updated = 0
    removed = 0
    seen: set[str] = set()
    for row in rows:
        asset_id = str(row.get("dataset_id") or row.get("canonical_key") or "").strip()
        if not asset_id:
            rel = str(row.get("rel_path") or "").strip()
            asset_id = Path(rel).stem if rel else ""
        if not asset_id or asset_id in seen:
            continue
        seen.add(asset_id)
        existing = _get_asset_row(project_id, asset_id)
        mirror_file_row(project_id, row)
        if existing:
            updated += 1
        else:
            inserted += 1
    if local_data_path:
        for asset in list_assets(project_id):
            asset_id = str(asset.get("id") or "").strip()
            if not asset_id or asset_id in seen:
                continue
            status = str(asset.get("status") or "").lower()
            if status in _ACTIVE_STATUSES:
                continue
            if _asset_has_disk_evidence(asset, local_data_path):
                continue
            remove_asset_db(project_id, asset_id)
            removed += 1
    now = _now_iso()
    with get_db_connection() as connection:
        connection.execute(
            """
            INSERT INTO catalog_project_meta (project_id, catalog_revision, last_reconciled_at)
            VALUES (?, 0, ?)
            ON CONFLICT(project_id) DO UPDATE SET last_reconciled_at = excluded.last_reconciled_at
            """,
            (project_id, now),
        )
        connection.commit()
    bump_revision(project_id)
    return {"inserted": inserted, "updated": updated, "removed": removed, "total": len(seen)}
