from __future__ import annotations

import argparse
from getpass import getpass
from pathlib import Path
import secrets
from urllib.parse import quote

import psycopg2
from psycopg2 import sql


BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
DEV_ENV_PATH = BACKEND_DIR / ".env"
ENV_PATH = BACKEND_DIR / ".env.live"
LIVE_RELEASE_ROOT = Path(r"D:\1_Portal_Workflows_development\DroidSurvair_Live_Release")
LIVE_ENV_PATH = LIVE_RELEASE_ROOT / "backend" / ".env"
SETUP_MARKER = BACKEND_DIR / ".postgres_live_setup_complete"

LIVE_ENV_KEYS = {
    "DB_BACKEND",
    "DEV_MODE",
    "POSTGRES_DATABASE_URL",
    "SQLITE_DATABASE_URL",
    "POSTGIS_SRID",
    "CATALOG_DB_ENABLED",
    "CATALOG_JSON_MIRROR",
}


def _quoted_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _seed_env_lines() -> list[str]:
    if ENV_PATH.is_file():
        return ENV_PATH.read_text(encoding="utf-8").splitlines()
    if DEV_ENV_PATH.is_file():
        return DEV_ENV_PATH.read_text(encoding="utf-8").splitlines()
    return []


def _update_env(path: Path, updates: dict[str, str]) -> None:
    lines = _seed_env_lines() if path == ENV_PATH else (
        path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    )
    pending = dict(updates)
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in pending:
                result.append(f"{key}={_quoted_env(pending.pop(key))}")
                continue
            if path == ENV_PATH and key in LIVE_ENV_KEYS:
                continue
        result.append(line)
    while result and not result[-1].strip():
        result.pop()
    if result:
        result.append("")
    result.append("# Live-only PostgreSQL/PostGIS configuration")
    for key, value in pending.items():
        result.append(f"{key}={_quoted_env(value)}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def _role_exists(connection, role_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
        return cursor.fetchone() is not None


def _database_exists(connection, database_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        return cursor.fetchone() is not None


def setup_live_postgres(
    *,
    host: str,
    port: int,
    admin_user: str,
    admin_password: str,
    database_name: str,
    app_role: str,
) -> None:
    app_password = secrets.token_urlsafe(32)
    admin_connection = psycopg2.connect(
        host=host,
        port=port,
        dbname="postgres",
        user=admin_user,
        password=admin_password,
        connect_timeout=10,
    )
    admin_connection.autocommit = True
    try:
        with admin_connection.cursor() as cursor:
            if _role_exists(admin_connection, app_role):
                cursor.execute(
                    sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(app_role)),
                    (app_password,),
                )
                print(f"[OK] Refreshed credentials for existing role: {app_role}")
            else:
                cursor.execute(
                    sql.SQL("CREATE ROLE {} WITH LOGIN PASSWORD %s").format(sql.Identifier(app_role)),
                    (app_password,),
                )
                print(f"[OK] Created Live application role: {app_role}")

            if _database_exists(admin_connection, database_name):
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                        sql.Identifier(database_name),
                        sql.Identifier(app_role),
                    )
                )
                print(f"[OK] Reusing existing Live database: {database_name}")
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0"
                    ).format(sql.Identifier(database_name), sql.Identifier(app_role))
                )
                print(f"[OK] Created Live database: {database_name}")

            cursor.execute(
                sql.SQL("GRANT ALL PRIVILEGES ON DATABASE {} TO {}").format(
                    sql.Identifier(database_name),
                    sql.Identifier(app_role),
                )
            )
    finally:
        admin_connection.close()

    target_connection = psycopg2.connect(
        host=host,
        port=port,
        dbname=database_name,
        user=admin_user,
        password=admin_password,
        connect_timeout=10,
    )
    target_connection.autocommit = True
    try:
        with target_connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS postgis")
            cursor.execute(
                sql.SQL("GRANT USAGE, CREATE ON SCHEMA public TO {}").format(
                    sql.Identifier(app_role)
                )
            )
            cursor.execute("SELECT postgis_full_version()")
            print(f"[OK] PostGIS active: {cursor.fetchone()[0]}")
    finally:
        target_connection.close()

    encoded_role = quote(app_role, safe="")
    encoded_password = quote(app_password, safe="")
    postgres_url = (
        f"postgresql+psycopg2://{encoded_role}:{encoded_password}"
        f"@{host}:{port}/{database_name}"
    )
    sqlite_path = (REPO_ROOT / "Project_Data" / "droid_cloud_prod.db").resolve().as_posix()
    sqlite_url = f"sqlite:///file:{sqlite_path}?mode=ro&uri=true"
    live_updates = {
        "DB_BACKEND": "postgres",
        "DEV_MODE": "False",
        "POSTGRES_DATABASE_URL": postgres_url,
        "SQLITE_DATABASE_URL": sqlite_url,
        "POSTGIS_SRID": "4326",
        "CATALOG_DB_ENABLED": "true",
        "CATALOG_JSON_MIRROR": "true",
    }
    _update_env(ENV_PATH, live_updates)
    if LIVE_RELEASE_ROOT.exists():
        _update_env(LIVE_ENV_PATH, live_updates)
        print(f"[OK] Live release environment updated: {LIVE_ENV_PATH}")
    SETUP_MARKER.write_text(
        f"database={database_name}\nrole={app_role}\nhost={host}\nport={port}\n",
        encoding="utf-8",
    )
    print(f"[OK] Live environment updated: {ENV_PATH}")
    print("[SAFE] Dev PostgreSQL and droid_master_suite_dev were not modified.")
    print("[NEXT] Run Droid_Environment_Manager\\7_Migrate_Live_SQLite_to_PostgreSQL.bat")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Droid Live PostgreSQL/PostGIS database.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--database", default="droid_master_suite")
    parser.add_argument("--app-role", default="droid_live_app")
    args = parser.parse_args()

    print("Droid Live PostgreSQL/PostGIS Setup")
    print("This configures LIVE only. Dev PostgreSQL is not modified.")
    admin_password = getpass(f"Password for PostgreSQL administrator '{args.admin_user}': ")
    if not admin_password:
        print("[ERROR] PostgreSQL administrator password is required.")
        return 1
    try:
        setup_live_postgres(
            host=args.host,
            port=args.port,
            admin_user=args.admin_user,
            admin_password=admin_password,
            database_name=args.database,
            app_role=args.app_role,
        )
    except Exception as exc:
        print(f"[ERROR] Live PostgreSQL setup failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
