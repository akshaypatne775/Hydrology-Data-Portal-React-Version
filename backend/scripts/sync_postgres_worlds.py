from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
SCRIPTS_DIR = BACKEND_DIR / "scripts"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from app.core.database import _postgres_schema_statements  # noqa: E402
from migrate_sqlite_to_postgres import MIGRATION_TABLES  # noqa: E402


DEV_ENV_PATH = BACKEND_DIR / ".env"
LIVE_ENV_PATH = BACKEND_DIR / ".env.live"

COPY_CHUNK_SIZE = 1000


def _load_env_value(env_path: Path, key: str) -> str:
    if not env_path.is_file():
        raise FileNotFoundError(f"Environment file not found: {env_path}")
    try:
        from dotenv import dotenv_values

        values = dotenv_values(env_path)
        value = str(values.get(key) or "").strip().strip('"').strip("'")
    except ImportError:
        value = ""
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            env_key, env_value = line.split("=", 1)
            if env_key.strip() == key:
                value = env_value.strip().strip('"').strip("'")
                break
    if not value:
        raise RuntimeError(f"{key} is missing in {env_path}")
    return value


def _postgres_url(env_path: Path) -> str:
    return _load_env_value(env_path, "POSTGRES_DATABASE_URL")


def _postgis_srid(env_path: Path) -> int:
    try:
        return int(_load_env_value(env_path, "POSTGIS_SRID"))
    except Exception:
        return 4326


def _postgres_table_exists(connection: Connection, table_name: str) -> bool:
    return bool(
        connection.execute(
            text("SELECT to_regclass(:table_name)"),
            {"table_name": f"public.{table_name}"},
        ).scalar_one()
    )


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
    return [str(row[0]) for row in rows]


def _source_tables(connection: Connection) -> list[str]:
    tables = [table_name for table_name in MIGRATION_TABLES if _postgres_table_exists(connection, table_name)]
    if not tables:
        raise RuntimeError("Source PostgreSQL database has no migration tables to copy.")
    return tables


def _ensure_target_schema(connection: Connection, srid: int) -> None:
    connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
    for statement in _postgres_schema_statements(srid):
        connection.execute(text(statement))


def _truncate_target_tables(connection: Connection, table_names: Iterable[str]) -> None:
    existing = [table_name for table_name in table_names if _postgres_table_exists(connection, table_name)]
    if not existing:
        return
    connection.execute(text(f"TRUNCATE TABLE {', '.join(existing)} RESTART IDENTITY CASCADE"))
    print(f"[RESET] Truncated target tables: {', '.join(existing)}")


def _chunked(rows: list[dict[str, Any]], size: int) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(rows), size):
        yield rows[index : index + size]


