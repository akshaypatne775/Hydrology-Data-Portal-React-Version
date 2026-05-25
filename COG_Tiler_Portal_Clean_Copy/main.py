from pathlib import Path
from time import perf_counter
from uuid import uuid4

import aiofiles
import base64
import csv
import hashlib
import io
import json
import logging
import numpy as np
import ezdxf
from PIL import Image
from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import HTMLResponse
from fastapi.responses import Response
from fastapi.responses import StreamingResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import rasterio
from rasterio.enums import Resampling
from rasterio.warp import transform_bounds
from rio_tiler.colormap import cmap
from rio_tiler.io import Reader
from rio_cogeo.cogeo import cog_translate
from rio_cogeo.profiles import cog_profiles
from starlette.concurrency import run_in_threadpool
from titiler.core.factory import TilerFactory
from terrain_3d import Terrain3DOptions, generate_terrain_3d


BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
COG_DIR = BASE_DIR / "cogs"
TERRAIN3D_DIR = BASE_DIR / "terrain3d"
logger = logging.getLogger("cog-tiler-poc")
TRANSPARENT_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
CATEGORIES = {"ortho", "dtm", "dsm"}
AGISOFT_DEM_CMAP = None


def build_dji_terra_colormap() -> dict[int, tuple[int, int, int, int]]:
    stops = [
        (0.00, (0, 0, 130)),
        (0.25, (0, 255, 255)),
        (0.50, (0, 255, 0)),
        (0.75, (255, 255, 0)),
        (1.00, (139, 0, 0)),
    ]
    color_map: dict[int, tuple[int, int, int, int]] = {}

    for index in range(256):
        position = index / 255

        for stop_index in range(len(stops) - 1):
            left_pos, left_color = stops[stop_index]
            right_pos, right_color = stops[stop_index + 1]

            if left_pos <= position <= right_pos:
                ratio = (position - left_pos) / (right_pos - left_pos)
                rgb = tuple(
                    int(round(left_color[channel] + ratio * (right_color[channel] - left_color[channel])))
                    for channel in range(3)
                )
                color_map[index] = (*rgb, 255)
                break

    return color_map


AGISOFT_DEM_CMAP = build_dji_terra_colormap()
cmap.register({"agisoft_dem": AGISOFT_DEM_CMAP})

UPLOAD_DIR.mkdir(exist_ok=True)
COG_DIR.mkdir(exist_ok=True)
TERRAIN3D_DIR.mkdir(exist_ok=True)

app = FastAPI(title="COG Tiler PoC")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
app.mount("/terrain3d", StaticFiles(directory=str(TERRAIN3D_DIR)), name="terrain3d")


def raster_path(url: str = Query(..., description="Local COG path or URI")) -> str:
    return url


cog = TilerFactory(path_dependency=raster_path)
app.include_router(cog.router, prefix="/tiles", tags=["COG Tiles"])


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, "index.html")


@app.get("/favicon.ico")
async def favicon():
    return Response(status_code=204)


def is_readable_cog(cog_path: Path) -> tuple[bool, str | None]:
    try:
        with rasterio.open(cog_path) as dataset:
            if not dataset.crs:
                return False, "COG has no CRS."
            if dataset.width <= 0 or dataset.height <= 0:
                return False, "COG has invalid raster dimensions."
            dataset.bounds
        return True, None
    except Exception as exc:
        return False, str(exc)


def metadata_path(cog_path: Path) -> Path:
    return cog_path.with_suffix(f"{cog_path.suffix}.json")


def read_cog_metadata(cog_path: Path) -> dict:
    path = metadata_path(cog_path)

    if not path.exists():
        return {}

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def infer_category_from_name(filename: str) -> str:
    lower_name = filename.lower()

    if "dtm" in lower_name or "dem" in lower_name:
        return "dtm"
    if "dsm" in lower_name:
        return "dsm"

    return "ortho"


