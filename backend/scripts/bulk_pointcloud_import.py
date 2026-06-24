"""
Bulk-import LAS/LAZ files from a local folder into a project and run COPC conversion.

Usage (from repo root, backend venv active):
  python backend/scripts/bulk_pointcloud_import.py --project-id proj_abc123 --source "D:/bulk_las"

After conversion finishes, open Data Catalog and click "Sync Manual Folders" if needed.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import secrets
import shutil
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None

if load_dotenv is not None:
    load_dotenv(BACKEND_DIR / ".env", override=False)
    load_dotenv(REPO_ROOT / ".env", override=False)

from app.main import (  # noqa: E402
    LOCAL_DATA_PATH,
    _ept_dataset_dir,
    _safe_pointcloud_basename,
    _safe_project_id,
    get_project_dirs,
    process_pointcloud_ept_job,
)


def _content_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(8 * 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _dataset_id_for_file(file_name: str, content_hash: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(file_name).stem).strip("-") or "cloud"
    return f"{stem[:40]}-{content_hash[:12]}"


def import_one(project_id: str, source_file: Path, *, dry_run: bool) -> None:
    safe_project_id = _safe_project_id(project_id)
    safe_name = _safe_pointcloud_basename(source_file.name)
    raw_dir, _ = get_project_dirs(safe_project_id)
    raw_target = raw_dir / f"{safe_project_id}__{safe_name}"
    content_hash = _content_hash(source_file)
    dataset_id = _dataset_id_for_file(safe_name, content_hash)
    output_dir = _ept_dataset_dir(safe_project_id, dataset_id)

    print(f"\n==> {source_file.name}")
    print(f"    raw:    {raw_target}")
    print(f"    copc:   {output_dir / 'output.copc.laz'}")

    if dry_run:
        return

    raw_dir.mkdir(parents=True, exist_ok=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_file, raw_target)
    try:
        (output_dir / ".source_name.txt").write_text(safe_name, encoding="utf-8")
        (output_dir / ".source_hash.txt").write_text(content_hash, encoding="utf-8")
    except OSError:
        pass

    process_pointcloud_ept_job(
        str(raw_target),
        str(output_dir),
        dataset_id,
        safe_project_id,
        dataset_id,
        safe_name,
        content_hash,
    )

    copc_file = output_dir / "output.copc.laz"
    if copc_file.is_file():
        print(f"    OK  COPC ready ({copc_file.stat().st_size // (1024 * 1024)} MB)")
    else:
        err_file = output_dir / ".conversion_error.txt"
        detail = err_file.read_text(encoding="utf-8").strip() if err_file.is_file() else "unknown error"
        print(f"    FAIL  {detail[:400]}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Bulk LAS/LAZ -> COPC import for Droid Cloud Portal")
    parser.add_argument("--project-id", required=True, help="Project id, e.g. proj_0c7e667eba610b2a")
    parser.add_argument("--source", required=True, help="Folder containing .las / .laz files")
    parser.add_argument("--dry-run", action="store_true", help="Show planned paths only")
    args = parser.parse_args()

    source_dir = Path(args.source).expanduser().resolve()
    if not source_dir.is_dir():
        print(f"Source folder not found: {source_dir}", file=sys.stderr)
        return 1

    files = sorted(
        [path for path in source_dir.iterdir() if path.is_file() and path.suffix.lower() in {".las", ".laz"}],
        key=lambda path: path.name.lower(),
    )
    if not files:
        print(f"No LAS/LAZ files found in {source_dir}", file=sys.stderr)
        return 1

    print(f"Project data root: {LOCAL_DATA_PATH}")
    print(f"Project: {args.project_id}")
    print(f"Files:   {len(files)}")

    for source_file in files:
        import_one(args.project_id, source_file, dry_run=args.dry_run)

    if not args.dry_run:
        print("\nDone. Open Data Catalog -> Sync Manual Folders if entries are missing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
