@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "PYTHON_EXE=%ROOT%\backend\venv\Scripts\python.exe"
set "SETUP_SCRIPT=%ROOT%\backend\scripts\setup_live_postgres.py"
popd

title Droid Live PostgreSQL and PostGIS Setup
color 0A

echo.
echo  ================================================================
echo            DROID LIVE POSTGRESQL AND POSTGIS SETUP
echo  ================================================================
echo.
echo  This configures LIVE only:
echo    Database: droid_master_suite
echo    Role:     droid_live_app
echo    Port:     5432
echo.
echo  Dev PostgreSQL and droid_master_suite_dev are not modified.
echo  Live SQLite droid_cloud_prod.db is not modified in this step.
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend Python was not found: "%PYTHON_EXE%"
  pause
  exit /b 1
)

if not exist "%SETUP_SCRIPT%" (
  echo [ERROR] Setup script was not found: "%SETUP_SCRIPT%"
  pause
  exit /b 1
)

"%PYTHON_EXE%" "%SETUP_SCRIPT%"
if errorlevel 1 (
  echo.
  echo [FAILED] Live PostgreSQL setup did not complete.
  pause
  exit /b 1
)

echo.
echo [READY] Live PostgreSQL and PostGIS setup completed.
echo [NEXT] Run 7_Migrate_Live_SQLite_to_PostgreSQL.bat
echo.
pause
exit /b 0
