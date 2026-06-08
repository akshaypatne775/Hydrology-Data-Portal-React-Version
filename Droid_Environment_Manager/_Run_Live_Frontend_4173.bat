@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "SOURCE_FRONTEND_DIR=%ROOT%\frontend"
set "LIVE_RELEASE_ROOT=D:\1_Portal_Workflows_development\DroidSurvair_Live_Release"
set "LIVE_FRONTEND_DIR=%LIVE_RELEASE_ROOT%\frontend"
popd

title Droid Live Frontend 4173
color 0A

echo.
echo  ================================================================
echo                  DROID LIVE FRONTEND - PORT 4173
echo  ================================================================
echo.

if not exist "%SOURCE_FRONTEND_DIR%" (
  echo [ERROR] Source frontend folder not found: "%SOURCE_FRONTEND_DIR%"
  pause
  exit /b 1
)

if not exist "%LIVE_FRONTEND_DIR%\dist_live\index.html" (
  echo [ERROR] External Live frontend bundle not found: "%LIVE_FRONTEND_DIR%\dist_live"
  echo [FIX] Run 2_Start_Live_Portal.bat once, or run 3_Nightly_Auto_Deploy.bat and choose Deploy Now.
  pause
  exit /b 1
)

pushd "%SOURCE_FRONTEND_DIR%"
set VITE_BACKEND_PORT=8000
echo [INFO] Starting Vite preview on http://localhost:4173
echo [INFO] Live frontend bundle: "%LIVE_FRONTEND_DIR%\dist_live"
echo [INFO] Cloudflare tunnel should point to this port.
echo.
call npm run preview -- --host 0.0.0.0 --port 4173 --outDir "%LIVE_FRONTEND_DIR%\dist_live"
set "EXIT_CODE=%ERRORLEVEL%"
popd

echo.
echo [STOPPED] Live frontend exited with code %EXIT_CODE%.
pause
exit /b %EXIT_CODE%
