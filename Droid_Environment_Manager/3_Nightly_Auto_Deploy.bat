@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "ROOT=%CD%"
set "BACKEND_DIR=%ROOT%\backend"
set "FRONTEND_DIR=%ROOT%\frontend"
set "PROJECT_DATA_DIR=%ROOT%\Project_Data"
set "PYTHON_EXE=%BACKEND_DIR%\venv\Scripts\python.exe"
set "LIVE_RELEASE_ROOT=D:\1_Portal_Workflows_development\DroidSurvair_Live_Release"
set "LIVE_FRONTEND_DIR=%LIVE_RELEASE_ROOT%\frontend"
set "LIVE_BACKEND_DIR=%LIVE_RELEASE_ROOT%\backend"
set "LOG_DIR=%LIVE_RELEASE_ROOT%\logs"
set "LOG_FILE=%LOG_DIR%\deployment_log.txt"
popd

color 09
title Droid Cloud - Nightly Auto Deploy

echo.
echo  ================================================================
echo                   DROID CLOUD NIGHTLY DEPLOY
echo  ================================================================
echo.

if not exist "%PYTHON_EXE%" (
  echo [ERROR] Backend venv not found.
  pause
  exit /b 1
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS first.
  pause
  exit /b 1
)

if not exist "%LIVE_RELEASE_ROOT%" mkdir "%LIVE_RELEASE_ROOT%"
if not exist "%LIVE_FRONTEND_DIR%" mkdir "%LIVE_FRONTEND_DIR%"
if not exist "%LIVE_BACKEND_DIR%" mkdir "%LIVE_BACKEND_DIR%"
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

echo Choose when to deploy the current Dev release to Live:
echo   [N] Deploy Now
echo   [S] Schedule for the next 3:00 AM
choice /C NS /N /M "Select N or S: "
if errorlevel 2 goto WAIT_FOR_3AM
goto START_DEPLOY

:WAIT_FOR_3AM
for /f %%S in ('powershell -NoProfile -ExecutionPolicy Bypass -Command "$now=Get-Date; $target=$now.Date.AddHours(3); if($now -ge $target){$target=$target.AddDays(1)}; [int][Math]::Ceiling(($target-$now).TotalSeconds)"') do set "WAIT_SECONDS=%%S"

echo [INFO] Waiting %WAIT_SECONDS% seconds until the next 3:00 AM deployment window...
timeout /t %WAIT_SECONDS% /nobreak

:START_DEPLOY
echo.
echo [INFO] Starting nightly deployment at %DATE% %TIME%
echo [%DATE% %TIME%] Nightly deployment started.>> "%LOG_FILE%"
echo [INFO] Deploy updates Live code only. Live PostgreSQL client data is not modified.

pushd "%FRONTEND_DIR%"
set VITE_BACKEND_PORT=8000
set VITE_BUILD_OUT_DIR=dist_live
call npm run build
if errorlevel 1 (
  popd
  echo [%DATE% %TIME%] Frontend build failed.>> "%LOG_FILE%"
  echo [ERROR] Frontend build failed.
  pause
  exit /b 1
)
popd
echo [INFO] Copying frontend bundle to external Live release...
robocopy "%FRONTEND_DIR%\dist_live" "%LIVE_FRONTEND_DIR%\dist_live" /MIR >nul
if errorlevel 8 (
  echo [%DATE% %TIME%] Frontend copy to external Live release failed.>> "%LOG_FILE%"
  echo [ERROR] Frontend copy to external Live release failed.
  pause
  exit /b 1
)
echo [%DATE% %TIME%] Frontend build completed.>> "%LOG_FILE%"

echo [INFO] Refreshing Live backend release copy...
if not exist "%LIVE_BACKEND_DIR%" mkdir "%LIVE_BACKEND_DIR%"
robocopy "%BACKEND_DIR%\app" "%LIVE_BACKEND_DIR%\app" /MIR /XD __pycache__ /XF *.pyc >nul
if errorlevel 8 (
  echo [%DATE% %TIME%] Live backend release copy failed.>> "%LOG_FILE%"
  echo [ERROR] Live backend release copy failed.
  pause
  exit /b 1
)
if exist "%BACKEND_DIR%\requirements.txt" copy /Y "%BACKEND_DIR%\requirements.txt" "%LIVE_BACKEND_DIR%\requirements.txt" >nul
if exist "%BACKEND_DIR%\.env.live" (
  copy /Y "%BACKEND_DIR%\.env.live" "%LIVE_BACKEND_DIR%\.env" >nul
  echo [%DATE% %TIME%] Live backend env copied from backend\.env.live.>> "%LOG_FILE%"
) else if exist "%BACKEND_DIR%\.env" (
  copy /Y "%BACKEND_DIR%\.env" "%LIVE_BACKEND_DIR%\.env" >nul
  echo [%DATE% %TIME%] WARN: backend\.env.live missing; copied dev .env to Live release.>> "%LOG_FILE%"
  echo [WARN] backend\.env.live not found. Copied dev .env to Live release.
  echo [WARN] Run 6_Setup_Live_PostgreSQL.bat before Live PostgreSQL cutover.
)
echo [%DATE% %TIME%] Live backend release copy refreshed.>> "%LOG_FILE%"

echo [INFO] Stopping existing Live backend process on port 8000...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$currentPid=$PID; $pids=Get-NetTCPConnection -LocalPort 8000 -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique; foreach($procId in $pids){ if($procId -and $procId -ne $currentPid){ Stop-Process -Id $procId -ErrorAction SilentlyContinue } }"
echo [%DATE% %TIME%] Existing Live backend stop requested for port 8000.>> "%LOG_FILE%"

timeout /t 5 /nobreak >nul

echo [INFO] Restarting Live backend on http://127.0.0.1:8000
start "Droid Live Backend 8000" "%ComSpec%" /k call "%~dp0_Run_Live_Backend_8000.bat"

echo [%DATE% %TIME%] Live backend restarted on port 8000.>> "%LOG_FILE%"
echo [%DATE% %TIME%] Live release folder: %LIVE_RELEASE_ROOT%.>> "%LOG_FILE%"
echo [DONE] Nightly deployment completed. Log: "%LOG_FILE%"
pause
endlocal
