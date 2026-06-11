@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "BACKEND_DIR=%ROOT%\backend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
popd

title Droid Dev Backend 8001
color 0B

echo.
echo  ================================================================
echo                   DROID DEV BACKEND - PORT 8001
echo  ================================================================
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend venv not found: "%PYTHON_EXE%"
  pause
  exit /b 1
)

pushd "%BACKEND_DIR%"
call "%~dp0_Setup_EPT_Environment.bat"
set DEV_MODE=True
set LOCAL_DATA_PATH=%PROJECT_DATA_DIR%
set PORTAL_VERSION=dev-%RANDOM%
echo [INFO] Starting FastAPI on http://127.0.0.1:8001
echo [INFO] Database: Project_Data\droid_cloud_dev.db
echo [INFO] Backend code: "%BACKEND_DIR%"
echo [INFO] Project data: "%PROJECT_DATA_DIR%"
echo.
"%PYTHON_EXE%" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8001
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo [STOPPED] Dev backend exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
