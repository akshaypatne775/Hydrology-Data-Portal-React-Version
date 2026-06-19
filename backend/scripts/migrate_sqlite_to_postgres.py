from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover
    load_dotenv = None

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.core.database import _postgres_schema_statements  # noqa: E402


MIGRATION_TABLES = [
    "users",
    "projects",
    "issues",
    "sessions",
    "dataset_crop_masks",
    "spatial_layers",
    "spatial_features",
    "camera_views",
    "activity_logs",
]


def _load_env() -> None:
    if load_dotenv is not None:
        load_dotenv(BACKEND_DIR / ".env", override=False)
        load_dotenv(REPO_ROOT / ".env", override=False)
        return
    for env_path in (BACKEND_DIR / ".env", REPO_ROOT / ".env"):
        if not env_path.is_file():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _default_sqlite_file() -> Path:
    project_data = Path(os.getenv("LOCAL_DATA_PATH", str(REPO_ROOT / "Project_Data")))
    filename = "droid_cloud_dev.db" if os.getenv("DEV_MODE", "").lower() in {"1", "true", "yes", "on"} else "droid_cloud_prod.db"
    return project_data / filename


def _sqlite_path_from_url(value: str) -> Path:
    url = (value or "").strip()
    if not url:
        return _default_sqlite_file()
    if url.startswith("sqlite:///file:"):
        clean = url.removeprefix("sqlite:///file:")
        clean = clean.split("?", 1)[0]
        return Path(clean)
    if url.startswith("sqlite:///"):
        return Path(url.removeprefix("sqlite:///"))
    raise ValueError(f"Unsupported SQLITE_DATABASE_URL. Use sqlite:///... or sqlite:///file:...?mode=ro&uri=true. Got: {url}")


def _connect_sqlite_readonly(sqlite_path: Path) -> sqlite3.Connection:
    if not sqlite_path.is_file():
        raise FileNotFoundError(f"SQLite source database does not exist: {sqlite_path}")
    uri = f"file:{sqlite_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.row_factory = sqlite3.Row
    return connection


def _sqlite_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    rows = connection.execute(f"PRAGMA table_info({table_name})").fetchall()
    if not rows:
        raise RuntimeError(f"SQLite source table is missing: {table_name}")
    return [str(row["name"]) for row in rows]


def _postgres_columns(connection: Connection, table_name: str) -> list[str]:
    rows = connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name
            ORDER BY ordinal_position
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    if not rows:
        raise RuntimeError(f"PostgreSQL target table is missing: {table_name}")
    return [str(row[0]) for row in rows]


def _target_has_data(connection: Connection) -> list[tuple[str, int]]:
    occupied: list[tuple[str, int]] = []
    for table_name in MIGRATION_TABLES:
        count = int(connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one())
        if count:
            occupied.append((table_name, count))
    return occupied


def _geojson_geometry_text(raw_geojson: str, table_name: str, row_key: Any) -> str:
    try:
        parsed = json.loads(raw_geojson)
        if parsed.get("type") == "Feature":
            geometry = parsed.get("geometry")
        elif parsed.get("type") == "FeatureCollection":
            features = parsed.get("features") or []
            if not features:
                raise ValueError("FeatureCollection has no features")
            geometry = features[0].get("geometry")
        else:
            geometry = parsed
        if not isinstance(geometry, dict) or not geometry.get("type") or "coordinates" not in geometry:
            raise ValueError("GeoJSON geometry is missing type/coordinates")
        return json.dumps(geometry, separators=(",", ":"))
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(f"Invalid GeoJSON in table={table_name}, row={row_key}: {exc}") from exc


def _insert_row(
    connection: Connection,
    table_name: str,
    sqlite_columns: list[str],
    postgres_columns: list[str],
    row: sqlite3.Row,
    srid: int,
) -> None:
    row_dict = {column: row[column] for column in sqlite_columns}
    row_key = row_dict.get("id") or row_dict.get("token_hash") or row_dict.get("project_id") or "unknown"
    insert_columns = [column for column in postgres_columns if column in row_dict]
    values = {column: row_dict[column] for column in insert_columns}
    if table_name == "spatial_features" and "geom" in postgres_columns:
        insert_columns.append("geom")
        geometry_text = _geojson_geometry_text(str(row_dict.get("geojson") or ""), table_name, row_key)
        values["geom_json"] = geometry_text
        values["srid"] = srid
        placeholders = [
            "ST_SetSRID(ST_GeomFromGeoJSON(:geom_json), :srid)" if column == "geom" else f":{column}"
            for column in insert_columns
        ]
    else:
        placeholders = [f":{column}" for column in insert_columns]
    sql = text(
        f"""
        INSERT INTO {table_name} ({", ".join(insert_columns)})
        VALUES ({", ".join(placeholders)})
        """
    )
    connection.execute(sql, values)


