@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "DEV_DB=%PROJECT_DATA_DIR%\droid_cloud_dev.db"
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

if not exist "%DEV_DB%" (
  echo [WARN] Legacy Dev SQLite migration source is missing: "%DEV_DB%"
  echo [INFO] Continuing because Dev now uses PostgreSQL/PostGIS.
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  pause
  exit /b 1
)

powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/version' -TimeoutSec 2; if($r.dev_mode -eq 'true'){exit 0} } catch {}; exit 1" >nul 2>&1
if errorlevel 1 goto START_DEV_BACKEND
echo [INFO] Reusing healthy Dev FastAPI backend on http://127.0.0.1:8001
goto CHECK_DEV_FRONTEND

:START_DEV_BACKEND
powershell -NoProfile -Command "if(Get-NetTCPConnection -LocalPort 8001 -State Listen -ErrorAction SilentlyContinue){exit 0}else{exit 1}" >nul 2>&1
if not errorlevel 1 goto DEV_BACKEND_PORT_ERROR
echo [INFO] Starting Dev FastAPI backend on http://127.0.0.1:8001
start "Droid Dev Backend 8001" "%ComSpec%" /k call "%~dp0_Run_Dev_Backend_8001.bat"

:CHECK_DEV_FRONTEND
powershell -NoProfile -Command "try { $r=Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/version' -TimeoutSec 2; if($r.dev_mode -eq 'true'){exit 0} } catch {}; exit 1" >nul 2>&1
if errorlevel 1 goto START_DEV_FRONTEND
echo [INFO] Reusing healthy Dev React frontend on http://localhost:5173
goto DEV_READY

:START_DEV_FRONTEND
powershell -NoProfile -Command "if(Get-NetTCPConnection -LocalPort 5173 -State Listen -ErrorAction SilentlyContinue){exit 0}else{exit 1}" >nul 2>&1
if not errorlevel 1 goto DEV_FRONTEND_PORT_ERROR
echo [INFO] Starting Dev React frontend on http://localhost:5173
start "Droid Dev Frontend 5173" /D "%FRONTEND_DIR%" "%ComSpec%" /k "set VITE_BACKEND_PORT=8001&&call npm run dev"
goto DEV_READY

:DEV_BACKEND_PORT_ERROR
echo [ERROR] Port 8001 is occupied by another or unhealthy process.
echo [FIX] Close that process and run this Dev launcher again.
pause
exit /b 1

:DEV_FRONTEND_PORT_ERROR
echo [ERROR] Port 5173 is occupied by another or unhealthy process.
echo [FIX] Close that process and run this Dev launcher again.
pause
exit /b 1

:DEV_READY

echo.
echo [INFO] Waiting for Dev backend and frontend health checks...
powershell -NoProfile -Command "$deadline=(Get-Date).AddSeconds(60); do { $backend=$false; $frontend=$false; try { $b=Invoke-RestMethod -Uri 'http://127.0.0.1:8001/api/version' -TimeoutSec 2; $backend=($b.dev_mode -eq 'true') } catch {}; try { $f=Invoke-RestMethod -Uri 'http://127.0.0.1:5173/api/version' -TimeoutSec 2; $frontend=($f.dev_mode -eq 'true') } catch {}; if($backend -and $frontend){exit 0}; Start-Sleep -Seconds 2 } while((Get-Date) -lt $deadline); exit 1"
if errorlevel 1 goto DEV_START_FAILED

echo.
echo [READY] Dev world started.
echo        Backend:  127.0.0.1:8001  DB: PostgreSQL/PostGIS droid_master_suite_dev
echo        Frontend: localhost:5173
echo.
start "" "http://localhost:5173/"
echo [INFO] Dev portal opened in your default browser.
echo.
pause
endlocal
exit /b 0

:DEV_START_FAILED
echo.
echo [ERROR] Dev services did not become healthy within 60 seconds.
echo [CHECK] Review the Droid Dev Backend 8001 and Droid Dev Frontend 5173 windows.
echo [SAFE] Live 8000/4173 services were not changed.
echo.
pause
endlocal
exit /b 1
