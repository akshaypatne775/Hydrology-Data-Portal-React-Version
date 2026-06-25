import ast
import os
import shutil

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

# Define the mapping of module -> list of function/class names
SERVICES = {
    # Phase 4
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
    ],
    # Phase 5
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
        "update_dataset_status", "_write_dataset_manifest", "_read_dataset_manifest",
        "delete_dataset_artifacts", "delete_dataset_by_name", "rename_dataset_artifacts",
        "_read_project_manifest", "_write_project_manifest"
    ],
    "services/upload_service.py": [
        "_upload_session_dir", "_dataset_upload_session_dir", "_ensure_disk_space_for_bytes",
        "_merge_upload_chunks"
    ],
    "services/analysis_service.py": [
        "_sample_raster", "_interpolate_profile_points", "_profile_summary", 
        "_volume_for_raster", "_dtm_volume_between", "_circle_points", "_pixel_area_m2",
        "sample_cross_section"
    ],
    "services/grid_export_service.py": [
        "_csv_grid_generator", "_dxf_grid_generator", "_generate_grid_export_background"
    ],
    "services/spatial_feature_service.py": [
        "_ensure_spatial_layer", "_insert_spatial_feature", "_spatial_row_to_dict", 
        "_normalize_spatial_feature_geojson", "_can_manage_spatial_feature"
    ],
    "services/activity_service.py": [
        "log_activity"
    ],
    "services/bulk_import_service.py": [
        "_browse_server_folder", "_bulk_scan_files", "_admin_manual_bulk_import_background", 
        "_prepare_admin_manual_bulk_import", "_queue_admin_manual_bulk_import"
    ]
}

lines_to_remove = set()
extracted_code = {k: [] for k in SERVICES.keys()}

# Map function names to their target module
func_to_module = {}
for mod, funcs in SERVICES.items():
    for f in funcs:
        func_to_module[f] = mod

# Find nodes and extract them with preceding comments
for node in tree.body:
    name = getattr(node, "name", None)
    if name in func_to_module:
        mod = func_to_module[name]
        
        start_line = node.lineno - 1
        if hasattr(node, "decorator_list") and node.decorator_list:
            start_line = min(d.lineno - 1 for d in node.decorator_list)
            
        # Include preceding comments
        while start_line > 0 and source_lines[start_line - 1].strip().startswith("#"):
            start_line -= 1
            
        end_line = node.end_lineno
        
        # Extract lines
        code_block = "\n".join(source_lines[start_line:end_line])
        extracted_code[mod].append(code_block)
        
        # Mark for removal
        for i in range(start_line, end_line):
            lines_to_remove.add(i)

# Create the new files
for mod, blocks in extracted_code.items():
    if not blocks:
        continue
    
    file_path = f"app/{mod}"
    os.makedirs(os.path.dirname(file_path), exist_ok=True)
    
    with open(file_path, "w", encoding="utf-8") as f:
        # We will manually add imports to these files later
        f.write("\n\n".join(blocks) + "\n")
    print(f"Created {file_path} with {len(blocks)} functions")

# Write the pruned main.py
new_main_lines = [line for i, line in enumerate(source_lines) if i not in lines_to_remove]
with open(source_path, "w", encoding="utf-8") as f:
    f.write("\n".join(new_main_lines) + "\n")

print(f"Removed {len(lines_to_remove)} lines from main.py")
