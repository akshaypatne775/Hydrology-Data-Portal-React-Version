@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "PYTHON_EXE=%ROOT%\backend\venv\Scripts\python.exe"
set "SYNC_SCRIPT=%ROOT%\backend\scripts\sync_postgres_worlds.py"
popd

title Droid Dev PostgreSQL to Live PostgreSQL Copy
color 0A

echo.
echo  ================================================================
echo         COPY DEV POSTGRESQL DATA TO LIVE POSTGRESQL
echo  ================================================================
echo.
echo  Source: droid_master_suite_dev
echo  Target: droid_master_suite
echo.
echo  Live PostgreSQL will be replaced with the current Dev data.
echo  SQLite files are not modified.
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend Python was not found: "%PYTHON_EXE%"
  pause
  exit /b 1
)

if not exist "%SYNC_SCRIPT%" (
  echo [ERROR] Sync script was not found: "%SYNC_SCRIPT%"
  pause
  exit /b 1
)

choice /C YN /N /M "Copy Dev PostgreSQL into Live PostgreSQL now? Y/N: "
if errorlevel 2 (
  echo [CANCELLED] Dev to Live copy was not started.
  pause
  exit /b 0
)

echo.
echo [INFO] Copying Dev PostgreSQL data into Live PostgreSQL...
"%PYTHON_EXE%" "%SYNC_SCRIPT%" --direction dev-to-live
if errorlevel 1 (
  echo.
  echo [FAILED] Dev to Live PostgreSQL copy did not complete.
  pause
  exit /b 1
)

echo.
echo [READY] Live PostgreSQL now matches Dev PostgreSQL.
echo [WARN] This is a manual one-off tool only.
echo        Normal deploy does NOT copy Dev database into Live.
echo        Live client PostgreSQL should stay untouched during deploy.
echo.
pause
exit /b 0
