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
        connection.commit()