def infer_category_from_relative_path(relative_path: str) -> str | None:
    category_names = {
        "ortho": "ortho",
        "orthos": "ortho",
        "orthomosaic": "ortho",
        "orthomosaics": "ortho",
        "dtm": "dtm",
        "dtms": "dtm",
        "dem": "dtm",
        "dems": "dtm",
        "dsm": "dsm",
        "dsms": "dsm",
    }

    normalized = relative_path.replace("\\", "/")

    for part in normalized.split("/"):
        category = category_names.get(part.strip().lower())
        if category:
            return category

    return None


def write_cog_metadata(cog_path: Path, metadata: dict) -> None:
    metadata_path(cog_path).write_text(json.dumps(metadata, indent=2), encoding="utf-8")


def safe_slug(value: str) -> str:
    slug = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in value)
    return slug.strip("_") or "terrain"


def terrain3d_slug(filename: str) -> str:
    return safe_slug(Path(filename).stem)


def terrain3d_output_dir(filename: str) -> Path:
    return TERRAIN3D_DIR / terrain3d_slug(filename)


def terrain3d_info(filename: str) -> dict | None:
    slug = terrain3d_slug(filename)
    output_dir = TERRAIN3D_DIR / slug
    tileset_path = output_dir / "tileset.json"
    viewer_path = output_dir / "viewer_3d.html"
    metadata_path_3d = output_dir / "terrain3d.json"

    if not tileset_path.exists() or not viewer_path.exists():
        return None

    metadata = {}

    if metadata_path_3d.exists():
        try:
            metadata = json.loads(metadata_path_3d.read_text(encoding="utf-8"))
        except Exception:
            metadata = {}

    return {
        "slug": slug,
        "viewer_url": f"/terrain3d/{slug}/viewer_3d.html",
        "tileset_url": f"/terrain3d/{slug}/tileset.json",
        "metadata": metadata,
    }


def build_cog_destination_profile(category: str) -> dict:
    if category == "ortho":
        profile = dict(cog_profiles.get("jpeg"))
        profile.update(
            {
                "compress": "JPEG",
                "quality": 85,
                "photometric": "YCbCr",
                "bigtiff": "IF_SAFER",
                "num_threads": "ALL_CPUS",
            }
        )
        return profile

    if category in {"dtm", "dsm"}:
        profile = dict(cog_profiles.get("deflate"))
        profile.update(
            {
                "compress": "LERC_ZSTD",
                "max_z_error": 0.001,
                "bigtiff": "IF_SAFER",
                "num_threads": "ALL_CPUS",
            }
        )
        return profile

    profile = dict(cog_profiles.get("deflate"))
    profile.update({"bigtiff": "IF_SAFER", "num_threads": "ALL_CPUS"})
    return profile


def calculate_percentile_rescale(cog_path: Path) -> dict[str, float] | None:
    with rasterio.open(cog_path) as dataset:
        if dataset.count < 1:
            return None

        max_preview_size = 1024
        scale = max(dataset.width / max_preview_size, dataset.height / max_preview_size, 1)
        out_width = max(1, int(dataset.width / scale))
        out_height = max(1, int(dataset.height / scale))
        data = dataset.read(
            1,
            out_shape=(out_height, out_width),
            masked=True,
            resampling=Resampling.bilinear,
        )

    values = data.compressed() if np.ma.isMaskedArray(data) else data.reshape(-1)
    values = values[np.isfinite(values)]

    if values.size == 0:
        return None

    low, high = np.percentile(values, [5, 95])

    if float(low) == float(high):
        high = low + 1

    return {
        "min": round(float(low), 3),
        "max": round(float(high), 3),
    }


