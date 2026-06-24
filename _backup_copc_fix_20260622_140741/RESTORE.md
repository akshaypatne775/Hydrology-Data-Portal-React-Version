# COPC Fix Backup — Restore Instructions

Created: 2026-06-22

## What was backed up

- `main.py` — backend before COPC CORS + PDAL writer fix
- `.env` — dev env before POTREE_NATIVE_COPC_ENABLED / PDAL_EXE
- `.env.example`
- `index.html` — droid-ept-viewer before copc.js + loader fix
- `potree_build/` — full old Potree 1.8 build folder

## Restore (if COPC fix causes problems)

From PowerShell, set paths:

```powershell
$backup = "D:\1_Portal_Workflows_development\Droid Survair Cloud Portal\_backup_copc_fix_20260622_140741"
$root = "D:\1_Portal_Workflows_development\Droid Survair Cloud Portal"
```

1. Restore backend files:
```powershell
Copy-Item "$backup\main.py" "$root\backend\app\main.py" -Force
Copy-Item "$backup\.env" "$root\backend\.env" -Force
Copy-Item "$backup\.env.example" "$root\backend\.env.example" -Force
```

2. Restore viewer HTML:
```powershell
Copy-Item "$backup\index.html" "$root\frontend\public\droid-ept-viewer\index.html" -Force
```

3. Restore old Potree build:
```powershell
robocopy "$backup\potree_build" "$root\frontend\public\droid-ept-viewer\build\potree" /E /MIR
```

4. Remove added copc lib (optional):
```powershell
Remove-Item "$root\frontend\public\droid-ept-viewer\libs\copc" -Recurse -Force -ErrorAction SilentlyContinue
```

5. Restart **dev** backend only (`1_Start_Dev_Environment.bat`).

## Delete backup when no longer needed

```powershell
Remove-Item "D:\1_Portal_Workflows_development\Droid Survair Cloud Portal\_backup_copc_fix_20260622_140741" -Recurse -Force
```
