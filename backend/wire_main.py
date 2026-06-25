import os
import ast

SERVICES = {
    "app.services.pointcloud.ept_service": [
        "_prepare_las_for_ept", "_run_ept_converter_once", "process_pointcloud_ept_job",
        "_ept_error_needs_las_bbox_repair", "_repair_las_bounding_box",
        "_looks_like_lon_lat_bounds", "_utm_epsg_for_lon_lat"
    ],
    "app.services.pointcloud.copc_service": [
        "_run_copc_converter_once", "_process_copc_ept_compat_job",
        "_copc_ept_compat_dir", "_best_copc_asset", "_copc_asset_in_dir"
    ],
    "app.services.pointcloud.pointcloud_jobs": [
        "process_pointcloud", "process_pointcloud_background", "process_contours_background"
    ],
    "app.services.pointcloud.pointcloud_slice": [
        "_run_pointcloud_slice_export", "_rotation_matrix_xyz", "_finite_vector", "_point_record_value"
    ],
    "app.services.pointcloud.pdal_tools": [
        "_resolve_converter_executable", "_pdal_has_driver"
    ],
    "app.core.security": [
        "_hash_password", "_verify_password", "_sign_session_token", "_unsign_session_token", "_token_hash"
    ],
    "app.core.middleware": [
        "Debug404Middleware", "ActivityTrackingMiddleware", "ProtectedDataPathMiddleware"
    ],
    "app.dependencies": [
        "_require_user", "_get_optional_user", "_require_admin", "verify_admin", 
        "_require_upload_user", "_client_ip_for_limit", "_enforce_rate_limit"
    ],
    "app.services.auth_service": [
        "_create_pending_user", "_approval_url", "_send_owner_sms", "_send_email"
    ],
    "app.services.project_service": [
        "get_project_dirs", "get_project_dataset_type_dirs", "_delete_project_storage", 
        "_ensure_project_owner", "_is_admin_user_id"
    ],
    "app.services.dataset_service": [
        "_infer_dataset_type", "_normalize_dataset_type", "_raster_layer_type", 
        "update_dataset_status"
    ],
    "app.services.upload_service": [
        "_upload_session_dir", "_dataset_upload_session_dir", "_ensure_disk_space_for_bytes",
        "_merge_upload_chunks"
    ],
    "app.services.analysis_service": [
        "_sample_raster", "_interpolate_profile_points", "_profile_summary", 
        "_volume_for_raster", "_dtm_volume_between", "_circle_points", "_pixel_area_m2",
        "sample_cross_section"
    ],
    "app.services.grid_export_service": [
        "_csv_grid_generator", "_dxf_grid_generator", "_generate_grid_export_background"
    ],
    "app.services.spatial_feature_service": [
        "_ensure_spatial_layer", "_insert_spatial_feature", "_spatial_row_to_dict", 
        "_normalize_spatial_feature_geojson", "_can_manage_spatial_feature"
    ],
    "app.services.bulk_import_service": [
        "_browse_server_folder", "_bulk_scan_files", "_admin_manual_bulk_import_background", 
        "_prepare_admin_manual_bulk_import", "_queue_admin_manual_bulk_import"
    ]
}

# Generate import lines
import_lines = ["\n# --- EXTRACTED SERVICES ---"]
for module, funcs in SERVICES.items():
    if os.path.exists(module.replace(".", "/") + ".py"):
        import_lines.append(f"from {module} import ({', '.join(funcs)})")

# Inject into main.py
with open("app/main.py", "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

# find line after last import
insert_idx = 0
for i, line in enumerate(source_lines):
    if line.startswith("from app.utils.analysis_utils"):
        insert_idx = i + 1
        break

if insert_idx == 0:
    for i, line in enumerate(source_lines):
        if line.startswith("app = FastAPI("):
            insert_idx = i - 1
            break

source_lines = source_lines[:insert_idx] + import_lines + source_lines[insert_idx:]

with open("app/main.py", "w", encoding="utf-8") as f:
    f.write("\n".join(source_lines) + "\n")
