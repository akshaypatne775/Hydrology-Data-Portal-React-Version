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
ENV_PATH = BACKEND_DIR / ".env"
SETUP_MARKER = BACKEND_DIR / ".postgres_dev_setup_complete"


def _quoted_env(value: str) -> str:
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _update_env(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pending = dict(updates)
    result: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in line:
            key = line.split("=", 1)[0].strip()
            if key in pending:
                result.append(f"{key}={_quoted_env(pending.pop(key))}")
                continue
        result.append(line)
    if result and result[-1]:
        result.append("")
    result.append("# Dev-only PostgreSQL/PostGIS configuration")
    for key, value in pending.items():
        result.append(f"{key}={_quoted_env(value)}")
    path.write_text("\n".join(result).rstrip() + "\n", encoding="utf-8")


def _role_exists(connection, role_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,))
        return cursor.fetchone() is not None


def _database_exists(connection, database_name: str) -> bool:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (database_name,))
        return cursor.fetchone() is not None


def setup_dev_postgres(
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
                print(f"[OK] Created Dev application role: {app_role}")

            if _database_exists(admin_connection, database_name):
                cursor.execute(
                    sql.SQL("ALTER DATABASE {} OWNER TO {}").format(
                        sql.Identifier(database_name),
                        sql.Identifier(app_role),
                    )
                )
                print(f"[OK] Reusing existing Dev database: {database_name}")
            else:
                cursor.execute(
                    sql.SQL(
                        "CREATE DATABASE {} OWNER {} ENCODING 'UTF8' TEMPLATE template0"
                    ).format(sql.Identifier(database_name), sql.Identifier(app_role))
                )
                print(f"[OK] Created Dev database: {database_name}")

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
    sqlite_path = (REPO_ROOT / "Project_Data" / "droid_cloud_dev.db").resolve().as_posix()
    sqlite_url = f"sqlite:///file:{sqlite_path}?mode=ro&uri=true"
    _update_env(
        ENV_PATH,
        {
            "DB_BACKEND": "postgres",
            "DEV_MODE": "True",
            "POSTGRES_DATABASE_URL": postgres_url,
            "SQLITE_DATABASE_URL": sqlite_url,
            "POSTGIS_SRID": "4326",
        },
    )
    SETUP_MARKER.write_text(
        f"database={database_name}\nrole={app_role}\nhost={host}\nport={port}\n",
        encoding="utf-8",
    )
    print(f"[OK] Dev environment updated: {ENV_PATH}")
    print("[SAFE] Live SQLite database and Live release configuration were not accessed.")


def main() -> int:
    parser = argparse.ArgumentParser(description="Create the Droid Dev PostgreSQL/PostGIS database.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5432)
    parser.add_argument("--admin-user", default="postgres")
    parser.add_argument("--database", default="droid_master_suite_dev")
    parser.add_argument("--app-role", default="droid_dev_app")
    args = parser.parse_args()

    print("Droid Dev PostgreSQL/PostGIS Setup")
    print("This configures DEV only. Live SQLite is not opened or modified.")
    admin_password = getpass(f"Password for PostgreSQL administrator '{args.admin_user}': ")
    if not admin_password:
        print("[ERROR] PostgreSQL administrator password is required.")
        return 1
    try:
        setup_dev_postgres(
            host=args.host,
            port=args.port,
            admin_user=args.admin_user,
            admin_password=admin_password,
            database_name=args.database,
            app_role=args.app_role,
        )
    except Exception as exc:
        print(f"[ERROR] Dev PostgreSQL setup failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
