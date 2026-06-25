import ast
import os
from pathlib import Path
import shutil

groups = {
    "raster": [
        "_build_dji_terra_colormap", "_agisoft_dtm_lut", "_elevation_to_agisoft_rgba",
        "_read_cog_bounds_wgs84", "_xyz_bounds_from_tiles_dir", "_tile_y_to_lat",
        "_detect_input_srs", "_detect_epsg_from_file", "_normalize_epsg_input",
        "_render_dji_terra_dem_png", "_render_dji_terra_tile", "_render_ortho_cog_png", "_render_ortho_cog_tile", 
        "_transparent_png_tile", "_parse_rescale_pair", "_edge_connected_padding_mask", "_save_png_tile",
        "_read_compact_ortho_tile", "_read_compact_dem_tile", "_sample_raster_percentiles", "_compute_tile_hillshade",
        "_run_gdal2tiles_subprocess", "process_tif_to_tiles", "process_dataset_background",
        "_run_compact_rasterio_tiler", "_compact_tile_tasks", "_choose_compact_zoom", "_zoom_for_raster_resolution"
    ],
    "utils": [
        "_safe_pointcloud_basename", "_safe_tif_basename", "_safe_dataset_upload_basename", "_normalize_month",
        "get_dir_size", "calculate_folder_size", "_format_size_bytes", "_fast_tile_dir_size", "_now_iso",
        "_write_portal_error_log", "_file_fingerprint", "_safe_spatial_id", "_safe_export_stem", "_safe_tile_folder_name",
        "_safe_project_id", "_safe_dataset_id", "_safe_ept_folder_name", "_safe_tileset_id",
        "_normalize_hidden_tabs", "_set_session_cookie", "_clear_session_cookie", "_clear_session_auth_cache", "_picker_filter_for_kind"
    ],
    "paths": [
        "_secure_local_cog_path", "_dataset_type_folder", "_project_processed_root", "_project_exports_root",
        "_project_pointcloud_root", "_legacy_project_pointcloud_root", "_ept_dataset_dir", "_legacy_ept_dataset_dir",
        "_legacy_ept_pointcloud_dataset_dir", "_dataset_dir", "_dataset_status_file", "_dataset_manifest_name",
        "_manifest_target_for", "_processing_jobs_file", "_analysis_cache_dir", "_dataset_source_path",
        "_grid_export_raster_path", "_grid_export_output_path", "_pointcloud_slice_exports_root", "_pointcloud_raw_candidates",
        "_resolve_pointcloud_slice_source", "_resolve_dataset_tiles_dir",
        "_read_cache", "_write_cache", "_cache_path", "_invalidate_project_files_cache", "_get_cached_project_files",
        "_set_cached_project_files", "_conversion_cache_file", "_read_conversion_cache", "_write_conversion_cache",
        "_primary_copc_dir_for_ept_folder", "_should_skip_ept_listing_for_native_copc", "_ept_dataset_name",
        "_ept_asset_quality", "_ept_asset_candidates", "_best_ept_asset", "_ept_json_url", "_copc_url",
        "_copc_viewer_url", "_ept_viewer_url", "_pointcloud_viewer_url", "_looks_like_cesium_tileset_json",
        "_find_tileset_json", "_ensure_tileset_alias", "_contains_pointcloud_viewer_asset", "_project_copc_assets",
        "_is_3d_model_dataset", "_candidate_processed_tile_dirs", "_candidate_processed_cog_files",
        "_candidate_processed_model_dirs", "_display_model_folder_name", "_safe_extract_zip", "_find_extracted_tileset_root",
        "_read_processing_jobs", "_write_processing_jobs", "_upsert_processing_job", "_remove_project_processing_jobs",
        "_remove_processing_job",
        "_ring_score", "_normalize_crop_points", "_extract_kml_points", "_save_crop_mask", "_get_crop_mask",
        "_titiler_tile_url_template", "_is_valid_tile_dataset", "_read_raster_manual_metadata", "_project_dataset_statuses",
        "_dataset_status_by_id", "_grid_coordinate_range", "_grid_sample_value", "_write_grid_export_metadata",
        "_generate_grid_export_file", "_require_rasterio", "_read_volume_csv", "_admin_dataset_status_by_key",
        "_safe_remove_dataset_path", "_safe_rename_dataset_path", "_dataset_status_matches_rel", "_find_dataset_status_for_rel",
        "_dataset_extra_response_fields", "_canonical_file_row", "_ensure_project_file_access", "_safe_project_file_response_path",
        "_copc_range_response", "_serve_project_data_file", "_serve_pointcloud_data_file", "_secure_dataset_file",
        "_dedupe_pointcloud_file_rows", "_purge_catalog_dataset", "_read_text_marker",
        "_backfill_processed_sizes"
    ]
}

