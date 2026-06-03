from __future__ import annotations

import json
import tempfile
import zipfile
from pathlib import Path
from typing import Any
import xml.etree.ElementTree as ET

from fastapi import HTTPException

try:
    import shapefile
except ImportError:  # pragma: no cover - surfaced by endpoint when dependency is missing
    shapefile = None

try:
    from rasterio.crs import CRS
    from rasterio.warp import transform as transform_coords
except ImportError:  # pragma: no cover - rasterio is already a backend dependency
    CRS = None
    transform_coords = None


STRUCTURE_COLORS = {
    "Residential": "#22c55e",
    "Commercial": "#ef4444",
    "Road": "#64748b",
    "Water Body": "#2563eb",
    "Industrial": "#f97316",
    "Open Space": "#16a34a",
    "Unassigned": "#f59e0b",
}


def style_for_structure(structure_type: str) -> dict[str, str]:
    color = STRUCTURE_COLORS.get(structure_type, STRUCTURE_COLORS["Unassigned"])
    return {"fill_color": color, "stroke_color": color}


def normalize_structure_type(value: str | None) -> str:
    clean = (value or "").strip()
    return clean if clean in STRUCTURE_COLORS else "Unassigned"


def _kml_text(node: ET.Element, tag_suffix: str) -> str:
    for child in node.iter():
        if child.tag.endswith(tag_suffix) and child.text:
            return child.text.strip()
    return ""


def _parse_kml_coordinates(raw: str) -> list[list[float]]:
    coords: list[list[float]] = []
    for token in raw.replace("\n", " ").replace("\t", " ").split():
        parts = token.split(",")
        if len(parts) < 2:
            continue
        try:
            lon = float(parts[0])
            lat = float(parts[1])
        except ValueError:
            continue
        if -180 <= lon <= 180 and -90 <= lat <= 90:
            coords.append([lon, lat])
    return coords


def _feature(name: str, geometry: dict[str, Any], properties: dict[str, Any] | None = None) -> dict[str, Any]:
    merged = {"name": name}
    if properties:
        merged.update(properties)
    return {"type": "Feature", "properties": merged, "geometry": geometry}


