import ast
import json

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

funcs = []
for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        funcs.append(node.name)

# Grouping logic
groups = {
    "raster/colormaps": ["_build_dji_terra_colormap", "_agisoft_dtm_lut", "_elevation_to_agisoft_rgba"],
    "raster/bounds": ["_read_cog_bounds_wgs84", "_xyz_bounds_from_tiles_dir", "_tile_y_to_lat"],
    "raster/srs": ["_detect_input_srs", "_detect_epsg_from_file", "_normalize_epsg_input"],
    "raster/readers": [
        "_render_dji_terra_dem_png", "_render_dji_terra_tile", "_render_ortho_cog_png", "_render_ortho_cog_tile", 
        "_transparent_png_tile", "_parse_rescale_pair", "_edge_connected_padding_mask", "_save_png_tile",
        "_read_compact_ortho_tile", "_read_compact_dem_tile", "_sample_raster_percentiles", "_compute_tile_hillshade"
    ],
    "raster/tiler": [
        "_run_gdal2tiles_subprocess", "process_tif_to_tiles", "process_dataset_background",
        "_run_compact_rasterio_tiler", "_compact_tile_tasks", "_choose_compact_zoom", "_zoom_for_raster_resolution"
    ],
    "core/utils": [
        "_safe_pointcloud_basename", "_safe_tif_basename", "_safe_dataset_upload_basename", "_normalize_month",
        "get_dir_size", "calculate_folder_size", "_format_size_bytes", "_fast_tile_dir_size", "_now_iso",
        "_write_portal_error_log", "_file_fingerprint", "_safe_spatial_id", "_safe_export_stem", "_safe_tile_folder_name",
        "_safe_project_id", "_safe_dataset_id", "_safe_ept_folder_name", "_safe_tileset_id"
    ],
    "core/paths": [
        "_secure_local_cog_path", "_dataset_type_folder", "_project_processed_root", "_project_exports_root",
        "_project_pointcloud_root", "_legacy_project_pointcloud_root", "_ept_dataset_dir", "_legacy_ept_dataset_dir",
        "_legacy_ept_pointcloud_dataset_dir", "_dataset_dir", "_dataset_status_file", "_dataset_manifest_name",
        "_manifest_target_for", "_processing_jobs_file", "_analysis_cache_dir", "_dataset_source_path",
        "_grid_export_raster_path", "_grid_export_output_path", "_pointcloud_slice_exports_root", "_pointcloud_raw_candidates",
        "_resolve_pointcloud_slice_source", "_resolve_dataset_tiles_dir"
    ],
    "core/cache": [
        "_read_cache", "_write_cache", "_cache_path", "_invalidate_project_files_cache", "_get_cached_project_files",
        "_set_cached_project_files", "_conversion_cache_file", "_read_conversion_cache", "_write_conversion_cache"
    ],
    "services/ept_catalog": [
        "_primary_copc_dir_for_ept_folder", "_should_skip_ept_listing_for_native_copc", "_ept_dataset_name",
        "_ept_asset_quality", "_ept_asset_candidates", "_best_ept_asset", "_ept_json_url", "_copc_url",
        "_copc_viewer_url", "_ept_viewer_url", "_pointcloud_viewer_url", "_looks_like_cesium_tileset_json",
        "_find_tileset_json", "_ensure_tileset_alias", "_contains_pointcloud_viewer_asset", "_project_copc_assets",
        "_is_3d_model_dataset", "_candidate_processed_tile_dirs", "_candidate_processed_cog_files",
        "_candidate_processed_model_dirs", "_display_model_folder_name", "_safe_extract_zip", "_find_extracted_tileset_root"
    ],
    "services/jobs": [
        "_read_processing_jobs", "_write_processing_jobs", "_upsert_processing_job", "_remove_project_processing_jobs",
        "_remove_processing_job"
    ],
    "services/crop": [
        "_ring_score", "_normalize_crop_points", "_extract_kml_points", "_save_crop_mask", "_get_crop_mask"
    ],
    "services/dataset_helpers": [
        "_titiler_tile_url_template", "_is_valid_tile_dataset", "_read_raster_manual_metadata", "_project_dataset_statuses",
        "_dataset_status_by_id", "_grid_coordinate_range", "_grid_sample_value", "_write_grid_export_metadata",
        "_generate_grid_export_file", "_require_rasterio", "_read_volume_csv", "_admin_dataset_status_by_key",
        "_safe_remove_dataset_path", "_safe_rename_dataset_path", "_dataset_status_matches_rel", "_find_dataset_status_for_rel",
        "_dataset_extra_response_fields", "_canonical_file_row", "_ensure_project_file_access", "_safe_project_file_response_path",
        "_copc_range_response", "_serve_project_data_file", "_serve_pointcloud_data_file", "_secure_dataset_file",
        "_dedupe_pointcloud_file_rows", "_purge_catalog_dataset", "_read_text_marker"
    ],
    "services/session": [
        "_normalize_hidden_tabs", "_set_session_cookie", "_clear_session_cookie", "_clear_session_auth_cache", "_picker_filter_for_kind"
    ],
    "core/lifespan": [
        "_backfill_processed_sizes"
    ]
}

# invert mapping
func_to_group = {}
for g, fns in groups.items():
    for f in fns:
        func_to_group[f] = g

unmapped = []
for f in funcs:
    if f not in func_to_group:
        unmapped.append(f)

print("Unmapped:", unmapped)
