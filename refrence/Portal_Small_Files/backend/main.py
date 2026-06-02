from typing import Annotated

import bcrypt
import jwt
from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel
import sqlite3
import datetime
import json
import threading
import base64
import binascii
import os
import re
import uuid
import mimetypes
from pathlib import Path

app = FastAPI()

JWT_SECRET = os.environ.get("JWT_SECRET", "dev-insecure-change-me")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 12

app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"],
)

# ==========================================
# UPLOADS (files on disk; DB stores relative paths like uploads/name.ext)
# ==========================================
BACKEND_ROOT = Path(__file__).resolve().parent
UPLOAD_DIR = BACKEND_ROOT / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

# Prefix stored in SQLite (same columns as before; values are paths, not Base64)
UPLOAD_REL_PREFIX = "uploads/"


def _api_public_base() -> str:
    return os.environ.get("API_PUBLIC_BASE", "http://127.0.0.1:8000").rstrip("/")


def _delete_file_if_path(stored: str) -> None:
    s = _normalize_stored_ref((stored or "").strip())
    if not s.startswith(UPLOAD_REL_PREFIX):
        return
    rel = s[len(UPLOAD_REL_PREFIX) :].lstrip("/")
    if not rel or ".." in rel:
        return
    p = (UPLOAD_DIR / rel).resolve()
    try:
        p.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def _safe_disk_path(stored: str) -> Path | None:
    s = _normalize_stored_ref((stored or "").strip())
    if not s.startswith(UPLOAD_REL_PREFIX):
        return None
    rel = s[len(UPLOAD_REL_PREFIX) :].lstrip("/")
    if not rel or ".." in rel:
        return None
    p = (UPLOAD_DIR / rel).resolve()
    try:
        p.relative_to(UPLOAD_DIR.resolve())
    except ValueError:
        return None
    return p if p.is_file() else None


def _decode_data_url_or_b64(raw: str) -> tuple[bytes, str, str]:
    """Decode payload; returns (data, media_type, file_extension)."""
    raw = (raw or "").strip()
    if not raw:
        return b"", "", ""
    if raw.startswith("data:"):
        try:
            header, b64part = raw.split(",", 1)
            media = "application/octet-stream"
            if ":" in header:
                media = header.split(":", 1)[1].split(";")[0].strip() or media
            data = base64.b64decode(b64part, validate=False)
            ext = "bin"
            if "jpeg" in media or "jpg" in media:
                ext = "jpg"
            elif "png" in media:
                ext = "png"
            elif "pdf" in media:
                ext = "pdf"
            elif "webp" in media:
                ext = "webp"
            elif "gif" in media:
                ext = "gif"
            return data, media, ext
        except Exception:
            return b"", "", ""
    try:
        data = base64.b64decode(raw, validate=False)
    except (binascii.Error, ValueError):
        return b"", "", ""
    if len(data) < 8:
        return b"", "", ""
    if data[:4] == b"%PDF":
        return data, "application/pdf", "pdf"
    if data[:2] == b"\xff\xd8":
        return data, "image/jpeg", "jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return data, "image/png", "png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return data, "image/gif", "gif"
    return data, "application/octet-stream", "bin"


def _save_upload_file(property_id: str, doc_kind: str, raw_b64: str) -> str:
    """Write file under uploads/. Returns relative path uploads/name.ext or ""."""
    data, _media, ext = _decode_data_url_or_b64(raw_b64)
    if not data or len(data) < 10:
        return ""
    safe_prop = re.sub(r"[^\w\-\.]+", "_", str(property_id))[:80] or "unknown"
    uid = uuid.uuid4().hex[:12]
    name = f"{doc_kind}_{safe_prop}_{uid}.{ext}"
    path = UPLOAD_DIR / name
    path.write_bytes(data)
    return f"{UPLOAD_REL_PREFIX}{name}"


def _normalize_stored_ref(s: str) -> str:
    """Turn absolute URLs from clients back into uploads/... if applicable."""
    s = (s or "").strip()
    for base in (_api_public_base(), "http://127.0.0.1:8000", "http://localhost:8000"):
        prefix = f"{base.rstrip('/')}/"
        if s.startswith(prefix):
            s = s[len(prefix) :]
            break
    return s


