@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "PROD_DB=%PROJECT_DATA_DIR%\droid_cloud_prod.db"
set "DEV_DB=%PROJECT_DATA_DIR%\droid_cloud_dev.db"
set "LEGACY_DB=%PROJECT_DATA_DIR%\issues.db"
popd

color 0B
title Droid Cloud - Dev Environment Manager

echo.
echo  ================================================================
echo                 DROID CLOUD DEV ENVIRONMENT
echo  ================================================================
echo.

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend folder not found: "%BACKEND_DIR%"
  pause
  exit /b 1
)

if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend folder not found: "%FRONTEND_DIR%"
  pause
  exit /b 1
)

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend venv not found.
  echo [FIX] Run the project setup first.
  pause
  exit /b 1
)

if not exist "%PROJECT_DATA_DIR%" mkdir "%PROJECT_DATA_DIR%"

if not exist "%PROD_DB%" if exist "%LEGACY_DB%" (
  echo [INFO] First-time migration: copying legacy issues.db to droid_cloud_prod.db...
  copy /Y "%LEGACY_DB%" "%PROD_DB%" >nul
)

if exist "%PROD_DB%" (
  echo [INFO] Creating fresh dev DB snapshot from live droid_cloud_prod.db...
  "%PYTHON_EXE%" -c "import pathlib, sqlite3; src=pathlib.Path(r'%PROD_DB%'); dst=pathlib.Path(r'%DEV_DB%'); dst.parent.mkdir(parents=True, exist_ok=True); s=sqlite3.connect(str(src)); d=sqlite3.connect(str(dst)); s.backup(d); s.close(); d.close(); print('Dev DB snapshot ready:', dst)"
  if errorlevel 1 (
    echo [ERROR] Failed to snapshot live DB into dev DB.
    pause
    exit /b 1
  )
) else (
  echo [WARN] Live DB not found. Dev backend will create a fresh droid_cloud_dev.db.
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  pause
  exit /b 1
)

echo [INFO] Starting Dev FastAPI backend on http://127.0.0.1:8001
start "Droid Dev Backend 8001" "%ComSpec%" /k call "%~dp0_Run_Dev_Backend_8001.bat"

echo [INFO] Starting Dev React frontend on http://localhost:5173
start "Droid Dev Frontend 5173" /D "%FRONTEND_DIR%" "%ComSpec%" /k "set VITE_BACKEND_PORT=8001&&call npm run dev"

echo.
echo [READY] Dev world started.
echo        Backend:  127.0.0.1:8001  DB: Project_Data\droid_cloud_dev.db
echo        Frontend: localhost:5173
echo.
pause
endlocal
