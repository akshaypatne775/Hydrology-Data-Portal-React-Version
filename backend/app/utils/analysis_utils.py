import math
from pathlib import Path
from typing import Any


def _haversine_m(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    radius_m = 6371008.8
    phi_a = math.radians(lat_a)
    phi_b = math.radians(lat_b)
    d_phi = phi_b - phi_a
    d_lambda = math.radians(lng_b - lng_a)
    a = (
        math.sin(d_phi / 2) ** 2
        + math.cos(phi_a) * math.cos(phi_b) * math.sin(d_lambda / 2) ** 2
    )
    return radius_m * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


def _normalize_lon_lat_line(coordinates: list[Any]) -> list[tuple[float, float]]:
    clean: list[tuple[float, float]] = []
    for item in coordinates:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        lon = float(item[0])
        lat = float(item[1])
        if math.isfinite(lat) and math.isfinite(lon):
            clean.append((lon, lat))
    if len(clean) < 2:
        raise ValueError("At least two line coordinates are required.")
    return clean


def _interpolate_lon_lat_line(
    coordinates: list[Any],
    samples: int,
) -> list[tuple[float, float, float]]:
    line = _normalize_lon_lat_line(coordinates)
    segment_lengths: list[float] = []
    total_m = 0.0
    for index in range(1, len(line)):
        prev_lon, prev_lat = line[index - 1]
        next_lon, next_lat = line[index]
        distance = _haversine_m(prev_lat, prev_lon, next_lat, next_lon)
        segment_lengths.append(distance)
        total_m += distance

    sample_count = max(2, min(int(samples or 160), 800))
    targets = [total_m * index / (sample_count - 1) for index in range(sample_count)]
    output: list[tuple[float, float, float]] = []
    segment_start_m = 0.0
    segment_index = 0
    for target_m in targets:
        while (
            segment_index < len(segment_lengths) - 1
            and target_m > segment_start_m + segment_lengths[segment_index]
        ):
            segment_start_m += segment_lengths[segment_index]
            segment_index += 1
        segment_len = segment_lengths[segment_index] or 1.0
        ratio = (target_m - segment_start_m) / segment_len
        lon_a, lat_a = line[segment_index]
        lon_b, lat_b = line[segment_index + 1]
        output.append((
            lon_a + (lon_b - lon_a) * ratio,
            lat_a + (lat_b - lat_a) * ratio,
            target_m,
        ))
    return output


def sample_cross_section(
    raster_path: Path,
    line_coordinates: list[Any],
    samples: int = 180,
) -> dict[str, Any]:
    try:
        import rasterio  # type: ignore
        from rasterio.warp import transform as rio_transform  # type: ignore
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError("Raster analysis requires rasterio in the backend environment.") from exc

    sample_points = _interpolate_lon_lat_line(line_coordinates, samples)
    with rasterio.open(str(raster_path)) as src:
        lons = [point[0] for point in sample_points]
        lats = [point[1] for point in sample_points]
        xs, ys = rio_transform("EPSG:4326", src.crs, lons, lats) if src.crs else (lons, lats)
        sampled = src.sample(list(zip(xs, ys)), masked=True)
        rows: list[dict[str, Any]] = []
        for (lon, lat, distance_m), value in zip(sample_points, sampled):
            elevation: float | None = None
            first = value[0] if len(value) else None
            if first is not None and not getattr(first, "mask", False):
                numeric = float(first)
                if math.isfinite(numeric):
                    elevation = numeric
            rows.append({
                "distance": distance_m,
                "distance_m": distance_m,
                "elevation": elevation,
                "lat": lat,
                "lng": lon,
            })

    valid = [float(row["elevation"]) for row in rows if row["elevation"] is not None]
    return {
        "points": rows,
        "length_m": rows[-1]["distance_m"] if rows else 0.0,
        "min_elevation": min(valid) if valid else None,
        "max_elevation": max(valid) if valid else None,
        "avg_elevation": sum(valid) / len(valid) if valid else None,
        "start_elevation": valid[0] if valid else None,
        "end_elevation": valid[-1] if valid else None,
        "elevation_change": (valid[-1] - valid[0]) if len(valid) >= 2 else None,
    }
