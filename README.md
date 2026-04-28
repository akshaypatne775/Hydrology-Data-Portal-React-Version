# Droid Cloud Hydrology Data Portal

Professional project-based hydrology and geospatial portal built with React + FastAPI.  
It combines 2D map analysis, 3D globe point-cloud visualization, authentication, and project isolation in one scalable workspace.

## Project Description

This platform helps teams manage hydrology workflows per project:

- Create user accounts and securely sign in
- Create and manage project workspaces
- Visualize project data in 2D (Leaflet) and 3D (Cesium)
- Upload very large LAS/LAZ point clouds in chunks
- Convert LAS/LAZ to 3D Tiles in background jobs
- Track status, reuse cached conversions, and avoid duplicate processing

The architecture is modular and production-oriented (`components`, `hooks`, `services`, `pages`, `context`, `utils`) with centralized API handling and lazy-loaded heavy modules.

## Features

- Project-based dashboard with user-specific project visibility
- Secure auth (signup, login, logout, session cookies)
- 2D map workspace with hydrology layers and issue overlays
- 3D globe workspace with point cloud upload and rendering
- Chunked upload + background processing for large LAS/LAZ files
- Point-cloud processing status polling and conversion error reporting
- Conversion caching (same file content is reused)
- Recharts-based hydrology visualizations (line, bar, summary cards)
- Professional branded UI (Droid Cloud)
- Environment-driven configuration for API URLs and security settings

## Tech Stack

### Frontend

- React 19 + TypeScript
- Vite
- Leaflet + React Leaflet
- Cesium
- Recharts
- Turf.js
- ESLint

### Backend

- FastAPI
- Uvicorn
- SQLite
- py3dtiles (`py3dtiles[las]`)
- laspy
- Pillow

## Installation Steps

## 1) Clone repository

```bash
git clone <your-repo-url>
cd "Hydrology Data Portal React Version"
```

## 2) Frontend setup

```bash
cd frontend
npm install
```

Create local env file from example:

```bash
copy .env.example .env.local
```

or on PowerShell:

```powershell
Copy-Item .env.example .env.local
```

## 3) Backend setup

```bash
cd ../backend
python -m venv venv
venv\Scripts\activate
pip install -r requirements.txt
```

Create backend env file from example:

```powershell
Copy-Item .env.example .env
```

Set a strong secret in `backend/.env`:

```env
SESSION_SIGNING_SECRET=your-long-random-secret
```

## Usage Guide

## Run backend

From `backend/`:

```bash
venv\Scripts\activate
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Backend endpoints:

- API base: `http://localhost:8000/api`
- Static tiles/media: `http://localhost:8000/tiles`
- Health: `http://localhost:8000/health`

## Run frontend

From `frontend/`:

```bash
npm run dev
```

Default frontend URL:

- `http://localhost:5173`

## Build for production

Frontend:

```bash
cd frontend
npm run build
```

Backend (example production command):

```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## Typical workflow

1. Sign up / log in
2. Create or select a project
3. Open **Map View** for 2D hydrology analysis
4. Open **Globe View** to upload LAS/LAZ point clouds
5. Wait for background conversion and status polling
6. Review loaded 3D tileset inside Cesium globe

## Environment Variables

## Frontend (`frontend/.env.local`)

- `VITE_API_BASE_URL` - FastAPI origin (example: `http://localhost:8000`)
- `VITE_S3_TILE_BASE_URL` - Base tile path (local or cloud)
- `VITE_FLOOD_TILE_BASE_URL` - Flood tiles base path
- `VITE_S3_ORTHO_PREFIX` - Ortho tile folder
- `VITE_S3_DEM_PREFIX` - DEM tile folder
- `VITE_S3_DTM_PREFIX` - DTM tile folder
- `VITE_MAP_DEFAULT_LAT`
- `VITE_MAP_DEFAULT_LNG`
- `VITE_MAP_DEFAULT_ZOOM`
- `VITE_CESIUM_ION_TOKEN`

## Backend (`backend/.env`)

- `LOCAL_DATA_PATH` - Local storage root (uploads, tiles, media, db)
- `UPLOAD_DISK_HEADROOM_MB` - Required free-space headroom for large uploads
- `POINTCLOUD_SRS_IN` - Optional forced source CRS
- `POINTCLOUD_SRS_OUT` - Output CRS for point cloud conversion
- `SESSION_TTL_SECONDS` - Session lifetime
- `SESSION_SIGNING_SECRET` - Cookie signing secret (required in production)
- `FRONTEND_ORIGINS` - Comma-separated CORS origins

## Screenshots

Add screenshots in a folder like `docs/screenshots/` and update links below.

### 1. Login / Signup

![Login Screen](docs/screenshots/login.png)

### 2. Project Dashboard

![Project Dashboard](docs/screenshots/dashboard.png)

### 3. Map View (Hydrology Analysis)

![Map View](docs/screenshots/map-view.png)

### 4. Globe View (Point Cloud)

![Globe View](docs/screenshots/globe-view.png)

### 5. Point Cloud Upload + Processing Status

![Point Cloud Upload](docs/screenshots/pointcloud-upload.png)

## Notes

- Do not commit secrets (`.env`, tokens, credentials) to git.
- Use `.env.example` files as templates for onboarding.
- Keep generated build artifacts (`frontend/dist`) out of version control when possible.
