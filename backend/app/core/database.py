from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping, Sequence
from pathlib import Path
import os
import re
import shutil
import sqlite3
from typing import Any

try:
    from sqlalchemy import create_engine, text
    from sqlalchemy.engine import Connection, Engine, RowMapping
    from sqlalchemy.orm import sessionmaker
except ImportError:  # pragma: no cover - lets the legacy SQLite app boot before deps are installed.
    create_engine = None  # type: ignore[assignment]
    text = None  # type: ignore[assignment]
    Connection = Any  # type: ignore[misc,assignment]
    Engine = Any  # type: ignore[misc,assignment]
    RowMapping = Any  # type: ignore[misc,assignment]
    sessionmaker = None  # type: ignore[assignment]


def _load_database_environment() -> None:
    """Load the local backend environment before SQLAlchemy engines are created."""
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.is_file():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv(env_path, override=False)
        return
    except ImportError:
        pass
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_database_environment()


_DATABASE_PATH: Path | None = None


def _env_true(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _database_filename() -> str:
    return "droid_cloud_dev.db" if _env_true(os.getenv("DEV_MODE")) else "droid_cloud_prod.db"


def _project_data_dir() -> Path:
    configured = os.getenv("LOCAL_DATA_PATH")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parent.parent.parent.parent / "Project_Data"


def _sqlite_url_from_path(path: Path, *, read_only: bool = False) -> str:
    resolved = path.resolve().as_posix()
    if read_only:
        return f"sqlite:///file:{resolved}?mode=ro&uri=true"
    return f"sqlite:///{resolved}"


def _default_sqlite_path() -> Path:
    return _project_data_dir() / _database_filename()


def _default_sqlite_url(*, read_only: bool = False) -> str:
    return _sqlite_url_from_path(_default_sqlite_path(), read_only=read_only)


def _require_sqlalchemy() -> None:
    if create_engine is None or sessionmaker is None or text is None:
        raise RuntimeError(
            "SQLAlchemy is required for PostgreSQL support. Install backend requirements first."
        )


def _engine(url: str, **kwargs: Any) -> Engine | None:
    if not url:
        return None
    _require_sqlalchemy()
    return create_engine(url, future=True, **kwargs)


def _postgres_url() -> str:
    return os.getenv("POSTGRES_DATABASE_URL", "").strip()


def _sqlite_url() -> str:
    return os.getenv("SQLITE_DATABASE_URL", "").strip() or _default_sqlite_url()


def _postgres_enabled() -> bool:
    backend = os.getenv("DB_BACKEND", "").strip().lower()
    if backend:
        return backend == "postgres"
    return bool(_postgres_url())


def _postgis_srid() -> int:
    try:
        return int(os.getenv("POSTGIS_SRID", "4326"))
    except ValueError:
        return 4326


def configure_database(path: str | Path) -> None:
    """Configure the legacy SQLite path without touching PostgreSQL settings."""
    global _DATABASE_PATH
    requested_path = Path(path)
    base_dir = requested_path if requested_path.suffix == "" else requested_path.parent
    _DATABASE_PATH = base_dir / _database_filename()
    _DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("SQLITE_DATABASE_URL", _sqlite_url_from_path(_DATABASE_PATH))
    legacy_live_path = base_dir / "issues.db"
    if (
        _DATABASE_PATH.name == "droid_cloud_prod.db"
        and not _DATABASE_PATH.exists()
        and legacy_live_path.exists()
    ):
        shutil.copy2(legacy_live_path, _DATABASE_PATH)


def _require_database_path() -> Path:
    if _DATABASE_PATH is None:
        configured = os.getenv("SQLITE_DATABASE_URL", "").strip()
        if configured.startswith("sqlite:///") and "file:" not in configured:
            return Path(configured.removeprefix("sqlite:///"))
        _DATABASE_PATH_DEFAULT = _default_sqlite_path()
        return _DATABASE_PATH_DEFAULT
    return _DATABASE_PATH


sqlite_engine = _engine(_sqlite_url(), connect_args={"timeout": 30}) if create_engine else None
postgres_engine = _engine(_postgres_url(), pool_pre_ping=True) if create_engine and _postgres_url() else None
SqliteSessionLocal = sessionmaker(bind=sqlite_engine, autoflush=False, autocommit=False) if sessionmaker and sqlite_engine else None
PostgresSessionLocal = sessionmaker(bind=postgres_engine, autoflush=False, autocommit=False) if sessionmaker and postgres_engine else None


class CompatRow:
    def __init__(self, values: Mapping[str, Any]):
        self._values = dict(values)

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return list(self._values.values())[key]
        return self._values[key]

    def __iter__(self) -> Iterator[Any]:
        return iter(self._values.values())

    def keys(self) -> Iterable[str]:
        return self._values.keys()

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def as_dict(self) -> dict[str, Any]:
        return dict(self._values)


class CompatResult:
    def __init__(self, rows: Sequence[RowMapping] | None = None, rowcount: int = -1, lastrowid: Any = None):
        self._rows = [CompatRow(row) for row in (rows or [])]
        self.rowcount = rowcount
        self.lastrowid = lastrowid

    def fetchone(self) -> CompatRow | None:
        return self._rows[0] if self._rows else None

    def fetchall(self) -> list[CompatRow]:
        return list(self._rows)

    def __iter__(self) -> Iterator[CompatRow]:
        return iter(self._rows)


class PostgresCompatConnection:
    def __init__(self, engine: Engine):
        self._engine = engine
        self._connection: Connection | None = None
        self._transaction = None

    def __enter__(self) -> "PostgresCompatConnection":
        self._connection = self._engine.connect()
        self._transaction = self._connection.begin()
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        try:
            if self._transaction is not None:
                if exc_type is None:
                    self._transaction.commit()
                else:
                    self._transaction.rollback()
        finally:
            if self._connection is not None:
                self._connection.close()
            self._connection = None
            self._transaction = None

    def _require_connection(self) -> Connection:
        if self._connection is None:
            self._connection = self._engine.connect()
            self._transaction = self._connection.begin()
        return self._connection

    def execute(self, statement: str, parameters: Sequence[Any] | Mapping[str, Any] | None = None) -> CompatResult:
        sql, bind_values = _translate_sql(statement, parameters)
        returns_generated_id = bool(
            re.match(r"^\s*INSERT\s+INTO\s+(issues|users|activity_logs)\b", sql, flags=re.I)
            and not re.search(r"\bRETURNING\b", sql, flags=re.I)
        )
        if returns_generated_id:
            sql = f"{sql.rstrip().rstrip(';')} RETURNING id"
        result = self._require_connection().execute(text(sql), bind_values)
        rows = result.mappings().all() if result.returns_rows else []
        lastrowid = rows[0].get("id") if returns_generated_id and rows else None
        return CompatResult(rows=rows, rowcount=result.rowcount, lastrowid=lastrowid)

    def commit(self) -> None:
        if self._transaction is not None:
            self._transaction.commit()
            self._transaction = self._require_connection().begin()

    def rollback(self) -> None:
        if self._transaction is not None:
            self._transaction.rollback()
            self._transaction = self._require_connection().begin()

    def close(self) -> None:
        if self._connection is not None:
            self._connection.close()
        self._connection = None
        self._transaction = None


def _translate_sql(
    statement: str,
    parameters: Sequence[Any] | Mapping[str, Any] | None,
) -> tuple[str, dict[str, Any]]:
    sql = _normalize_sql_for_postgres(statement)
    if parameters is None:
        return sql, {}
    if isinstance(parameters, Mapping):
        return sql, dict(parameters)
    values = list(parameters)
    bind_values = {f"p{index}": value for index, value in enumerate(values)}
    index = 0

    def replace_placeholder(match: re.Match[str]) -> str:
        nonlocal index
        name = f":p{index}"
        index += 1
        return name

    translated = re.sub(r"\?", replace_placeholder, sql)
    return translated, bind_values


def _normalize_sql_for_postgres(statement: str) -> str:
    sql = statement.strip()
    sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql, flags=re.I)
    sql = re.sub(r"\bTEXT\b", "TEXT", sql, flags=re.I)
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\b", "INSERT", sql, flags=re.I)
    sql = sql.replace("strftime('%Y-%m-%dT%H:%M:%fZ','now')", "CURRENT_TIMESTAMP")
    sql = re.sub(
        r"ON CONFLICT\(project_id\) DO UPDATE SET catalog_revision = catalog_revision \+ 1",
        "ON CONFLICT(project_id) DO UPDATE SET catalog_revision = catalog_project_meta.catalog_revision + 1",
        sql,
        flags=re.I,
    )
    return sql


