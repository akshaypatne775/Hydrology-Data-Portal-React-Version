@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "FRONTEND_DIR=%ROOT%frontend"

color 0B
title Droid Cloud - Frontend

echo.
echo  ================================================================
echo                    DROID CLOUD FRONTEND
echo  ================================================================
echo.

if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend folder not found: "%FRONTEND_DIR%"
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  pause
  exit /b 1
)

echo [INFO] Checking backend on http://127.0.0.1:8000/health
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { $res=Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2; if($res.StatusCode -ge 200 -and $res.StatusCode -lt 500){ exit 0 }; exit 1 } catch { exit 1 }" >nul 2>&1
if errorlevel 1 (
  echo [WARN] Backend is not ready yet.
  echo [FIX] First run start_backend.bat and wait for "Application startup complete".
  echo.
  pause
  exit /b 1
)

pushd "%FRONTEND_DIR%"

if not exist "node_modules" (
  echo [ERROR] Frontend dependencies are missing.
  echo [FIX] Run droid_manager.bat option 1 first.
  popd
  pause
  exit /b 1
)

echo [INFO] Starting frontend on http://localhost:5173
echo [INFO] Keep this window open.
echo.

npm run dev

popd
echo.
echo [INFO] Frontend stopped.
pause
endlocal
