# Droid Survair COG Streaming Portal

This is a standalone Proof-of-Concept portal for testing Cloud Optimized GeoTIFFs (COGs), browser-based map streaming, DTM/DSM visualization, grid export, and Cesium 3D terrain generation.

## What This Portal Does

- Upload GeoTIFF files (`.tif` / `.tiff`)
- Convert uploaded rasters into Cloud Optimized GeoTIFFs
- Stream COG tiles on the map without pre-generating XYZ tile folders
- Support three data categories:
  - Orthomosaic
  - DTM
  - DSM
- Render DTM/DSM files with a DJI Terra-style elevation color ramp and hillshade
- Remove white/no-data background from orthomosaic tiles where possible
- Reuse already converted COGs instead of converting the same file again
- Bulk process folders named `Ortho`, `DTM`, or `DSM`
- Export DTM/DSM grid points as:
  - CSV
  - DXF
- Generate a Cesium 3D terrain model from DTM/DSM COGs

## Folder Structure

```text
.
├── main.py
├── terrain_3d.py
├── requirements.txt
├── install.bat
├── run.bat
├── templates/
│   └── index.html
├── static/
│   └── style.css
├── uploads/
├── cogs/
└── terrain3d/
```

## Setup

Run:

```bat
install.bat
```

This creates the virtual environment, installs dependencies, and creates the required folders.

## Start Portal

Run:

```bat
run.bat
```

Then open:

```text
http://localhost:8000
```

## Bulk Folder Input

For bulk processing, select a parent folder like:

```text
data/
  Ortho/
    ortho_file.tif
  DTM/
    dtm_file.tif
  DSM/
    dsm_file.tif
```

Folder names are case-insensitive.

## Notes

- Large orthomosaics and DTM/DSM files can take time to convert.
- 3D terrain generation is available only for DTM/DSM layers.
- The generated COGs are stored in `cogs/`.
- Generated Cesium 3D terrain outputs are stored in `terrain3d/`.