def parse_kml_features(kml_text: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(kml_text)
    except ET.ParseError as exc:
        raise HTTPException(status_code=400, detail=f"Invalid KML: {exc}") from exc

    placemarks = [node for node in root.iter() if node.tag.endswith("Placemark")]
    features: list[dict[str, Any]] = []
    source_nodes = placemarks or [root]

    for index, node in enumerate(source_nodes, start=1):
        name = _kml_text(node, "name") or f"KML Feature {index}"
        for polygon in [item for item in node.iter() if item.tag.endswith("Polygon")]:
            coords_raw = _kml_text(polygon, "coordinates")
            coords = _parse_kml_coordinates(coords_raw)
            if len(coords) < 3:
                continue
            if coords[0] != coords[-1]:
                coords.append(coords[0])
            features.append(_feature(name, {"type": "Polygon", "coordinates": [coords]}))

        for line in [item for item in node.iter() if item.tag.endswith("LineString")]:
            coords_raw = _kml_text(line, "coordinates")
            coords = _parse_kml_coordinates(coords_raw)
            if len(coords) < 2:
                continue
            features.append(_feature(name, {"type": "LineString", "coordinates": coords}))

        for point in [item for item in node.iter() if item.tag.endswith("Point")]:
            coords_raw = _kml_text(point, "coordinates")
            coords = _parse_kml_coordinates(coords_raw)
            if not coords:
                continue
            features.append(_feature(name, {"type": "Point", "coordinates": coords[0]}))

    if not features:
        raise HTTPException(status_code=400, detail="No supported KML geometry found")
    return features


def _find_shp_file(root: Path) -> Path:
    candidates = sorted(root.rglob("*.shp"), key=lambda p: p.name.lower())
    if not candidates:
        raise HTTPException(status_code=400, detail="Shapefile .shp not found")
    return candidates[0]


def _safe_extract_zip(archive: zipfile.ZipFile, target_dir: Path) -> None:
    resolved_target = target_dir.resolve()
    for member in archive.infolist():
        target = (target_dir / member.filename).resolve()
        if target != resolved_target and resolved_target not in target.parents:
            raise HTTPException(status_code=400, detail="Unsafe shapefile ZIP path")
        archive.extract(member, target_dir)


def _read_prj_crs(shp_path: Path):
    if CRS is None:
        return None
    prj_path = shp_path.with_suffix(".prj")
    if not prj_path.is_file():
        return None
    try:
        return CRS.from_wkt(prj_path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return None


def _transform_pair(lon: float, lat: float, source_crs) -> list[float]:
    if not source_crs or not transform_coords:
        return [lon, lat]
    try:
        target_crs = CRS.from_epsg(4326)
        if source_crs == target_crs:
            return [lon, lat]
        xs, ys = transform_coords(source_crs, target_crs, [lon], [lat])
        return [float(xs[0]), float(ys[0])]
    except Exception:
        return [lon, lat]


def _transform_coordinates(coords: Any, source_crs) -> Any:
    if (
        isinstance(coords, (list, tuple))
        and len(coords) >= 2
        and isinstance(coords[0], (int, float))
        and isinstance(coords[1], (int, float))
    ):
        return _transform_pair(float(coords[0]), float(coords[1]), source_crs)
    if isinstance(coords, (list, tuple)):
        return [_transform_coordinates(item, source_crs) for item in coords]
    return coords


def parse_shapefile_features(path: Path) -> list[dict[str, Any]]:
    if shapefile is None:
        raise HTTPException(status_code=500, detail="pyshp dependency is not installed")
    source_path = Path(path)

    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if source_path.suffix.lower() == ".zip":
            temp_dir = tempfile.TemporaryDirectory()
            with zipfile.ZipFile(source_path) as archive:
                _safe_extract_zip(archive, Path(temp_dir.name))
            shp_path = _find_shp_file(Path(temp_dir.name))
        elif source_path.suffix.lower() == ".shp":
            shp_path = source_path
        else:
            raise HTTPException(status_code=400, detail="Only .shp or zipped shapefiles are supported")

        reader = shapefile.Reader(str(shp_path))
        fields = [field[0] for field in reader.fields[1:]]
        source_crs = _read_prj_crs(shp_path)
        features: list[dict[str, Any]] = []

        for index, record_shape in enumerate(reader.iterShapeRecords(), start=1):
            geometry = record_shape.shape.__geo_interface__
            if not geometry:
                continue
            clean_geometry = json.loads(json.dumps(geometry))
            clean_geometry["coordinates"] = _transform_coordinates(clean_geometry.get("coordinates"), source_crs)
            properties = {
                str(key): value
                for key, value in zip(fields, list(record_shape.record))
                if value is not None
            }
            name = str(properties.get("name") or properties.get("Name") or properties.get("NAME") or f"SHP Feature {index}")
            features.append(_feature(name, clean_geometry, properties))

        if not features:
            raise HTTPException(status_code=400, detail="No supported shapefile geometry found")
        return features
    finally:
        if temp_dir is not None:
            temp_dir.cleanup()


def parse_spatial_upload(path: Path, suffix: str, text: str | None = None) -> list[dict[str, Any]]:
    lowered = suffix.lower()
    if lowered in {".kml", ".xml"}:
        content = text if text is not None else Path(path).read_text(encoding="utf-8", errors="ignore")
        return parse_kml_features(content)
    if lowered in {".shp", ".zip"}:
        return parse_shapefile_features(Path(path))
    if lowered in {".geojson", ".json"}:
        data = json.loads(text if text is not None else Path(path).read_text(encoding="utf-8"))
        if data.get("type") == "FeatureCollection":
            return [feature for feature in data.get("features", []) if feature.get("geometry")]
        if data.get("type") == "Feature":
            return [data]
        if data.get("type") in {"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"}:
            return [_feature("GeoJSON Feature", data)]
    raise HTTPException(status_code=400, detail="Only .kml, .geojson, .shp, or zipped shapefiles are supported")
