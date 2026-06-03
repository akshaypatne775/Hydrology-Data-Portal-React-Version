@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "FRONTEND_DIR=%ROOT%\frontend"
popd

title Droid Live Frontend 4173
color 0A

echo.
echo  ================================================================
echo                  DROID LIVE FRONTEND - PORT 4173
echo  ================================================================
echo.

if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend folder not found: "%FRONTEND_DIR%"
  pause
  exit /b 1
)

pushd "%FRONTEND_DIR%"
set VITE_BACKEND_PORT=8000
set VITE_BUILD_OUT_DIR=dist_live
echo [INFO] Starting Vite preview on http://localhost:4173
echo [INFO] Cloudflare tunnel should point to this port.
echo.
call npm run preview -- --host 0.0.0.0 --port 4173 --outDir dist_live
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo [STOPPED] Live frontend exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
