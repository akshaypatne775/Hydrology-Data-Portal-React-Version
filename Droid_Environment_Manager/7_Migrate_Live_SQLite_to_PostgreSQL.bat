@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "PYTHON_EXE=%ROOT%\backend\venv\Scripts\python.exe"
set "MIGRATE_SCRIPT=%ROOT%\backend\scripts\migrate_sqlite_to_postgres.py"
set "ENV_FILE=%ROOT%\backend\.env.live"
set "PROD_DB=%ROOT%\Project_Data\droid_cloud_prod.db"
set "LIVE_RELEASE_ROOT=D:\1_Portal_Workflows_development\DroidSurvair_Live_Release"
set "LIVE_ENV=%LIVE_RELEASE_ROOT%\backend\.env"
popd

title Droid Live SQLite to PostgreSQL Migration
color 0A

echo.
echo  ================================================================
echo         DROID LIVE SQLITE TO POSTGRESQL MIGRATION
echo  ================================================================
echo.
echo  Source: %PROD_DB%  (read-only)
echo  Target: PostgreSQL/PostGIS droid_master_suite
echo.
echo  Dev SQLite and droid_master_suite_dev are not used.
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend Python was not found: "%PYTHON_EXE%"
  pause
  exit /b 1
)

if not exist "%MIGRATE_SCRIPT%" (
  echo [ERROR] Migration script was not found: "%MIGRATE_SCRIPT%"
  pause
  exit /b 1
)

if not exist "%ENV_FILE%" (
  echo [ERROR] Live env file was not found: "%ENV_FILE%"
  echo [FIX] Run 6_Setup_Live_PostgreSQL.bat first.
  pause
  exit /b 1
)

if not exist "%PROD_DB%" (
  echo [ERROR] Live SQLite database was not found: "%PROD_DB%"
  pause
  exit /b 1
)

echo [WARN] This is a one-time migration. It refuses to run if Live PostgreSQL already has data.
echo [WARN] If you already tried once, choose Reset on the next prompt.
echo.
choice /C YN /N /M "Continue with Live migration? Y/N: "
if errorlevel 2 (
  echo [CANCELLED] Live migration was not started.
  pause
  exit /b 0
)

echo.
choice /C RN /N /M "Reset existing Live PostgreSQL data first? R=Reset, N=Normal: "
set "RESET_FLAG="
if not errorlevel 2 set "RESET_FLAG=--reset-target"

echo.
echo [INFO] Migrating Live SQLite data into PostgreSQL...
"%PYTHON_EXE%" "%MIGRATE_SCRIPT%" --env-file "%ENV_FILE%" --sqlite-path "%PROD_DB%" %RESET_FLAG%
if errorlevel 1 (
  echo.
  echo [FAILED] Live migration did not complete.
  pause
  exit /b 1
)

if exist "%LIVE_RELEASE_ROOT%" (
  echo [INFO] Refreshing Live release env file...
  if not exist "%LIVE_RELEASE_ROOT%\backend" mkdir "%LIVE_RELEASE_ROOT%\backend"
  copy /Y "%ENV_FILE%" "%LIVE_ENV%" >nul
)

echo.
echo [READY] Live migration completed.
echo [NEXT] Run 3_Nightly_Auto_Deploy.bat and choose Deploy Now.
echo        That will refresh Live backend code and restart Live on PostgreSQL.
echo.
pause
exit /b 0
