@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "PYTHON_EXE=%ROOT%\backend\venv\Scripts\python.exe"
set "SETUP_SCRIPT=%ROOT%\backend\scripts\setup_dev_postgres.py"
popd

title Droid Dev PostgreSQL and PostGIS Setup
color 0B

echo.
echo  ================================================================
echo            DROID DEV POSTGRESQL AND POSTGIS SETUP
echo  ================================================================
echo.
echo  This configures DEV only:
echo    Database: droid_master_suite_dev
echo    Role:     droid_dev_app
echo    Port:     5432
echo.
echo  Live SQLite and Live server are not accessed.
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
  echo [FAILED] Dev PostgreSQL setup did not complete.
  pause
  exit /b 1
)

echo.
echo [READY] Dev PostgreSQL and PostGIS setup completed.
echo [NEXT] Return to Codex so migration and verification can continue.
echo.
pause
exit /b 0