def cog_info(cog_path: Path) -> dict:
    is_valid, error = is_readable_cog(cog_path)
    stat = cog_path.stat()
    metadata = read_cog_metadata(cog_path)
    category = metadata.get("category") or infer_category_from_name(cog_path.name)
    rescale = metadata.get("rescale")

    if category in {"dtm", "dsm"} and not rescale and is_valid:
        try:
            rescale = calculate_percentile_rescale(cog_path)
            metadata.update(
                {
                    "category": category,
                    "rescale": rescale,
                    "message": "Rescale metadata calculated from existing COG.",
                }
            )
            write_cog_metadata(cog_path, metadata)
        except Exception as exc:
            logger.warning("Could not calculate rescale metadata for %s: %s", cog_path.name, exc)

    return {
        "filename": cog_path.name,
        "path": str(cog_path.resolve()),
        "uri": cog_path.resolve().as_uri(),
        "size_mb": round(stat.st_size / (1024 * 1024), 2),
        "modified": stat.st_mtime,
        "category": category if category in CATEGORIES else "ortho",
        "rescale": rescale,
        "terrain3d": terrain3d_info(cog_path.name),
        "message": metadata.get("message"),
        "valid": is_valid,
        "error": error,
    }


@app.get("/cogs")
async def list_cogs():
    cogs = [cog_info(path) for path in COG_DIR.glob("*.tif")]
    cogs.extend(cog_info(path) for path in COG_DIR.glob("*.tiff"))
    cogs.sort(key=lambda item: item["modified"], reverse=True)
    return {"cogs": cogs}


@app.post("/upload-and-convert")
async def upload_and_convert(file: UploadFile = File(...), category: str = Form("ortho")):
    request_started = perf_counter()
    original_name = Path(file.filename or "").name
    suffix = Path(original_name).suffix.lower()
    category = category.lower()

    if suffix not in {".tif", ".tiff"}:
        raise HTTPException(status_code=400, detail="Please upload a .tif or .tiff file.")

    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Please choose Orthomosaic, DTM, or DSM.")

    stem = Path(original_name).stem or "raster"
    safe_stem = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in stem)
    unique_name = f"{safe_stem}_{uuid4().hex[:8]}{suffix}"
    upload_path = UPLOAD_DIR / unique_name
    file_hash = hashlib.sha256()
    cog_path: Path | None = None

    try:
        async with aiofiles.open(upload_path, "wb") as out_file:
            while chunk := await file.read(1024 * 1024):
                file_hash.update(chunk)
                await out_file.write(chunk)

        upload_seconds = perf_counter() - request_started
        digest = file_hash.hexdigest()[:12]
        cog_filename = f"{safe_stem}_{digest}_cog.tif"
        cog_path = COG_DIR / cog_filename

        if cog_path.exists():
            is_valid, validation_error = is_readable_cog(cog_path)
            if is_valid:
                rescale = None
                if category in {"dtm", "dsm"}:
                    metadata = read_cog_metadata(cog_path)
                    rescale = metadata.get("rescale") or calculate_percentile_rescale(cog_path)

                write_cog_metadata(
                    cog_path,
                    {
                        "category": category,
                        "source_name": original_name,
                        "rescale": rescale,
                        "message": "Existing valid COG found. Using it without reconversion.",
                    },
                )
                total_seconds = perf_counter() - request_started
                upload_path.unlink(missing_ok=True)
                return {
                    "filename": cog_filename,
                    "path": str(cog_path.resolve()),
                    "uri": cog_path.resolve().as_uri(),
                    "category": category,
                    "rescale": rescale,
                    "upload_seconds": round(upload_seconds, 2),
                    "conversion_seconds": 0,
                    "total_seconds": round(total_seconds, 2),
                    "reused": True,
                    "message": "Existing valid COG found. Using it without reconversion.",
                }

            logger.warning(
                "Existing COG %s is not valid/readable and will be reconverted: %s",
                cog_path.name,
                validation_error,
            )
            cog_path.unlink(missing_ok=True)
            metadata_path(cog_path).unlink(missing_ok=True)

        dst_profile = build_cog_destination_profile(category)
        conversion_started = perf_counter()
        await run_in_threadpool(
            cog_translate,
            str(upload_path),
            str(cog_path),
            dst_profile,
            in_memory=False,
            quiet=True,
        )
        conversion_seconds = perf_counter() - conversion_started
        total_seconds = perf_counter() - request_started
        rescale = calculate_percentile_rescale(cog_path) if category in {"dtm", "dsm"} else None
        write_cog_metadata(
            cog_path,
            {
                "category": category,
                "source_name": original_name,
                "rescale": rescale,
                "message": "Converted uploaded TIFF to a new COG.",
            },
        )
        logger.info(
            "Converted %s to %s in %.2fs, total request %.2fs",
            upload_path.name,
            cog_path.name,
            conversion_seconds,
            total_seconds,
        )
    except Exception as exc:
        if upload_path.exists():
            upload_path.unlink(missing_ok=True)
        if cog_path and cog_path.exists():
            cog_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=f"COG conversion failed: {exc}") from exc
    finally:
        await file.close()

    return {
        "filename": cog_filename,
        "path": str(cog_path.resolve()),
        "uri": cog_path.resolve().as_uri(),
        "category": category,
        "rescale": rescale,
        "upload_seconds": round(upload_seconds, 2),
        "conversion_seconds": round(conversion_seconds, 2),
        "total_seconds": round(total_seconds, 2),
        "reused": False,
        "message": "Converted uploaded TIFF to a new COG.",
    }


