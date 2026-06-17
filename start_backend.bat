@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "PROJECT_DATA_DIR=%ROOT%Project_Data"

color 0B
title Droid Cloud - Backend

echo.
echo  ================================================================
echo                    DROID CLOUD BACKEND
echo  ================================================================
echo.

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend folder not found: "%BACKEND_DIR%"
  pause
  exit /b 1
)

if not exist "%BACKEND_DIR%\venv\Scripts\python.exe" (
  echo [ERROR] Backend venv not found.
  echo [FIX] Run droid_manager.bat option 1 first.
  pause
  exit /b 1
)

if not exist "%PROJECT_DATA_DIR%" mkdir "%PROJECT_DATA_DIR%"

pushd "%BACKEND_DIR%"

if exist "%ROOT%Droid_Environment_Manager\_Setup_EPT_Environment.bat" (
  call "%ROOT%Droid_Environment_Manager\_Setup_EPT_Environment.bat"
)

"venv\Scripts\python.exe" -c "import sys, uvicorn, fastapi" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Backend dependencies missing or venv broken.
  echo [FIX] Run droid_manager.bat option 1 first.
  popd
  pause
  exit /b 1
)

echo [INFO] Starting backend on http://127.0.0.1:8000
echo [INFO] Keep this window open.
echo.

"venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "%BACKEND_DIR%" --reload --host 127.0.0.1 --port 8000

popd
echo.
echo [INFO] Backend stopped.
pause
endlocal
