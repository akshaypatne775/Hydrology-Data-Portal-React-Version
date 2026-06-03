from pathlib import Path
import sqlite3


_DATABASE_PATH: Path | None = None


def configure_database(path: str | Path) -> None:
    global _DATABASE_PATH
    _DATABASE_PATH = Path(path)
    _DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)


def _require_database_path() -> Path:
    if _DATABASE_PATH is None:
        raise RuntimeError("Database path has not been configured.")
    return _DATABASE_PATH


def get_db_connection() -> sqlite3.Connection:
    connection = sqlite3.connect(_require_database_path())
    connection.row_factory = sqlite3.Row
    return connection


def ensure_tables() -> None:
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
        connection.commit()