def get_db_connection() -> sqlite3.Connection | PostgresCompatConnection:
    if _postgres_enabled():
        if postgres_engine is None:
            raise RuntimeError("POSTGRES_DATABASE_URL is required when DB_BACKEND=postgres.")
        return PostgresCompatConnection(postgres_engine)
    connection = sqlite3.connect(_require_database_path(), timeout=30)
    connection.execute("PRAGMA journal_mode=WAL;")
    connection.execute("PRAGMA synchronous=NORMAL;")
    connection.row_factory = sqlite3.Row
    return connection


def ensure_tables() -> None:
    if _postgres_enabled():
        _ensure_postgres_tables()
    else:
        _ensure_sqlite_tables()


def _ensure_postgres_tables() -> None:
    if postgres_engine is None:
        raise RuntimeError("POSTGRES_DATABASE_URL is required when DB_BACKEND=postgres.")
    srid = _postgis_srid()
    with postgres_engine.begin() as connection:
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        for statement in _postgres_schema_statements(srid):
            connection.execute(text(statement))


def _postgres_schema_statements(srid: int) -> list[str]:
    return [
        """
        CREATE TABLE IF NOT EXISTS issues (
            id SERIAL PRIMARY KEY,
            lat DOUBLE PRECISION,
            lng DOUBLE PRECISION,
            title TEXT,
            description TEXT,
            status TEXT DEFAULT 'open'
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            email TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'user',
            approval_status TEXT NOT NULL DEFAULT 'approved',
            requested_role TEXT NOT NULL DEFAULT 'user',
            approved_at TEXT,
            can_access_catalog INTEGER NOT NULL DEFAULT 1,
            can_upload_data INTEGER NOT NULL DEFAULT 0,
            hidden_tabs TEXT NOT NULL DEFAULT '[]',
            location_required INTEGER NOT NULL DEFAULT 1,
            approval_token_hash TEXT
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS projects (
            id TEXT PRIMARY KEY,
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            location TEXT NOT NULL,
            date TEXT NOT NULL,
            status TEXT NOT NULL,
            type TEXT NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS sessions (
            token_hash TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id),
            expires_at INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS dataset_crop_masks (
            project_id TEXT NOT NULL,
            tile_folder TEXT NOT NULL,
            source TEXT NOT NULL,
            points_json TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, tile_folder)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS spatial_layers (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            source_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS spatial_features (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            layer_id TEXT NOT NULL REFERENCES spatial_layers(id) ON DELETE CASCADE,
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            geometry_type TEXT NOT NULL,
            geojson TEXT NOT NULL,
            geom geometry(Geometry, {srid}),
            plot_id TEXT NOT NULL DEFAULT '',
            owner_name TEXT NOT NULL DEFAULT '',
            structure_type TEXT NOT NULL DEFAULT 'Unassigned',
            fill_color TEXT NOT NULL DEFAULT '#f59e0b',
            stroke_color TEXT NOT NULL DEFAULT '#f59e0b',
            source_type TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_spatial_layers_project ON spatial_layers(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_spatial_features_project ON spatial_features(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_spatial_features_layer ON spatial_features(layer_id)",
        "CREATE INDEX IF NOT EXISTS idx_spatial_features_geom ON spatial_features USING GIST (geom)",
        f"""
        CREATE OR REPLACE FUNCTION droid_sync_spatial_feature_geom()
        RETURNS trigger AS $$
        DECLARE
            payload jsonb;
            geometry_payload jsonb;
        BEGIN
            IF NEW.geojson IS NULL OR btrim(NEW.geojson) = '' THEN
                NEW.geom = NULL;
                RETURN NEW;
            END IF;
            payload = NEW.geojson::jsonb;
            IF payload->>'type' = 'Feature' THEN
                geometry_payload = payload->'geometry';
            ELSIF payload->>'type' = 'FeatureCollection' THEN
                geometry_payload = payload->'features'->0->'geometry';
            ELSE
                geometry_payload = payload;
            END IF;
            NEW.geom = ST_SetSRID(ST_GeomFromGeoJSON(geometry_payload::text), {srid});
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """,
        "DROP TRIGGER IF EXISTS trg_spatial_features_sync_geom ON spatial_features",
        """
        CREATE TRIGGER trg_spatial_features_sync_geom
        BEFORE INSERT OR UPDATE OF geojson ON spatial_features
        FOR EACH ROW EXECUTE FUNCTION droid_sync_spatial_feature_geom()
        """,
        """
        CREATE TABLE IF NOT EXISTS camera_views (
            id TEXT PRIMARY KEY,
            project_id TEXT NOT NULL REFERENCES projects(id),
            owner_user_id INTEGER NOT NULL REFERENCES users(id),
            name TEXT NOT NULL,
            lat DOUBLE PRECISION NOT NULL,
            lng DOUBLE PRECISION NOT NULL,
            height DOUBLE PRECISION NOT NULL,
            heading DOUBLE PRECISION NOT NULL,
            pitch DOUBLE PRECISION NOT NULL,
            roll DOUBLE PRECISION NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS activity_logs (
            id SERIAL PRIMARY KEY,
            user_id INTEGER REFERENCES users(id),
            ip_address TEXT NOT NULL,
            method TEXT NOT NULL,
            endpoint TEXT NOT NULL,
            device_label TEXT NOT NULL DEFAULT '',
            latitude DOUBLE PRECISION,
            longitude DOUBLE PRECISION,
            location_accuracy DOUBLE PRECISION,
            accessed_at TEXT NOT NULL
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_user_time ON activity_logs(user_id, accessed_at)",
        "CREATE INDEX IF NOT EXISTS idx_activity_logs_accessed_at ON activity_logs(accessed_at)",
        """
        CREATE TABLE IF NOT EXISTS catalog_assets (
            id TEXT NOT NULL,
            project_id TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
            asset_type TEXT NOT NULL DEFAULT '',
            display_name TEXT NOT NULL DEFAULT '',
            source_name TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'Processing',
            progress INTEGER NOT NULL DEFAULT 0,
            stage TEXT NOT NULL DEFAULT '',
            error_message TEXT NOT NULL DEFAULT '',
            primary_rel_path TEXT NOT NULL DEFAULT '',
            paths_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            viewer_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            meta_json JSONB NOT NULL DEFAULT '{}'::jsonb,
            content_hash TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (project_id, id)
        )
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_assets_primary_path ON catalog_assets(project_id, primary_rel_path) WHERE primary_rel_path <> ''",
        "CREATE INDEX IF NOT EXISTS idx_catalog_assets_project ON catalog_assets(project_id)",
        "CREATE INDEX IF NOT EXISTS idx_catalog_assets_status ON catalog_assets(project_id, status)",
        """
        CREATE TABLE IF NOT EXISTS catalog_project_meta (
            project_id TEXT PRIMARY KEY REFERENCES projects(id) ON DELETE CASCADE,
            catalog_revision BIGINT NOT NULL DEFAULT 0,
            last_reconciled_at TEXT NOT NULL DEFAULT ''
        )
        """,
    ]


def _ensure_sqlite_tables() -> None:
    with get_db_connection() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS issues (
                id INTEGER PRIMARY KEY,
                lat REAL,
                lng REAL,
                title TEXT,
                description TEXT,
                status TEXT DEFAULT 'open'
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        user_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(users)").fetchall()
        }
        had_approval_status = "approval_status" in user_columns
        if "role" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'"
            )
        if had_approval_status:
            connection.execute(
                "UPDATE users SET role = 'user' WHERE role IS NULL OR role = ''"
            )
        else:
            connection.execute(
                "UPDATE users SET role = 'user' WHERE role IS NULL OR role = '' OR role = 'admin'"
            )
        if "approval_status" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN approval_status TEXT NOT NULL DEFAULT 'approved'"
            )
        if "requested_role" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN requested_role TEXT NOT NULL DEFAULT 'user'"
            )
        if "approved_at" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN approved_at TEXT")
        if "can_access_catalog" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN can_access_catalog INTEGER NOT NULL DEFAULT 1"
            )
        if "can_upload_data" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN can_upload_data INTEGER NOT NULL DEFAULT 0"
            )
        if "hidden_tabs" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN hidden_tabs TEXT NOT NULL DEFAULT '[]'"
            )
        if "location_required" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN location_required INTEGER NOT NULL DEFAULT 1"
            )
            connection.execute(
                "UPDATE users SET location_required = 0 WHERE LOWER(COALESCE(role, 'user')) = 'admin'"
            )
        if "approval_token_hash" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN approval_token_hash TEXT")
        connection.execute(
            "UPDATE users SET approval_status = 'approved' WHERE approval_status IS NULL OR approval_status = ''"
        )
        connection.execute(
            "UPDATE users SET requested_role = role WHERE requested_role IS NULL OR requested_role = ''"
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS projects (
                id TEXT PRIMARY KEY,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                location TEXT NOT NULL,
                date TEXT NOT NULL,
                status TEXT NOT NULL,
                type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token_hash TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS dataset_crop_masks (
                project_id TEXT NOT NULL,
                tile_folder TEXT NOT NULL,
                source TEXT NOT NULL,
                points_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, tile_folder)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS spatial_layers (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id),
                FOREIGN KEY(project_id) REFERENCES projects(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS spatial_features (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                layer_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                geometry_type TEXT NOT NULL,
                geojson TEXT NOT NULL,
                plot_id TEXT NOT NULL DEFAULT '',
                owner_name TEXT NOT NULL DEFAULT '',
                structure_type TEXT NOT NULL DEFAULT 'Unassigned',
                fill_color TEXT NOT NULL DEFAULT '#f59e0b',
                stroke_color TEXT NOT NULL DEFAULT '#f59e0b',
                source_type TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id),
                FOREIGN KEY(project_id) REFERENCES projects(id),
                FOREIGN KEY(layer_id) REFERENCES spatial_layers(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spatial_layers_project
            ON spatial_layers(project_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spatial_features_project
            ON spatial_features(project_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_spatial_features_layer
            ON spatial_features(layer_id)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS camera_views (
                id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                owner_user_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                height REAL NOT NULL,
                heading REAL NOT NULL,
                pitch REAL NOT NULL,
                roll REAL NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(owner_user_id) REFERENCES users(id),
                FOREIGN KEY(project_id) REFERENCES projects(id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS activity_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                ip_address TEXT NOT NULL,
                method TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                device_label TEXT NOT NULL DEFAULT '',
                latitude REAL,
                longitude REAL,
                location_accuracy REAL,
                accessed_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id)
            )
            """
        )
        activity_columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(activity_logs)").fetchall()
        }
        if "device_label" not in activity_columns:
            connection.execute("ALTER TABLE activity_logs ADD COLUMN device_label TEXT NOT NULL DEFAULT ''")
        if "latitude" not in activity_columns:
            connection.execute("ALTER TABLE activity_logs ADD COLUMN latitude REAL")
        if "longitude" not in activity_columns:
            connection.execute("ALTER TABLE activity_logs ADD COLUMN longitude REAL")
        if "location_accuracy" not in activity_columns:
            connection.execute("ALTER TABLE activity_logs ADD COLUMN location_accuracy REAL")
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_activity_logs_user_time
            ON activity_logs(user_id, accessed_at)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_activity_logs_accessed_at
            ON activity_logs(accessed_at)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_assets (
                id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                asset_type TEXT NOT NULL DEFAULT '',
                display_name TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'Processing',
                progress INTEGER NOT NULL DEFAULT 0,
                stage TEXT NOT NULL DEFAULT '',
                error_message TEXT NOT NULL DEFAULT '',
                primary_rel_path TEXT NOT NULL DEFAULT '',
                paths_json TEXT NOT NULL DEFAULT '{}',
                viewer_json TEXT NOT NULL DEFAULT '{}',
                meta_json TEXT NOT NULL DEFAULT '{}',
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (project_id, id),
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        connection.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_catalog_assets_primary_path
            ON catalog_assets(project_id, primary_rel_path)
            WHERE primary_rel_path <> ''
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_catalog_assets_project
            ON catalog_assets(project_id)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_catalog_assets_status
            ON catalog_assets(project_id, status)
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS catalog_project_meta (
                project_id TEXT PRIMARY KEY,
                catalog_revision INTEGER NOT NULL DEFAULT 0,
                last_reconciled_at TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
            )
            """
        )
        connection.commit()
