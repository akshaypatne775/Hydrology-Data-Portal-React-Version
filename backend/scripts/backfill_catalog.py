"""One-time DEV backfill: import disk catalog rows into PostgreSQL catalog_assets."""

from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

os.environ.setdefault("CATALOG_DB_ENABLED", "true")

from app.core.database import ensure_tables  # noqa: E402
from app.services import catalog_service  # noqa: E402


def main() -> int:
    ensure_tables()
    project_root = Path(os.getenv("LOCAL_DATA_PATH", BACKEND_ROOT.parent / "Project_Data"))
    projects_dir = project_root / "projects"
    if not projects_dir.is_dir():
        print(f"No projects directory found at {projects_dir}")
        return 1
    total = 0
    for project_dir in sorted(projects_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        project_id = project_dir.name
        if catalog_service.asset_count(project_id) > 0:
            print(f"skip {project_id}: already has {catalog_service.asset_count(project_id)} assets")
            continue
        print(f"project {project_id}: run reconcile via API or open catalog once in UI")
        total += 1
    print(f"Projects needing first catalog scan: {total}")
    print("Open each project Data Catalog in DEV, or POST /api/admin/catalog/{project_id}/reconcile")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