@app.post("/bulk-upload-and-convert")
async def bulk_upload_and_convert(files: list[UploadFile] = File(...)):
    if not files:
        raise HTTPException(status_code=400, detail="No files were selected.")

    results = []
    summary = {
        "total": len(files),
        "converted": 0,
        "reused": 0,
        "skipped": 0,
        "failed": 0,
    }

    for file in files:
        relative_path = file.filename or ""
        suffix = Path(relative_path).suffix.lower()

        if suffix not in {".tif", ".tiff"}:
            summary["skipped"] += 1
            results.append(
                {
                    "source": relative_path,
                    "status": "skipped",
                    "message": "Only .tif and .tiff files are processed.",
                }
            )
            await file.close()
            continue

        category = infer_category_from_relative_path(relative_path)

        if not category:
            summary["skipped"] += 1
            results.append(
                {
                    "source": relative_path,
                    "status": "skipped",
                    "message": "Folder category not found. Use Ortho, DTM, or DSM folder names.",
                }
            )
            await file.close()
            continue

        try:
            output = await upload_and_convert(file=file, category=category)
            status = "reused" if output.get("reused") else "converted"
            summary[status] += 1
            results.append(
                {
                    "source": relative_path,
                    "status": status,
                    **output,
                }
            )
        except HTTPException as exc:
            summary["failed"] += 1
            results.append(
                {
                    "source": relative_path,
                    "status": "failed",
                    "message": exc.detail,
                }
            )
        except Exception as exc:
            summary["failed"] += 1
            results.append(
                {
                    "source": relative_path,
                    "status": "failed",
                    "message": str(exc),
                }
            )

    return {
        "summary": summary,
        "results": results,
    }


@app.get("/cog-bounds/{filename}")
async def cog_bounds(filename: str):
    cog_path = (COG_DIR / Path(filename).name).resolve()

    if not cog_path.exists() or not cog_path.is_file():
        raise HTTPException(status_code=404, detail="COG not found.")

    try:
        with rasterio.open(cog_path) as dataset:
            bounds = dataset.bounds
            if dataset.crs:
                west, south, east, north = transform_bounds(
                    dataset.crs,
                    "EPSG:4326",
                    bounds.left,
                    bounds.bottom,
                    bounds.right,
                    bounds.top,
                    densify_pts=21,
                )
            else:
                west, south, east, north = bounds.left, bounds.bottom, bounds.right, bounds.top
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read COG bounds: {exc}") from exc

    return {
        "bounds": [west, south, east, north],
    }


