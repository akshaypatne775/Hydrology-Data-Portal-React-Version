# PostgreSQL/PostGIS Zero-Data-Loss Migration

This migration keeps the existing SQLite database read-only and migrates data into a fresh PostgreSQL/PostGIS database.

## 1. Install Backend Dependencies

```powershell
pip install -r backend/requirements.txt
```

## 2. Create PostgreSQL Database

```sql
CREATE DATABASE droid_master_suite;
CREATE USER droid_user WITH PASSWORD 'CHANGE_ME';
GRANT ALL PRIVILEGES ON DATABASE droid_master_suite TO droid_user;
\c droid_master_suite
CREATE EXTENSION IF NOT EXISTS postgis;
```

## 3. Configure Environment

Copy values from `backend/.env.postgres.example` into `backend/.env` and replace the password.

Important:
- `SQLITE_DATABASE_URL` must point to the old `.db` with `mode=ro&uri=true`.
- Never copy PostgreSQL data back into SQLite.
- Do not delete `.db`, `.db-wal`, or `.db-shm`.

## 4. Run Migration Once

```powershell
python backend/scripts/migrate_sqlite_to_postgres.py
```

The script refuses to run if target PostgreSQL tables already contain data.

## 5. Start Backend On PostgreSQL

Set:

```env
DB_BACKEND=postgres
POSTGRES_DATABASE_URL=postgresql+psycopg2://droid_user:CHANGE_ME@localhost:5432/droid_master_suite
```

Then start FastAPI normally. SQLite remains available only as the legacy read-only migration source.
