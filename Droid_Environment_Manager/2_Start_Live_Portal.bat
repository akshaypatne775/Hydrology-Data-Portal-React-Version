@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "PROD_DB=%PROJECT_DATA_DIR%\droid_cloud_prod.db"
set "LEGACY_DB=%PROJECT_DATA_DIR%\issues.db"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "LIVE_DIST=%FRONTEND_DIR%\dist_live\index.html"
set "LIVE_BACKEND_DIR=%ROOT%\Droid_Environment_Manager\Live_Backend_Release\backend"
popd

color 0A
title Droid Cloud - Live Portal Manager

echo.
echo  ================================================================
echo                    DROID CLOUD LIVE PORTAL
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

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  pause
  exit /b 1
)

if not exist "%PROJECT_DATA_DIR%" mkdir "%PROJECT_DATA_DIR%"
if not exist "%PROD_DB%" if exist "%LEGACY_DB%" (
  echo [INFO] First-time migration: copying legacy issues.db to droid_cloud_prod.db...
  copy /Y "%LEGACY_DB%" "%PROD_DB%" >nul
)

if not exist "%LIVE_DIST%" (
  echo [INFO] No deployed Live bundle found. Building first Live bundle into frontend\dist_live...
  pushd "%FRONTEND_DIR%"
  set VITE_BACKEND_PORT=8000
  set VITE_BUILD_OUT_DIR=dist_live
  call npm run build
  if errorlevel 1 (
    popd
    echo [ERROR] Frontend build failed. Live portal was not started.
    pause
    exit /b 1
  )
  popd
) else (
  echo [INFO] Using existing deployed Live bundle: frontend\dist_live
  echo [INFO] Source changes will not appear here until Nightly Auto Deploy rebuilds dist_live.
)

if not exist "%LIVE_BACKEND_DIR%\app\main.py" (
  echo [INFO] No deployed Live backend copy found. Creating first Live backend release copy...
  if not exist "%LIVE_BACKEND_DIR%" mkdir "%LIVE_BACKEND_DIR%"
  robocopy "%BACKEND_DIR%\app" "%LIVE_BACKEND_DIR%\app" /MIR /XD __pycache__ /XF *.pyc >nul
  if errorlevel 8 (
    echo [ERROR] Failed to create Live backend release copy.
    pause
    exit /b 1
  )
  if exist "%BACKEND_DIR%\requirements.txt" copy /Y "%BACKEND_DIR%\requirements.txt" "%LIVE_BACKEND_DIR%\requirements.txt" >nul
  if exist "%BACKEND_DIR%\.env" copy /Y "%BACKEND_DIR%\.env" "%LIVE_BACKEND_DIR%\.env" >nul
) else (
  echo [INFO] Using existing deployed Live backend copy: Droid_Environment_Manager\Live_Backend_Release\backend
  echo [INFO] Backend source changes will not appear here until Nightly Auto Deploy refreshes this copy.
)

echo [INFO] Starting Live React preview on http://localhost:4173
start "Droid Live Frontend 4173" "%ComSpec%" /k call "%~dp0_Run_Live_Frontend_4173.bat"

echo [INFO] Starting Live FastAPI backend on http://127.0.0.1:8000
start "Droid Live Backend 8000" "%ComSpec%" /k call "%~dp0_Run_Live_Backend_8000.bat"

echo.
echo [READY] Live world started.
echo        Backend:  127.0.0.1:8000  DB: Project_Data\droid_cloud_prod.db
echo        Frontend: localhost:4173  Cloudflare tunnel target
echo.
pause
endlocal
