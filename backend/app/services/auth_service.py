import os
import sys
import math
import traceback
import subprocess
import shutil
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
import json
import logging
import uuid
import struct
import base64
import asyncio
import hashlib
import time

import numpy as np
from fastapi import Request, HTTPException, Depends
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response

from app.core.config import *
from app.models.auth import *
from app.models.projects import *
from app.models.datasets import *
from app.models.admin import *
from app.models.pointclouds import *
from app.models.spatial import *
from app.models.analysis import *
from app.models.issues import *
from app.models.misc import *

from app.services.catalog_service import mirror_processing_job, delete_asset_artifacts, upsert_asset, bump_revision
from app.services.raster import convert_tif_to_cog
from app.core.database import get_db_connection, get_db

# Deferred imports
def _get_project_dirs(*args, **kwargs):
    from app.main import get_project_dirs
    return get_project_dirs(*args, **kwargs)

def _read_dataset_status(*args, **kwargs):
    from app.main import _read_dataset_status
    return _read_dataset_status(*args, **kwargs)


def _send_owner_sms(message: str) -> None:
    sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_FROM_NUMBER", "").strip()
    to_number = os.getenv("ADMIN_ALERT_PHONE", ADMIN_ALERT_PHONE).strip()
    if not (sid and token and from_number and to_number):
        print(f"[SMS pending configuration] {message}")
        return
    payload = urlencode({"From": from_number, "To": to_number, "Body": message}).encode("utf-8")
    req = UrlRequest(
        f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json",
        data=payload,
        method="POST",
    )
    auth = base64.b64encode(f"{sid}:{token}".encode("utf-8")).decode("ascii")
    req.add_header("Authorization", f"Basic {auth}")
    try:
        with urlopen(req, timeout=8) as response:
            response.read()
    except URLError as exc:
        print(f"SMS send failed: {exc}")

def _send_email(to_email: str, subject: str, body: str) -> None:
    host = os.getenv("SMTP_HOST", "").strip()
    port = int(os.getenv("SMTP_PORT", "587"))
    username = os.getenv("SMTP_USERNAME", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip().replace(" ", "")
    from_email = os.getenv("SMTP_FROM_EMAIL", username or OWNER_APPROVAL_EMAIL).strip()
    if not (host and from_email):
        print(f"[Email pending configuration] To: {to_email}\nSubject: {subject}\n{body}")
        return
    msg = EmailMessage()
    msg["From"] = from_email
    msg["To"] = to_email
    msg["Subject"] = subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(host, port, timeout=10) as server:
            server.starttls()
            if username and password:
                server.login(username, password)
            server.send_message(msg)
    except OSError as exc:
        print(f"Email send failed: {exc}")

def _approval_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/approvals/approve?token={quote(token)}"

def _create_pending_user(email: str, password: str, requested_role: str, request: Request) -> dict[str, str]:
    normalized_email = email.strip().lower()
    if "@" not in normalized_email:
        raise HTTPException(status_code=400, detail="Invalid email")
    role = "admin" if requested_role == "admin" else "user"
    password_hash = _hash_password(password)
    created_at = _now_iso()
    approval_token = secrets.token_urlsafe(40)
    approval_hash = _token_hash(approval_token)
    try:
        with get_db_connection() as connection:
            connection.execute(
                """
                INSERT INTO users (
                    email, password_hash, created_at, role, approval_status,
                    requested_role, approval_token_hash
                )
                VALUES (?, ?, ?, 'user', 'pending', ?, ?)
                """,
                (normalized_email, password_hash, created_at, role, approval_hash),
            )
            connection.commit()
    except sqlite3.IntegrityError as exc:
        raise HTTPException(status_code=409, detail="Email already registered") from exc

    approve_link = _approval_url(request, approval_token)
    _send_email(
        OWNER_APPROVAL_EMAIL,
        f"Droid Cloud approval request: {normalized_email}",
        (
            f"New {role} access request for Droid Cloud.\n\n"
            f"Email: {normalized_email}\n"
            f"Requested role: {role}\n"
            f"Approve here: {approve_link}\n\n"
            "Only approve this request if you recognize the person."
        ),
    )
    _send_owner_sms(f"Droid Cloud approval request: {normalized_email} requested {role} access.")
    return {"status": "pending", "email": normalized_email, "requested_role": role}
