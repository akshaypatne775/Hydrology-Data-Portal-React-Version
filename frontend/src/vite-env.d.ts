/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Preferred root URL for XYZ tiles (local FastAPI, CDN, etc.) */
  readonly VITE_TILE_BASE_URL?: string
  /** Legacy alias for VITE_TILE_BASE_URL (same semantics — not AWS-specific). */
  readonly VITE_S3_TILE_BASE_URL?: string
  /** Bump to invalidate browser cache after regenerating local tiles (optional; default "1" in code). */
  readonly VITE_TILE_CACHE_BUST?: string
  /** Path segment under base for orthomosaic tiles (default: tiles/orthomosaic) */
  readonly VITE_S3_ORTHO_PREFIX?: string
  readonly VITE_S3_DEM_PREFIX?: string
  readonly VITE_S3_DTM_PREFIX?: string
  readonly VITE_S3_FLOOD_1IN25_PREFIX?: string
  readonly VITE_S3_FLOOD_1IN50_PREFIX?: string
  readonly VITE_S3_FLOOD_1IN100_PREFIX?: string
  /** File extension for tiles (default: png) */
  readonly VITE_S3_TILE_EXT?: string
  readonly VITE_MAP_DEFAULT_LAT?: string
  readonly VITE_MAP_DEFAULT_LNG?: string
  readonly VITE_MAP_DEFAULT_ZOOM?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