def _jsonb_columns(connection: Connection, table_name: str) -> set[str]:
    rows = connection.execute(
        text(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = :table_name AND data_type = 'jsonb'
            """
        ),
        {"table_name": table_name},
    ).fetchall()
    return {str(row[0]) for row in rows}


def _normalize_payload(row: dict[str, Any], jsonb_columns: set[str]) -> dict[str, Any]:
    normalized = dict(row)
    for column in jsonb_columns:
        value = normalized.get(column)
        if value is not None and not isinstance(value, str):
            normalized[column] = json.dumps(value)
    return normalized


def _copy_table(
    source_connection: Connection,
    target_connection: Connection,
    table_name: str,
) -> int:
    source_columns = _postgres_columns(source_connection, table_name)
    target_columns = set(_postgres_columns(target_connection, table_name))
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        return 0
    jsonb_columns = _jsonb_columns(source_connection, table_name)
    column_sql = ", ".join(columns)
    rows = source_connection.execute(
        text(f"SELECT {column_sql} FROM {table_name}")
    ).mappings().all()
    if not rows:
        return 0
    placeholders = ", ".join(f":{column}" for column in columns)
    insert_sql = text(
        f"INSERT INTO {table_name} ({column_sql}) VALUES ({placeholders})"
    )
    payload = [_normalize_payload(dict(row), jsonb_columns) for row in rows]
    for chunk in _chunked(payload, COPY_CHUNK_SIZE):
        target_connection.execute(insert_sql, chunk)
    return len(rows)


def _reset_serial_sequences(connection: Connection, table_names: Iterable[str]) -> None:
    sequence_tables = {
        "issues": "id",
        "users": "id",
        "activity_logs": "id",
    }
    for table_name, column_name in sequence_tables.items():
        if table_name not in table_names or not _postgres_table_exists(connection, table_name):
            continue
        connection.execute(
            text(
                f"""
                SELECT setval(
                    pg_get_serial_sequence('{table_name}', '{column_name}'),
                    COALESCE((SELECT MAX({column_name}) FROM {table_name}), 1),
                    (SELECT COUNT(*) FROM {table_name}) > 0
                )
                """
            )
        )


def _verify_copied_counts(
    target_connection: Connection,
    copied_counts: dict[str, int],
) -> None:
    for table_name, expected_count in copied_counts.items():
        target_count = int(
            target_connection.execute(text(f"SELECT COUNT(*) FROM {table_name}")).scalar_one()
        )
        if target_count != expected_count:
            raise RuntimeError(
                f"Row-count mismatch for {table_name}: copied={expected_count}, target={target_count}"
            )
        print(f"[VERIFY] {table_name}: {target_count} rows")


def sync_postgres_worlds(*, direction: str) -> None:
    if direction == "dev-to-live":
        source_env = DEV_ENV_PATH
        target_env = LIVE_ENV_PATH
        source_label = "Dev PostgreSQL droid_master_suite_dev"
        target_label = "Live PostgreSQL droid_master_suite"
    elif direction == "live-to-dev":
        source_env = LIVE_ENV_PATH
        target_env = DEV_ENV_PATH
        source_label = "Live PostgreSQL droid_master_suite"
        target_label = "Dev PostgreSQL droid_master_suite_dev"
    else:
        raise ValueError(f"Unsupported sync direction: {direction}")

    source_url = _postgres_url(source_env)
    target_url = _postgres_url(target_env)
    srid = _postgis_srid(target_env)
    print(f"[SOURCE] {source_label}")
    print(f"[TARGET] {target_label}")

    source_engine = create_engine(
        source_url,
        future=True,
        pool_pre_ping=True,
        isolation_level="REPEATABLE READ",
    )
    target_engine = create_engine(target_url, future=True, pool_pre_ping=True)
    try:
        with source_engine.connect() as source_connection, target_engine.connect() as target_connection:
            with source_connection.begin():
                table_names = _source_tables(source_connection)
                skipped = [table_name for table_name in MIGRATION_TABLES if table_name not in table_names]
                if skipped:
                    print(f"[SKIP] Source tables not present: {', '.join(skipped)}")
                copied_counts: dict[str, int] = {}
                transaction = target_connection.begin()
                try:
                    _ensure_target_schema(target_connection, srid)
                    _truncate_target_tables(target_connection, MIGRATION_TABLES)
                    for table_name in table_names:
                        copied = _copy_table(source_connection, target_connection, table_name)
                        copied_counts[table_name] = copied
                        print(f"[COPY] {table_name}: {copied} rows")
                    _reset_serial_sequences(target_connection, table_names)
                    if "spatial_features" in table_names:
                        spatial_count = int(
                            target_connection.execute(text("SELECT COUNT(*) FROM spatial_features")).scalar_one()
                        )
                        valid_geom_count = int(
                            target_connection.execute(
                                text(
                                    "SELECT COUNT(*) FROM spatial_features "
                                    "WHERE geom IS NOT NULL AND ST_IsValid(geom)"
                                )
                            ).scalar_one()
                        )
                        if valid_geom_count != spatial_count:
                            raise RuntimeError(
                                "PostGIS validation failed after copy: "
                                f"spatial_features={spatial_count}, valid_geom={valid_geom_count}"
                            )
                    _verify_copied_counts(target_connection, copied_counts)
                    transaction.commit()
                    print(f"[SYNC COMPLETE] {direction}")
                except Exception:
                    transaction.rollback()
                    raise
    finally:
        source_engine.dispose()
        target_engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy PostgreSQL portal data between Dev and Live worlds."
    )
    parser.add_argument(
        "--direction",
        required=True,
        choices=["dev-to-live", "live-to-dev"],
        help="dev-to-live seeds Live from Dev; live-to-dev refreshes Dev from Live.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        sync_postgres_worlds(direction=args.direction)
    except Exception as exc:  # noqa: BLE001
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
