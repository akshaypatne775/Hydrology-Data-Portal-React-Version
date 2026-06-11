@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "SOURCE_BACKEND_DIR=%ROOT%\backend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "LIVE_RELEASE_ROOT=D:\1_Portal_Workflows_development\DroidSurvair_Live_Release"
set "LIVE_BACKEND_DIR=%LIVE_RELEASE_ROOT%\backend"
set "PYTHON_EXE=%SOURCE_BACKEND_DIR%\venv\Scripts\python.exe"
popd

title Droid Live Backend 8000
color 0A

echo.
echo  ================================================================
echo                  DROID LIVE BACKEND - PORT 8000
echo  ================================================================
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend venv not found: "%PYTHON_EXE%"
  pause
  exit /b 1
)

if not exist "%LIVE_BACKEND_DIR%\app\main.py" (
  echo [ERROR] Live backend release copy not found: "%LIVE_BACKEND_DIR%"
  echo [FIX] Run 2_Start_Live_Portal.bat once, or run the Nightly deploy script.
  pause
  exit /b 1
)

pushd "%LIVE_BACKEND_DIR%"
call "%~dp0_Setup_EPT_Environment.bat"
set DEV_MODE=False
set LOCAL_DATA_PATH=%PROJECT_DATA_DIR%
set PORTAL_VERSION=live-%RANDOM%
echo [INFO] Starting FastAPI on http://127.0.0.1:8000
echo [INFO] Database: Project_Data\droid_cloud_prod.db
echo [INFO] Backend code: "%LIVE_BACKEND_DIR%"
echo [INFO] Project data: "%PROJECT_DATA_DIR%"
echo.
"%PYTHON_EXE%" -m uvicorn app.main:app --host 127.0.0.1 --port 8000
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo [STOPPED] Live backend exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