def _reset_serial_sequences(connection: Connection) -> None:
    sequence_tables = {
        "issues": "id",
        "users": "id",
        "activity_logs": "id",
    }
    for table_name, column_name in sequence_tables.items():
        connection.execute(
            text(
                """
                SELECT setval(
                    pg_get_serial_sequence(:table_name, :column_name),
                    COALESCE((SELECT MAX(id) FROM """ + table_name + """), 1),
                    (SELECT COUNT(*) FROM """ + table_name + """) > 0
                )
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )


def run_migration(sqlite_path: Path, postgres_url: str, srid: int) -> None:
    if not postgres_url:
        raise RuntimeError("POSTGRES_DATABASE_URL is required.")
    sqlite_connection = _connect_sqlite_readonly(sqlite_path)
    postgres_engine = create_engine(postgres_url, future=True, pool_pre_ping=True)
    try:
        with postgres_engine.connect() as postgres_connection:
            transaction = postgres_connection.begin()
            try:
                postgres_connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
                for statement in _postgres_schema_statements(srid):
                    postgres_connection.execute(text(statement))
                occupied = _target_has_data(postgres_connection)
                if occupied:
                    details = ", ".join(f"{table}={count}" for table, count in occupied)
                    raise RuntimeError(
                        "PostgreSQL target is not empty. Refusing migration to avoid duplicate/partial data. "
                        f"Existing rows: {details}"
                    )
                for table_name in MIGRATION_TABLES:
                    sqlite_columns = _sqlite_columns(sqlite_connection, table_name)
                    postgres_columns = _postgres_columns(postgres_connection, table_name)
                    rows = sqlite_connection.execute(f"SELECT * FROM {table_name}").fetchall()
                    print(f"[MIGRATE] {table_name}: {len(rows)} rows")
                    for index, row in enumerate(rows, start=1):
                        try:
                            _insert_row(postgres_connection, table_name, sqlite_columns, postgres_columns, row, srid)
                        except Exception as exc:  # noqa: BLE001
                            row_dict = {column: row[column] for column in sqlite_columns}
                            row_key = row_dict.get("id") or row_dict.get("token_hash") or row_dict.get("project_id") or index
                            raise RuntimeError(
                                "[MIGRATION ERROR] "
                                f"table={table_name} row_index={index} row_key={row_key} "
                                f"row={json.dumps(row_dict, default=str)} error={exc}"
                            ) from exc
                _reset_serial_sequences(postgres_connection)
                transaction.commit()
                print("[MIGRATION COMPLETE] SQLite was opened read-only and PostgreSQL migration committed.")
            except Exception:
                transaction.rollback()
                raise
    finally:
        sqlite_connection.close()
        postgres_engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely migrate Droid Cloud SQLite data to PostgreSQL/PostGIS.")
    parser.add_argument("--sqlite-path", type=Path, default=None, help="Optional explicit SQLite .db path. Opened read-only.")
    parser.add_argument("--postgres-url", default=None, help="Optional explicit PostgreSQL SQLAlchemy URL.")
    parser.add_argument("--srid", type=int, default=None, help="PostGIS SRID for migrated GeoJSON geometries.")
    return parser.parse_args()


def main() -> int:
    _load_env()
    args = parse_args()
    sqlite_path = args.sqlite_path or _sqlite_path_from_url(os.getenv("SQLITE_DATABASE_URL", ""))
    postgres_url = args.postgres_url or os.getenv("POSTGRES_DATABASE_URL", "").strip()
    srid = args.srid or int(os.getenv("POSTGIS_SRID", "4326"))
    print(f"[SOURCE] SQLite read-only: {sqlite_path}")
    print("[TARGET] PostgreSQL/PostGIS target configured.")
    try:
        run_migration(sqlite_path, postgres_url, srid)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