def _is_new_file_upload(incoming: str) -> bool:
    """True if client sent a new Base64 / data-URL payload (not a stored path/URL echo)."""
    s = (incoming or "").strip()
    if not s:
        return False
    if s.startswith("data:"):
        return True
    n = _normalize_stored_ref(s)
    if n.startswith(UPLOAD_REL_PREFIX):
        return False
    if s.startswith("http://") or s.startswith("https://"):
        return False
    # long payload without path shape → treat as raw base64 upload
    return len(s) > 200


def _resolve_file_column_for_update(
    incoming: str | None, old_stored: str | None, property_id: str, kind: str
) -> str:
    """Decide stored path for one file column on update."""
    inc_raw = (incoming or "").strip()
    old = (old_stored or "").strip()

    if not inc_raw:
        return old

    if not _is_new_file_upload(inc_raw):
        old_n = _normalize_stored_ref(old)
        inc_n = _normalize_stored_ref(inc_raw)
        return old_n or (inc_n if inc_n.startswith(UPLOAD_REL_PREFIX) else old_n)

    new_rel = _save_upload_file(property_id, kind, inc_raw)
    if new_rel:
        _delete_file_if_path(_normalize_stored_ref(old))
        return new_rel
    return _normalize_stored_ref(old)


def _ingest_file_for_insert(raw: str, property_id: str, kind: str) -> str:
    """On new survey row: write uploads from Base64/data-URL; keep existing uploads/ path if echoed."""
    if not (raw or "").strip():
        return ""
    if _is_new_file_upload(raw):
        return _save_upload_file(property_id, kind, raw) or ""
    n = _normalize_stored_ref(raw)
    if n.startswith(UPLOAD_REL_PREFIX) and _safe_disk_path(n):
        return n
    return ""


def _path_to_response_url(stored: str) -> str:
    """For get-surveys JSON: expose files as absolute URLs for img/src and clients."""
    s = (stored or "").strip()
    if not s:
        return ""
    if s.startswith("data:") or (len(s) > 600 and not s.startswith(UPLOAD_REL_PREFIX)):
        return s
    if s.startswith(UPLOAD_REL_PREFIX):
        return f"{_api_public_base()}/{s}"
    if s.startswith("http://") or s.startswith("https://"):
        return s
    return s


def _parse_surveys_bbox(bbox: str | None) -> tuple[float, float, float, float] | None:
    if not (bbox or "").strip():
        return None
    parts = [p.strip() for p in bbox.split(",")]
    if len(parts) != 4:
        raise HTTPException(
            status_code=400,
            detail="bbox must be four comma-separated numbers: minLng,minLat,maxLng,maxLat",
        )
    try:
        min_lng, min_lat, max_lng, max_lat = (float(parts[0]), float(parts[1]), float(parts[2]), float(parts[3]))
    except ValueError as e:
        raise HTTPException(status_code=400, detail="bbox values must be valid numbers") from e
    if min_lng > max_lng or min_lat > max_lat:
        raise HTTPException(status_code=400, detail="bbox: minLng <= maxLng and minLat <= maxLat required")
    return (min_lng, min_lat, max_lng, max_lat)


def _legal_survey_row_to_geojson_feature(row) -> dict:
    """Map one legal_surveys row to a GeoJSON Feature (same column order as get-surveys SELECT)."""
    td = row[12] if row[12] is not None else 0
    sr = row[13] if row[13] is not None else 0
    fsd = row[14] if row[14] is not None else 0
    ov = row[15] if row[15] is not None else 0
    ad = row[16] if row[16] is not None else 0
    pn = row[17] if row[17] is not None else 0
    bk = row[18] if row[18] is not None else 0
    nt = row[19] if row[19] is not None else 0
    adf = _path_to_response_url(row[20] if row[20] is not None else "")
    pnf = _path_to_response_url(row[21] if row[21] is not None else "")
    bkf = _path_to_response_url(row[22] if row[22] is not None else "")
    ovf = _path_to_response_url(row[23] if row[23] is not None else "")
    smf = _path_to_response_url(row[24] if row[24] is not None else "")
    svf = _path_to_response_url(row[25] if row[25] is not None else "")
    photo_out = _path_to_response_url(row[8] if row[8] is not None else "")
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [row[10], row[9]]},
        "properties": {
            "dbId": row[0],
            "propertyId": row[1],
            "ownerName": row[2],
            "structureType": row[3],
            "acquisitionStage": row[4],
            "noticeSent": row[5],
            "moneyDistributed": row[6],
            "areaSqft": row[7],
            "photoB64": photo_out,
            "district": row[11],
            "totalDistribution": int(td),
            "samarpanReceipt": int(sr),
            "fieldSurveyDone": bool(fsd),
            "ownerVerification": bool(ov),
            "aadharCollected": bool(ad),
            "panCollected": bool(pn),
            "bankDetailsCollected": bool(bk),
            "numberOfTrees": int(nt),
            "aadharFileB64": adf,
            "panFileB64": pnf,
            "bankFileB64": bkf,
            "ownerVerifFileB64": ovf,
            "samarpanFileB64": smf,
            "surveyFileB64": svf,
        },
    }