def get_cog_path_or_404(filename: str) -> Path:
    cog_path = (COG_DIR / Path(filename).name).resolve()

    if not cog_path.exists() or not cog_path.is_file():
        raise HTTPException(status_code=404, detail="COG not found.")

    return cog_path


def coordinate_range(start: float, stop: float, step: float, descending: bool = False):
    if step <= 0:
        raise ValueError("Interval must be greater than 0.")

    value = start

    if descending:
        while value >= stop:
            yield value
            value -= step
    else:
        while value <= stop:
            yield value
            value += step


def extract_sample_value(sample, nodata):
    value = sample[0]

    if np.ma.is_masked(value):
        return None

    value = float(value)

    if not np.isfinite(value):
        return None

    if nodata is not None and np.isclose(value, float(nodata)):
        return None

    return value


def csv_grid_generator(cog_path: Path, interval: float):
    with rasterio.open(cog_path) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000

        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["X", "Y", "Z"])
        yield output.getvalue()

        for y in coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch = []

            for x in coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))

                if len(batch) >= batch_size:
                    output = io.StringIO()
                    writer = csv.writer(output)

                    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
                        z = extract_sample_value(sample, nodata)
                        if z is not None:
                            writer.writerow([coord[0], coord[1], z])

                    yield output.getvalue()
                    batch = []

            if batch:
                output = io.StringIO()
                writer = csv.writer(output)

                for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
                    z = extract_sample_value(sample, nodata)
                    if z is not None:
                        writer.writerow([coord[0], coord[1], z])

                yield output.getvalue()


def build_dxf_grid(cog_path: Path, interval: float) -> bytes:
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()

    with rasterio.open(cog_path) as dataset:
        bounds = dataset.bounds
        nodata = dataset.nodata
        batch_size = 5000

        for y in coordinate_range(bounds.top, bounds.bottom, interval, descending=True):
            batch = []

            for x in coordinate_range(bounds.left, bounds.right, interval):
                batch.append((x, y))

                if len(batch) >= batch_size:
                    for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
                        z = extract_sample_value(sample, nodata)
                        if z is not None:
                            msp.add_point((coord[0], coord[1], z))

                    batch = []

            if batch:
                for coord, sample in zip(batch, dataset.sample(batch, masked=True)):
                    z = extract_sample_value(sample, nodata)
                    if z is not None:
                        msp.add_point((coord[0], coord[1], z))

    output = io.StringIO()
    doc.write(output)
    return output.getvalue().encode("utf-8")


@app.get("/export-grid")
async def export_grid(
    filename: str = Query(...),
    interval: float = Query(..., gt=0),
    format: str = Query("csv"),
):
    cog_path = get_cog_path_or_404(filename)
    export_format = format.lower()

    if export_format not in {"csv", "dxf"}:
        raise HTTPException(status_code=400, detail="Grid export format must be csv or dxf.")

    output_name = f"{cog_path.stem}_grid_{str(interval).replace('.', 'p')}.{export_format}"

    if export_format == "csv":
        return StreamingResponse(
            csv_grid_generator(cog_path, interval),
            media_type="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
        )

    dxf_bytes = await run_in_threadpool(build_dxf_grid, cog_path, interval)
    return StreamingResponse(
        io.BytesIO(dxf_bytes),
        media_type="application/dxf",
        headers={"Content-Disposition": f'attachment; filename="{output_name}"'},
    )