func_to_bucket = {}
for bucket, funcs in groups.items():
    for f in funcs:
        func_to_bucket[f] = bucket

def extract_nodes():
    source_path = "app/main.py"
    with open(source_path, "r", encoding="utf-8") as f:
        source_code = f.read()
    source_lines = source_code.splitlines()
    tree = ast.parse(source_code)

    extracted = {"raster": [], "utils": [], "paths": []}
    nodes_to_remove = []

    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            bucket = func_to_bucket.get(node.name)
            if bucket:
                start_line = node.lineno - 1
                
                # Capture decorators
                if node.decorator_list:
                    start_line = node.decorator_list[0].lineno - 1

                end_line = node.end_lineno
                
                # Expand up to capture comments directly above the function
                while start_line > 0 and source_lines[start_line-1].strip().startswith("#"):
                    start_line -= 1
                    
                code_chunk = "\n".join(source_lines[start_line:end_line])
                extracted[bucket].append(code_chunk)
                nodes_to_remove.append((start_line, end_line))
    
    # Remove nodes from main.py in reverse order
    for start, end in sorted(nodes_to_remove, key=lambda x: x[0], reverse=True):
        del source_lines[start:end]
        
    return source_lines, extracted

def main():
    source_lines, extracted = extract_nodes()
    
    # Save extracted code
    for bucket in ["raster", "utils", "paths"]:
        code = "\n\n".join(extracted[bucket])
        if bucket == "raster":
            # Append to existing raster.py
            with open("app/services/raster.py", "a", encoding="utf-8") as f:
                f.write("\n\n" + code + "\n")
        else:
            # Create new files
            imports = "import os\nimport sys\nimport time\nimport math\nimport json\nimport uuid\nimport shutil\nimport struct\nimport base64\nimport hashlib\nimport asyncio\nimport logging\nimport subprocess\nfrom pathlib import Path\nfrom datetime import datetime, timezone\nimport numpy as np\nfrom fastapi import HTTPException, Response\nfrom fastapi.responses import FileResponse, JSONResponse, StreamingResponse\nfrom app.core.config import *\nfrom app.core.database import *\nfrom app.models.datasets import *\n"
            with open(f"app/core/{bucket}.py", "w", encoding="utf-8") as f:
                f.write(imports + "\n\n" + code + "\n")
                
    # Insert new imports at the top of main.py
    import_statements = """
from app.services.raster import *
from app.core.utils import *
from app.core.paths import *
"""
    
    # Find a good place to inject the imports in main.py
    for i, line in enumerate(source_lines):
        if "from app.services.bulk_import_service" in line:
            source_lines.insert(i + 1, import_statements)
            break
            
    with open("app/main.py", "w", encoding="utf-8") as f:
        f.write("\n".join(source_lines))
        
    # Inject imports into all router files and service files to prevent any NameError
    for root_dir in ["app/routers", "app/services", "app/core"]:
        for file in os.listdir(root_dir):
            if file.endswith(".py") and file != "__init__.py" and file not in ["utils.py", "paths.py"]:
                filepath = os.path.join(root_dir, file)
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                
                if "from app.core.paths import *" not in content:
                    # Inject right after app.core.config import
                    content = content.replace(
                        "from app.core.config import *", 
                        "from app.core.config import *\nfrom app.core.utils import *\nfrom app.core.paths import *\nfrom app.services.raster import *"
                    )
                    with open(filepath, "w", encoding="utf-8") as f:
                        f.write(content)
                        
    print("Extraction complete!")

if __name__ == "__main__":
    main()
