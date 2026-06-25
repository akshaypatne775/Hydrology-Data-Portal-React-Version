import ast
import json

source_path = "app/main.py"
with open(source_path, "r", encoding="utf-8") as f:
    source_lines = f.read().splitlines()

tree = ast.parse("\n".join(source_lines))

routes = []

for node in tree.body:
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        if getattr(node, "decorator_list", None):
            for dec in node.decorator_list:
                # look for @app.get(...), @app.post(...), etc
                if isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute):
                    if getattr(dec.func.value, "id", "") == "app":
                        path = "UNKNOWN"
                        if dec.args and isinstance(dec.args[0], ast.Constant):
                            path = dec.args[0].value
                        routes.append((path, node.name))

# Group by first prefix
groups = {}
for path, name in routes:
    if path.startswith("/api/admin/users"):
        key = "admin_users"
    elif path.startswith("/api/admin/projects") or path.startswith("/api/admin/override/project") or "/admin/resync" in path or "cleanup-stale" in path:
        key = "admin_projects"
    elif "locate-folder" in path or "bulk-import" in path:
        key = "admin_import"
    elif path.startswith("/api/admin/catalog") or "admin/dataset" in path:
        key = "admin_catalog"
    elif path.startswith("/api/auth") or path in ["/api/login", "/api/logout", "/api/me", "/api/request-admin", "/api/approvals/approve"]:
        key = "auth"
    elif path.startswith("/api/projects") and "spatial" not in path:
        key = "projects"
    elif path.startswith("/api/media"):
        key = "media"
    elif path.startswith("/api/issues"):
        key = "issues"
    elif "spatial" in path:
        key = "spatial"
    elif "upload" in path:
        key = "uploads"
    elif path.startswith("/api/ortho") or path.startswith("/api/dji-terra"):
        key = "raster_tiles"
    elif "analysis" in path or "compare" in path:
        key = "analysis"
    elif path.startswith("/api/jobs"):
        key = "jobs"
    elif path.startswith("/api/proxy"):
        key = "proxy"
    elif "pointcloud" in path:
        key = "pointclouds"
    elif "dataset" in path or "contours" in path or "crop-mask" in path or "grid-export" in path or "bounds" in path:
        key = "datasets"
    elif path.startswith("/api/data") or "/data/" in path or "/tiles/" in path or "report" in path or "file" in path or "catalog-revision" in path:
        key = "files"
    elif path in ["/health", "/api/version", "/api/client-error-log"]:
        key = "system"
    else:
        key = "UNMAPPED"
        
    if key not in groups:
        groups[key] = []
    groups[key].append(name)

for k, v in groups.items():
    print(f"'{k}': {json.dumps(v)},")

print(f"\nTotal routes: {len(routes)}")