@app.post("/generate-3d")
async def generate_3d(
    filename: str = Form(...),
    mesh_step: int = Form(4),
    mesh_grid: int = Form(1),
    vertical_exag: float = Form(1.0),
    force: bool = Form(False),
):
    cog_path = get_cog_path_or_404(filename)
    metadata = read_cog_metadata(cog_path)
    category = metadata.get("category") or infer_category_from_name(cog_path.name)

    if category not in {"dtm", "dsm"}:
        raise HTTPException(status_code=400, detail="3D terrain generation is available only for DTM or DSM COGs.")

    mesh_step = max(1, min(int(mesh_step), 64))
    mesh_grid = 1
    vertical_exag = max(0.1, min(float(vertical_exag), 20.0))
    output_dir = terrain3d_output_dir(cog_path.name)

    if not force:
        existing = terrain3d_info(cog_path.name)
        if existing:
            return {
                "filename": cog_path.name,
                "category": category,
                "reused": True,
                **existing,
            }

    started = perf_counter()

    try:
        await run_in_threadpool(
            generate_terrain_3d,
            cog_path,
            output_dir,
            options=Terrain3DOptions(
                step=mesh_step,
                tiles_x=mesh_grid,
                tiles_y=mesh_grid,
                vertical_exaggeration=vertical_exag,
            ),
            metadata={
                "id": terrain3d_slug(cog_path.name),
                "label": f"{category.upper()} 3D - {cog_path.stem}",
                "category": category,
                "source_cog": cog_path.name,
            },
        )
    except Exception as exc:
        logger.exception("3D terrain generation failed for %s", cog_path.name)
        raise HTTPException(status_code=500, detail=f"3D terrain generation failed: {exc}") from exc

    elapsed = perf_counter() - started
    info = terrain3d_info(cog_path.name)

    if not info:
        raise HTTPException(status_code=500, detail="3D terrain finished but output files were not found.")

    return {
        "filename": cog_path.name,
        "category": category,
        "reused": False,
        "seconds": round(elapsed, 2),
        **info,
    }


def parse_rescale(value: str | None) -> tuple[float, float] | None:
    if not value:
        return None

    parts = value.split(",")
    if len(parts) != 2:
        raise ValueError("Rescale must use min,max format.")

    low, high = float(parts[0]), float(parts[1])
    if low >= high:
        raise ValueError("Rescale min must be less than max.")

    return low, high