# ==========================================
# DATABASE SETUP
# ==========================================
_thread_local = threading.local()

def get_db():
    if not hasattr(_thread_local, 'conn'):
        _thread_local.conn = sqlite3.connect('survey_data.db')
        _thread_local.cursor = _thread_local.conn.cursor()
    return _thread_local.conn, _thread_local.cursor


async def get_current_admin(
    authorization: str | None = Header(None),
    token: str | None = Query(
        default=None,
        description="JWT when Authorization cannot be sent (e.g. HTML img/iframe src)",
    ),
) -> str:
    raw: str | None = None
    if authorization:
        parts = authorization.split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            raw = parts[1].strip() or None
    if not raw and token:
        raw = token.strip() or None
    if not raw:
        raise HTTPException(
            status_code=401,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = jwt.decode(raw, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        username = payload.get("sub")
        if not username or not isinstance(username, str):
            raise HTTPException(status_code=401, detail="Invalid token")
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired") from None
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Invalid token") from None
    conn, cursor = get_db()
    cursor.execute("SELECT 1 FROM admin_users WHERE username = ? LIMIT 1", (username,))
    if not cursor.fetchone():
        raise HTTPException(status_code=401, detail="Invalid token")
    return username


CurrentAdmin = Annotated[str, Depends(get_current_admin)]

_init_conn = sqlite3.connect('survey_data.db')
_init_cursor = _init_conn.cursor()

_init_cursor.execute('''
    CREATE TABLE IF NOT EXISTS legal_surveys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id TEXT, owner_name TEXT, structure_type TEXT,
        acquisition_stage TEXT, notice_sent TEXT, money_distributed REAL,
        area_sqft REAL, photo_b64 TEXT, lat REAL, lng REAL,
        state TEXT, district TEXT, timestamp TEXT,
        total_distribution INTEGER DEFAULT 0,
        samarpan_receipt INTEGER DEFAULT 0,
        field_survey_done INTEGER DEFAULT 0,
        owner_verification INTEGER DEFAULT 0,
        aadhar_collected INTEGER DEFAULT 0,
        pan_collected INTEGER DEFAULT 0,
        bank_details_collected INTEGER DEFAULT 0,
        number_of_trees INTEGER DEFAULT 0,
        aadhar_file_b64 TEXT DEFAULT '',
        pan_file_b64 TEXT DEFAULT '',
        bank_file_b64 TEXT DEFAULT '',
        owner_verif_file_b64 TEXT DEFAULT '',
        samarpan_file_b64 TEXT DEFAULT '',
        survey_file_b64 TEXT DEFAULT ''
    )
''')

for _col, _typ in [
    ("total_distribution", "INTEGER DEFAULT 0"),
    ("samarpan_receipt", "INTEGER DEFAULT 0"),
    ("field_survey_done", "INTEGER DEFAULT 0"),
    ("owner_verification", "INTEGER DEFAULT 0"),
    ("aadhar_collected", "INTEGER DEFAULT 0"),
    ("pan_collected", "INTEGER DEFAULT 0"),
    ("bank_details_collected", "INTEGER DEFAULT 0"),
    ("number_of_trees", "INTEGER DEFAULT 0"),
    ("aadhar_file_b64", "TEXT DEFAULT ''"),
    ("pan_file_b64", "TEXT DEFAULT ''"),
    ("bank_file_b64", "TEXT DEFAULT ''"),
    ("owner_verif_file_b64", "TEXT DEFAULT ''"),
    ("samarpan_file_b64", "TEXT DEFAULT ''"),
    ("survey_file_b64", "TEXT DEFAULT ''"),
]:
    try:
        _init_cursor.execute(f"ALTER TABLE legal_surveys ADD COLUMN {_col} {_typ}")
    except sqlite3.OperationalError:
        pass

try:
    _init_cursor.execute("ALTER TABLE legal_surveys RENAME COLUMN legal_status TO acquisition_stage")
except sqlite3.OperationalError:
    pass

_init_cursor.execute('''
    CREATE TABLE IF NOT EXISTS spatial_shapes_v2 (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        property_id TEXT,
        structure_type TEXT,
        geojson_data TEXT,
        calculated_area REAL,
        timestamp TEXT
    )
''')

_init_cursor.execute('''
    CREATE TABLE IF NOT EXISTS admin_users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT NOT NULL UNIQUE,
        password_hash TEXT NOT NULL
    )
''')
_init_cursor.execute("SELECT 1 FROM admin_users WHERE username = ?", ("admin",))
if _init_cursor.fetchone() is None:
    _default_pw = "securepassword123"
    _hashed = bcrypt.hashpw(_default_pw.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    _init_cursor.execute(
        "INSERT INTO admin_users (username, password_hash) VALUES (?, ?)",
        ("admin", _hashed),
    )

_init_conn.commit()
_init_conn.close()

def _to_sql_int_bool(v) -> int:
    if v is True:
        return 1
    if v is False or v is None:
        return 0
    if isinstance(v, int):
        return 1 if v else 0
    return 1 if v else 0

def _samarpan_to_int(v) -> int:
    if isinstance(v, bool):
        return 1 if v else 0
    if isinstance(v, int):
        return max(0, v)
    return 0

def _is_import_pending_property_id(property_id: str | None) -> bool:
    if property_id is None:
        return False
    s = str(property_id).strip()
    return s.upper().startswith("PENDING_")

def _sync_property_area(cursor, property_id: str):
    if not property_id or _is_import_pending_property_id(property_id):
        return
    cursor.execute("SELECT COALESCE(SUM(calculated_area), 0) FROM spatial_shapes_v2 WHERE property_id=?", (property_id,))
    total_area = cursor.fetchone()[0] or 0
    cursor.execute("UPDATE legal_surveys SET area_sqft=? WHERE property_id=?", (float(total_area), property_id))

def _sync_property_structures(cursor, property_id: str):
    if not property_id or _is_import_pending_property_id(property_id):
        return
    cursor.execute('''
        SELECT DISTINCT structure_type
        FROM spatial_shapes_v2
        WHERE property_id=? AND COALESCE(TRIM(structure_type), '') <> ''
    ''', (property_id,))
    rows = cursor.fetchall()
    structures = sorted({(r[0] or '').strip() for r in rows if r and r[0]})
    merged = ", ".join(structures)
    cursor.execute("UPDATE legal_surveys SET structure_type=? WHERE property_id=?", (merged, property_id))

def _sync_property_summary(cursor, property_id: str):
    _sync_property_area(cursor, property_id)
    _sync_property_structures(cursor, property_id)

class SurveyData(BaseModel):
    propertyId: str
    ownerName: str
    structureType: str
    acquisitionStage: str
    noticeSent: str
    moneyDistributed: float
    areaSqft: float
    photoB64: str = ""
    state: str
    district: str
    coordinates: dict
    totalDistribution: int = 0
    samarpanReceipt: int | bool = 0
    fieldSurveyDone: bool = False
    ownerVerification: bool = False
    aadharCollected: bool = False
    panCollected: bool = False
    bankDetailsCollected: bool = False
    numberOfTrees: int = 0
    aadharFileB64: str = ""
    panFileB64: str = ""
    bankFileB64: str = ""
    ownerVerifFileB64: str = ""
    samarpanFileB64: str = ""
    surveyFileB64: str = ""

class UpdateSurveyData(BaseModel):
    id: int
    propertyId: str
    ownerName: str
    structureType: str
    acquisitionStage: str
    noticeSent: str
    moneyDistributed: float
    areaSqft: float
    photoB64: str = ""
    lat: float = None
    lng: float = None
    state: str = ""
    district: str = ""
    totalDistribution: int = 0
    samarpanReceipt: int | bool = 0
    fieldSurveyDone: bool = False
    ownerVerification: bool = False
    aadharCollected: bool = False
    panCollected: bool = False
    bankDetailsCollected: bool = False
    numberOfTrees: int = 0
    aadharFileB64: str = ""
    panFileB64: str = ""
    bankFileB64: str = ""
    ownerVerifFileB64: str = ""
    samarpanFileB64: str = ""
    surveyFileB64: str = ""

class ShapeData(BaseModel):
    propertyId: str
    structureType: str
    geoJson: str
    calculatedArea: float

class UpdateShapeData(BaseModel):
    id: int
    propertyId: str
    structureType: str
    geoJson: str = ""
    calculatedArea: float = None

class UpdateShapeRequest(BaseModel):
    oldPropertyId: str
    newPropertyId: str
    structureType: str
    calculatedArea: float | None = None


class AdminLogin(BaseModel):
    username: str
    password: str


@app.post("/api/login")
def admin_login(body: AdminLogin):
    conn, cursor = get_db()
    cursor.execute(
        "SELECT id, password_hash FROM admin_users WHERE username = ?",
        (body.username.strip(),),
    )
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    _stored_hash = row[1] if isinstance(row[1], str) else str(row[1])
    _ok = bcrypt.checkpw(
        body.password.encode("utf-8"),
        _stored_hash.encode("utf-8"),
    )
    if not _ok:
        raise HTTPException(status_code=401, detail="Incorrect username or password")
    expire = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(
        hours=ACCESS_TOKEN_EXPIRE_HOURS
    )
    token = jwt.encode(
        {"sub": body.username.strip(), "exp": expire},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    if isinstance(token, bytes):
        token = token.decode("ascii")
    return {"access_token": token, "token_type": "bearer"}


# ==========================================
# API ROUTES (POINTS)
# ==========================================
@app.post("/save-survey")
def save_survey(data: SurveyData, _admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        pid = data.propertyId
        photo_path = _ingest_file_for_insert(data.photoB64, pid, "photo")
        aadhar_path = _ingest_file_for_insert(data.aadharFileB64, pid, "aadhar")
        pan_path = _ingest_file_for_insert(data.panFileB64, pid, "pan")
        bank_path = _ingest_file_for_insert(data.bankFileB64, pid, "bank")
        ov_path = _ingest_file_for_insert(data.ownerVerifFileB64, pid, "owner_verif")
        sm_path = _ingest_file_for_insert(data.samarpanFileB64, pid, "samarpan")
        sv_path = _ingest_file_for_insert(data.surveyFileB64, pid, "survey")

        cursor.execute('''
            INSERT INTO legal_surveys (property_id, owner_name, structure_type, acquisition_stage, notice_sent, money_distributed, area_sqft, photo_b64, lat, lng, state, district, timestamp,
            total_distribution, samarpan_receipt, field_survey_done, owner_verification, aadhar_collected, pan_collected, bank_details_collected, number_of_trees,
            aadhar_file_b64, pan_file_b64, bank_file_b64, owner_verif_file_b64, samarpan_file_b64, survey_file_b64)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            data.propertyId, data.ownerName, data.structureType, data.acquisitionStage, data.noticeSent, data.moneyDistributed, data.areaSqft, photo_path,
            data.coordinates['lat'], data.coordinates['lng'], data.state, data.district, datetime.datetime.now().isoformat(),
            int(data.totalDistribution or 0), _samarpan_to_int(data.samarpanReceipt),
            _to_sql_int_bool(data.fieldSurveyDone), _to_sql_int_bool(data.ownerVerification),
            _to_sql_int_bool(data.aadharCollected), _to_sql_int_bool(data.panCollected),
            _to_sql_int_bool(data.bankDetailsCollected), int(data.numberOfTrees or 0),
            aadhar_path, pan_path, bank_path, ov_path, sm_path, sv_path,
        ))
        conn.commit()
        return {"message": "Legal Survey Saved."}
    except Exception as e:
        print(f"Error saving survey: {e}")
        raise HTTPException(status_code=500, detail="Failed to save survey data")

@app.post("/delete-survey")
def delete_survey(data: dict, _admin: CurrentAdmin):
    try:
        sid = int(data.get("id", 0))
        if sid <= 0:
            raise HTTPException(status_code=400, detail="Invalid survey id")
        conn, cursor = get_db()
        cursor.execute(
            "SELECT property_id, photo_b64, aadhar_file_b64, pan_file_b64, bank_file_b64, owner_verif_file_b64, samarpan_file_b64, survey_file_b64 FROM legal_surveys WHERE id=?",
            (sid,),
        )
        row = cursor.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Survey not found")
        property_id = row[0]
        for p in row[1:]:
            _delete_file_if_path(p or "")
        cursor.execute("DELETE FROM spatial_shapes_v2 WHERE property_id=?", (property_id,))
        cursor.execute("DELETE FROM legal_surveys WHERE id=?", (sid,))
        conn.commit()
        return {"message": "Survey and related boundaries deleted."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting survey: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete survey")

@app.post("/update-survey")
def update_survey(data: UpdateSurveyData, _admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        cursor.execute(
            """SELECT photo_b64, aadhar_file_b64, pan_file_b64, bank_file_b64, owner_verif_file_b64, samarpan_file_b64, survey_file_b64
               FROM legal_surveys WHERE id=?""",
            (data.id,),
        )
        old_row = cursor.fetchone()
        if not old_row:
            raise HTTPException(status_code=404, detail="Survey not found")

        old_photo, old_aadhar, old_pan, old_bank, old_ov, old_sm, old_sv = old_row
        pid = data.propertyId

        photo_new = _resolve_file_column_for_update(data.photoB64, old_photo, pid, "photo")
        aadhar_new = _resolve_file_column_for_update(data.aadharFileB64, old_aadhar, pid, "aadhar")
        pan_new = _resolve_file_column_for_update(data.panFileB64, old_pan, pid, "pan")
        bank_new = _resolve_file_column_for_update(data.bankFileB64, old_bank, pid, "bank")
        ov_new = _resolve_file_column_for_update(data.ownerVerifFileB64, old_ov, pid, "owner_verif")
        sm_new = _resolve_file_column_for_update(data.samarpanFileB64, old_sm, pid, "samarpan")
        sv_new = _resolve_file_column_for_update(data.surveyFileB64, old_sv, pid, "survey")

        ext = (
            int(data.totalDistribution or 0), _samarpan_to_int(data.samarpanReceipt),
            _to_sql_int_bool(data.fieldSurveyDone), _to_sql_int_bool(data.ownerVerification),
            _to_sql_int_bool(data.aadharCollected), _to_sql_int_bool(data.panCollected),
            _to_sql_int_bool(data.bankDetailsCollected), int(data.numberOfTrees or 0),
            aadhar_new, pan_new, bank_new, ov_new, sm_new, sv_new,
        )

        cursor.execute('''
            UPDATE legal_surveys SET property_id=?, owner_name=?, structure_type=?, acquisition_stage=?, notice_sent=?, money_distributed=?, area_sqft=?, photo_b64=?,
            lat=COALESCE(?, lat), lng=COALESCE(?, lng), state=COALESCE(NULLIF(?, ''), state), district=COALESCE(NULLIF(?, ''), district),
            total_distribution=?, samarpan_receipt=?, field_survey_done=?, owner_verification=?, aadhar_collected=?, pan_collected=?, bank_details_collected=?, number_of_trees=?,
            aadhar_file_b64=?, pan_file_b64=?, bank_file_b64=?, owner_verif_file_b64=?, samarpan_file_b64=?, survey_file_b64=?
            WHERE id=?
        ''', (
            data.propertyId, data.ownerName, data.structureType, data.acquisitionStage, data.noticeSent, data.moneyDistributed, data.areaSqft, photo_new,
            data.lat, data.lng, data.state, data.district, *ext, data.id,
        ))
        conn.commit()
        return {"message": "Survey Updated."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating survey: {e}")
        raise HTTPException(status_code=500, detail="Failed to update survey data")

_GET_SURVEYS_SELECT = """
        SELECT id, property_id, owner_name, structure_type, acquisition_stage, notice_sent, money_distributed, area_sqft, photo_b64, lat, lng, district,
        total_distribution, samarpan_receipt, field_survey_done, owner_verification, aadhar_collected, pan_collected, bank_details_collected, number_of_trees,
        aadhar_file_b64, pan_file_b64, bank_file_b64, owner_verif_file_b64, samarpan_file_b64, survey_file_b64
        FROM legal_surveys
"""


@app.get("/get-surveys")
def get_surveys(
    _admin: CurrentAdmin,
    limit: int = Query(default=500, ge=1, le=2000, description="Page size (max 2000)"),
    offset: int = Query(default=0, ge=0, description="Rows to skip, for pagination"),
    bbox: str | None = Query(
        default=None,
        description="Optional geographic filter: minLng,minLat,maxLng,maxLat (WGS84)",
    ),
):
    page_size = min(limit, 2000)
    fetch_n = page_size + 1
    bbox_t = _parse_surveys_bbox(bbox)
    conn, cursor = get_db()
    order_limit = " ORDER BY id LIMIT ? OFFSET ?"
    if bbox_t:
        min_lng, min_lat, max_lng, max_lat = bbox_t
        sql = (
            _GET_SURVEYS_SELECT
            + """ WHERE lng IS NOT NULL AND lat IS NOT NULL
            AND lng >= ? AND lng <= ? AND lat >= ? AND lat <= ?"""
            + order_limit
        )
        cursor.execute(sql, (min_lng, max_lng, min_lat, max_lat, fetch_n, offset))
    else:
        cursor.execute(_GET_SURVEYS_SELECT + order_limit, (fetch_n, offset))
    rows = cursor.fetchall()
    has_more = len(rows) > page_size
    if has_more:
        rows = rows[:page_size]
    features = [_legal_survey_row_to_geojson_feature(r) for r in rows]
    return {
        "type": "FeatureCollection",
        "features": features,
        "limit": page_size,
        "offset": offset,
        "hasMore": has_more,
    }

# ==========================================
# API ROUTES (SHAPES)
# ==========================================
@app.post("/save-shape")
def save_shape(data: ShapeData, _admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        cursor.execute('''
            INSERT INTO spatial_shapes_v2 (property_id, structure_type, geojson_data, calculated_area, timestamp)
            VALUES (?, ?, ?, ?, ?)
        ''', (data.propertyId, data.structureType, data.geoJson, data.calculatedArea, datetime.datetime.now().isoformat()))
        if not _is_import_pending_property_id(data.propertyId):
            _sync_property_summary(cursor, data.propertyId)
        conn.commit()
        return {"message": "Plot geometry saved."}
    except Exception as e:
        print(f"Error saving shape: {e}")
        raise HTTPException(status_code=500, detail="Failed to save shape data")

@app.post("/update-shape")
def update_shape(data: UpdateShapeData, _admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        cursor.execute("SELECT property_id FROM spatial_shapes_v2 WHERE id=?", (data.id,))
        old_row = cursor.fetchone()
        old_property_id = old_row[0] if old_row else None
        if data.geoJson and data.calculatedArea is not None:
            cursor.execute('''
                UPDATE spatial_shapes_v2
                SET property_id=?, structure_type=?, geojson_data=?, calculated_area=?
                WHERE id=?
            ''', (data.propertyId, data.structureType, data.geoJson, data.calculatedArea, data.id))
        else:
            cursor.execute('''
                UPDATE spatial_shapes_v2
                SET property_id=?, structure_type=?
                WHERE id=?
            ''', (data.propertyId, data.structureType, data.id))
        if not _is_import_pending_property_id(data.propertyId):
            _sync_property_summary(cursor, data.propertyId)
        if old_property_id and old_property_id != data.propertyId and not _is_import_pending_property_id(old_property_id):
            _sync_property_summary(cursor, old_property_id)
        conn.commit()
        return {"message": "Shape updated."}
    except Exception as e:
        print(f"Error updating shape: {e}")
        raise HTTPException(status_code=500, detail="Failed to update shape data")

@app.post("/update-shape-assignment")
def update_shape_assignment(data: UpdateShapeRequest, _admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        cursor.execute("SELECT 1 FROM legal_surveys WHERE property_id=? LIMIT 1", (data.newPropertyId,))
        if not cursor.fetchone():
            raise HTTPException(status_code=404, detail="No ID found in legal_surveys. Please create survey first.")

        cursor.execute(
            "UPDATE spatial_shapes_v2 SET property_id = ?, structure_type = ?, calculated_area = COALESCE(?, calculated_area) WHERE property_id = ?",
            (data.newPropertyId, data.structureType, data.calculatedArea, data.oldPropertyId),
        )

        if cursor.rowcount <= 0:
            raise HTTPException(status_code=404, detail="No matching shape found for assignment update")

        _sync_property_summary(cursor, data.newPropertyId)
        _sync_property_summary(cursor, data.oldPropertyId)
        conn.commit()
        return {"message": "Shape assignment updated.", "updatedRows": cursor.rowcount}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error updating shape assignment: {e}")
        raise HTTPException(status_code=500, detail="Failed to update shape assignment")

@app.post("/delete-shape")
def delete_shape(data: dict, _admin: CurrentAdmin):
    try:
        sid = int(data.get("id", 0))
        if sid <= 0:
            raise HTTPException(status_code=400, detail="Invalid shape id")
        conn, cursor = get_db()
        cursor.execute("SELECT property_id FROM spatial_shapes_v2 WHERE id=?", (sid,))
        row = cursor.fetchone()
        old_property_id = row[0] if row else None
        cursor.execute("DELETE FROM spatial_shapes_v2 WHERE id=?", (sid,))
        if old_property_id and not _is_import_pending_property_id(old_property_id):
            _sync_property_summary(cursor, old_property_id)
        conn.commit()
        return {"message": "Shape deleted."}
    except HTTPException:
        raise
    except Exception as e:
        print(f"Error deleting shape: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete shape")

@app.get("/get-shapes")
def get_shapes(_admin: CurrentAdmin):
    try:
        conn, cursor = get_db()
        cursor.execute("SELECT id, property_id, structure_type, geojson_data, calculated_area FROM spatial_shapes_v2")
        rows = cursor.fetchall()
        shapes = [{"id": r[0], "propertyId": r[1], "structureType": r[2], "geoJson": json.loads(r[3]), "area": r[4]} for r in rows]
        return shapes
    except Exception as e:
        print(f"Error getting shapes: {e}")
        raise HTTPException(status_code=500, detail="Failed to retrieve shapes")

@app.get("/document/{property_id}/{doc_type}")
def get_document(property_id: str, doc_type: str, _admin: CurrentAdmin):
    doc_map = {
        "aadhar": ("aadhar_file_b64", "aadhar"),
        "pan": ("pan_file_b64", "pan"),
        "bank": ("bank_file_b64", "bank"),
        "owner_verification": ("owner_verif_file_b64", "owner_verification"),
        "samarpan": ("samarpan_file_b64", "samarpan"),
        "survey": ("survey_file_b64", "survey"),
        "photo": ("photo_b64", "photo"),
    }

    if doc_type not in doc_map:
        raise HTTPException(status_code=400, detail="Invalid document type")

    column_name, filename_prefix = doc_map[doc_type]
    conn, cursor = get_db()
    cursor.execute(
        f"SELECT {column_name} FROM legal_surveys WHERE property_id=? ORDER BY id DESC LIMIT 1",
        (property_id,),
    )
    row = cursor.fetchone()
    if not row or not row[0]:
        raise HTTPException(status_code=404, detail="Document not found")

    raw = str(row[0]).strip()
    if not raw:
        raise HTTPException(status_code=404, detail="Document not found")

    disk_path = _safe_disk_path(raw)
    if disk_path is not None:
        media_type, _ = mimetypes.guess_type(str(disk_path))
        if not media_type:
            media_type = "application/octet-stream"
        ext = disk_path.suffix.lstrip(".") or "bin"
        filename = f"{filename_prefix}_{property_id}.{ext}"
        headers = {"Content-Disposition": f'inline; filename="{filename}"'}
        return FileResponse(str(disk_path), media_type=media_type, filename=filename, headers=headers)

    if len(raw) < 100:
        raise HTTPException(status_code=404, detail="Document not found")

    media_type = "application/octet-stream"
    ext = "bin"
    payload = raw
    if raw.startswith("data:"):
        try:
            header, payload = raw.split(",", 1)
            media_type = header.split(";")[0].split(":", 1)[1] or media_type
        except Exception:
            payload = raw

    if media_type.startswith("image/"):
        ext = media_type.split("/", 1)[1] or "jpg"
    elif media_type == "application/pdf":
        ext = "pdf"

    try:
        file_bytes = base64.b64decode(payload, validate=False)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=400, detail="Invalid document encoding")

    filename = f"{filename_prefix}_{property_id}.{ext}"
    headers = {"Content-Disposition": f'inline; filename="{filename}"'}
    return Response(content=file_bytes, media_type=media_type, headers=headers)

@app.post("/notify-dashboard")
def notify_dashboard(data: dict, _admin: CurrentAdmin):
    return {"message": "Dashboard notification received", "data": data}

@app.get("/")
def serve_form(): return FileResponse("index.html")

@app.get("/dashboard")
def serve_dashboard(): return FileResponse("dashboard.html")

app.mount("/uploads", StaticFiles(directory=str(UPLOAD_DIR)), name="uploads")
app.mount("/", StaticFiles(directory="."), name="static")