def render_dem_png(tile_array, rescale: tuple[float, float]) -> bytes:
    band = tile_array[0]
    values = np.ma.filled(band, np.nan).astype("float64")
    mask = np.ma.getmaskarray(band) | ~np.isfinite(values)
    low, high = rescale

    normalized = np.clip((values - low) / (high - low), 0, 1)
    color_indexes = np.nan_to_num(normalized * 255, nan=0).astype("uint8")

    lookup = np.array([AGISOFT_DEM_CMAP[index] for index in range(256)], dtype="uint8")
    rgba = lookup[color_indexes].astype("float64")

    valid_values = values[~mask]
    fill_value = float(np.nanmean(valid_values)) if valid_values.size else 0.0
    elevation = np.where(mask, fill_value, values)

    z_factor = 3.0
    dy, dx = np.gradient(elevation * z_factor)

    slope = np.arctan(np.sqrt((dx * dx) + (dy * dy)))
    aspect = np.arctan2(dy, -dx)

    azimuth = np.deg2rad(315.0)
    altitude = np.deg2rad(45.0)
    hillshade = (
        (np.sin(altitude) * np.cos(slope))
        + (np.cos(altitude) * np.sin(slope) * np.cos(azimuth - aspect))
    )
    hillshade = np.clip(np.nan_to_num(hillshade, nan=0.0, posinf=1.0, neginf=0.0), 0, 1)

    detail_shade = 0.32 + (0.68 * hillshade)
    rgba[..., :3] = np.clip(rgba[..., :3] * detail_shade[..., np.newaxis], 0, 255)
    rgba[mask, 3] = 0
    rgba = rgba.astype("uint8")

    image = Image.fromarray(rgba, mode="RGBA")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_ortho_png(tile_array) -> bytes:
    array = tile_array
    data = np.ma.filled(array, 0) if np.ma.isMaskedArray(array) else np.asarray(array)

    if data.shape[0] < 3:
        return None

    rgb = np.moveaxis(data[:3], 0, -1).astype("float64")

    if rgb.max(initial=0) <= 1:
        rgb = rgb * 255

    rgb = np.clip(rgb, 0, 255).astype("uint8")
    alpha = np.full(rgb.shape[:2], 255, dtype="uint8")

    if data.shape[0] >= 4:
        source_alpha = data[3].astype("float64")
        if source_alpha.max(initial=0) <= 1:
            source_alpha = source_alpha * 255
        alpha = np.clip(source_alpha, 0, 255).astype("uint8")

    if np.ma.isMaskedArray(array):
        mask = np.any(np.ma.getmaskarray(array[:3]), axis=0)
        alpha[mask] = 0

    white_background = np.all(rgb >= 248, axis=2)
    alpha[white_background] = 0

    rgba = np.dstack([rgb, alpha])
    image = Image.fromarray(rgba, mode="RGBA")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def render_local_cog_tile(
    cog_path: Path,
    z: int,
    x: int,
    y: int,
    colormap_name: str | None = None,
    rescale: tuple[float, float] | None = None,
) -> bytes:
    metadata = read_cog_metadata(cog_path)
    category = metadata.get("category") or infer_category_from_name(cog_path.name)

    if category in {"dtm", "dsm"}:
        colormap_name = colormap_name or "agisoft_dem"
        if rescale is None:
            metadata_rescale = metadata.get("rescale")
            if not metadata_rescale:
                metadata_rescale = calculate_percentile_rescale(cog_path)
                metadata.update({"category": category, "rescale": metadata_rescale})
                write_cog_metadata(cog_path, metadata)

            if metadata_rescale:
                rescale = (float(metadata_rescale["min"]), float(metadata_rescale["max"]))

    with Reader(str(cog_path)) as dataset:
        tile = dataset.tile(x, y, z, tilesize=256)

        if category in {"dtm", "dsm"} and rescale:
            return render_dem_png(tile.array, rescale)

        if category == "ortho":
            ortho_png = render_ortho_png(tile.array)
            if ortho_png:
                return ortho_png

        if rescale:
            processed_tile = tile.post_process(in_range=((rescale[0], rescale[1]),))
            if processed_tile is not None:
                tile = processed_tile

        render_options = {}
        if colormap_name:
            render_options["colormap"] = cmap.get(colormap_name)

        return tile.render(img_format="PNG", **render_options)


@app.get("/cog-tiles/{filename}/{z}/{x}/{y}.png")
async def cog_tile(
    filename: str,
    z: int,
    x: int,
    y: int,
    colormap_name: str | None = Query(default=None),
    rescale: str | None = Query(default=None),
):
    cog_path = (COG_DIR / Path(filename).name).resolve()

    if not cog_path.exists() or not cog_path.is_file():
        raise HTTPException(status_code=404, detail="COG not found.")

    try:
        parsed_rescale = parse_rescale(rescale)
        tile_bytes = await run_in_threadpool(
            render_local_cog_tile,
            cog_path,
            z,
            x,
            y,
            colormap_name,
            parsed_rescale,
        )
    except Exception as exc:
        message = str(exc).lower()
        expected_empty_tile = any(
            text in message
            for text in (
                "outside bounds",
                "outside image bounds",
                "does not overlap",
                "empty",
                "no data",
                "nodata",
            )
        )

        if expected_empty_tile:
            return Response(
                content=TRANSPARENT_PNG,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=3600"},
            )

        logger.exception("Could not render tile %s z=%s x=%s y=%s", filename, z, x, y)
        return Response(
            content=TRANSPARENT_PNG,
            media_type="image/png",
            headers={"Cache-Control": "no-store", "X-Tile-Error": str(exc)[:200]},
        )

    return Response(
        content=tile_bytes,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
