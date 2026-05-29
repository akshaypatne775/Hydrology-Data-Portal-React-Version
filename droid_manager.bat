@echo off
setlocal EnableExtensions EnableDelayedExpansion

if /I "%~1" NEQ "run" (
  start "Droid Cloud Master Tool" cmd /k ""%~f0" run"
  exit /b 0
)

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PROJECT_DATA_DIR=%ROOT%Project_Data"
set "PUBLIC_PORTAL_URL=https://portal.droidminingsolutions.com"

color 0B
title DROID CLOUD MASTER TOOL

:menu
cls
echo.
echo  ==================================================================
echo   DDDDD   RRRRR    OOOOO   IIIII  DDDDD        SSSSS  U   U  RRRRR
echo   D   D   R   R   O   O      I    D   D      S       U   U  R   R
echo   D   D   RRRRR   O   O      I    D   D       SSSS   U   U  RRRRR
echo   D   D   R  R    O   O      I    D   D          S   U   U  R  R
echo   DDDDD   R   R    OOO     IIIII  DDDDD      SSSSS    UUU   R   R
echo.
echo                   DROID CLOUD MASTER TOOL
echo  ==================================================================
echo.
echo   [1] One Click Install / Repair ^(safe, keeps your data^)
echo   [2] Save New Dependencies ^(Update requirements.txt^)
echo   [3] Start the Portal ^(Backend first, then Frontend^)
echo   [4] Exit
echo.
set "MAIN_CHOICE="
set /p "MAIN_CHOICE=Type 1, 2, 3, or 4 then press ENTER: "

if "%MAIN_CHOICE%"=="4" goto end
if "%MAIN_CHOICE%"=="3" goto start_portal
if "%MAIN_CHOICE%"=="2" goto save_dependencies
if "%MAIN_CHOICE%"=="1" goto one_click_install

echo [WARN] Invalid option. Please type 1, 2, 3, or 4.
timeout /t 1 >nul
goto menu

:check_folders
if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
  exit /b 1
)
if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend directory not found: "%FRONTEND_DIR%"
  exit /b 1
)
if not exist "%PROJECT_DATA_DIR%" mkdir "%PROJECT_DATA_DIR%"
if not exist "%PROJECT_DATA_DIR%\projects" mkdir "%PROJECT_DATA_DIR%\projects"
if not exist "%PROJECT_DATA_DIR%\uploads" mkdir "%PROJECT_DATA_DIR%\uploads"
if not exist "%PROJECT_DATA_DIR%\pointclouds" mkdir "%PROJECT_DATA_DIR%\pointclouds"
exit /b 0

:find_python
set "PYTHON_CMD="
for %%V in (3.12 3.11 3.10 3.13 3.14) do (
  if not defined PYTHON_CMD (
    py -%%V -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=py -%%V"
  )
)
if not defined PYTHON_CMD (
  python -c "import sys" >nul 2>&1
  if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
  echo [ERROR] Python was not found. Install Python 3.10, 3.11, or 3.12 and try again.
  exit /b 1
)
echo [INFO] Using Python command: %PYTHON_CMD%
exit /b 0

:ensure_backend_env
pushd "%BACKEND_DIR%"

set "VENV_BROKEN=0"
if exist "venv\Scripts\python.exe" (
  "venv\Scripts\python.exe" -c "import sys" >nul 2>&1
  if errorlevel 1 set "VENV_BROKEN=1"
) else (
  set "VENV_BROKEN=1"
)

if "%VENV_BROKEN%"=="1" (
  if exist venv (
    for /f %%T in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd_HHmmss"') do set "STAMP=%%T"
    echo [WARN] Existing backend venv is broken or stale.
    echo [INFO] Moving it to venv_broken_!STAMP! so nothing is destroyed.
    ren venv "venv_broken_!STAMP!"
    if errorlevel 1 (
      echo [ERROR] Could not move old venv. Close terminals using it and try again.
      popd
      exit /b 1
    )
  )
  echo [STEP] Creating backend virtual environment...
  %PYTHON_CMD% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create Python virtual environment.
    popd
    exit /b 1
  )
) else (
  echo [OK] Backend virtual environment already works.
)

call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [ERROR] Failed to update Python installer tools.
  popd
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Backend dependency installation failed.
  popd
  exit /b 1
)

if not exist ".env" (
  echo [STEP] Creating backend .env from safe local defaults...
  > ".env" echo LOCAL_DATA_PATH=%PROJECT_DATA_DIR:\=/%
  >> ".env" echo UPLOAD_DISK_HEADROOM_MB=512
  >> ".env" echo POINTCLOUD_SRS_IN=
  >> ".env" echo POINTCLOUD_SRS_OUT=4978
  >> ".env" echo SESSION_TTL_SECONDS=604800
  for /f %%S in ('powershell -NoProfile -Command "[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))"') do set "SESSION_SECRET=%%S"
  >> ".env" echo SESSION_SIGNING_SECRET=!SESSION_SECRET!
  >> ".env" echo FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,%PUBLIC_PORTAL_URL%
) else (
  echo [OK] Backend .env already exists. Keeping it unchanged.
)

python -m py_compile app\main.py app\core\database.py
if errorlevel 1 (
  echo [ERROR] Backend compile check failed.
  popd
  exit /b 1
)

popd
exit /b 0

:ensure_frontend_env
pushd "%FRONTEND_DIR%"

if not exist ".env.local" (
  if exist ".env.example" (
    copy ".env.example" ".env.local" >nul
    echo [STEP] Created frontend .env.local from .env.example.
  ) else (
    echo [WARN] frontend .env.example not found. Skipping .env.local creation.
  )
) else (
  echo [OK] Frontend .env.local already exists. Keeping it unchanged.
)

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS and try again.
  popd
  exit /b 1
)

echo [STEP] Installing frontend dependencies...
npm install
if errorlevel 1 (
  echo [ERROR] Frontend dependency installation failed.
  popd
  exit /b 1
)

echo [STEP] Checking frontend build...
npm run build
if errorlevel 1 (
  echo [ERROR] Frontend build failed. Read the error above.
  popd
  exit /b 1
)

popd
exit /b 0

:one_click_install
cls
echo [INFO] Starting safe one-click install / repair...
echo [INFO] Existing Project_Data, .env, and .env.local files will be kept.
echo.

call :check_folders
if errorlevel 1 (
  pause
  goto menu
)

call :find_python
if errorlevel 1 (
  pause
  goto menu
)

call :ensure_backend_env
if errorlevel 1 (
  pause
  goto menu
)
echo [SUCCESS] Backend setup complete.
echo.

call :ensure_frontend_env
if errorlevel 1 (
  pause
  goto menu
)
echo [SUCCESS] Frontend setup complete.
echo.

echo [DONE] One-click install / repair completed successfully.
echo [NEXT] Choose option 3 to start the portal.
pause
goto menu

:save_dependencies
cls
echo [INFO] Saving new dependencies...
echo.

call :check_folders
if errorlevel 1 (
  pause
  goto menu
)

pushd "%BACKEND_DIR%"
if not exist "venv\Scripts\activate.bat" (
  echo [ERROR] Virtual environment not found. Run option 1 first.
  popd
  pause
  goto menu
)

call "venv\Scripts\activate.bat"
python -m pip freeze > requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to save backend dependencies.
  popd
  pause
  goto menu
)
popd

echo [SUCCESS] Backend requirements.txt updated.
echo [INFO] Frontend packages are auto-saved to package.json by npm.
pause
goto menu

:ensure_backend_public_origin
set "BACKEND_ENV=%BACKEND_DIR%\.env"
if not exist "%BACKEND_ENV%" exit /b 0
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%BACKEND_ENV%'; $public='%PUBLIC_PORTAL_URL%'; $lines=Get-Content $p -ErrorAction SilentlyContinue; if(-not $lines){$lines=@()}; function Set-Line($k,$v){ $script:lines=@($script:lines | Where-Object {$_ -notmatch ('^'+[regex]::Escape($k)+'=')}); $script:lines += ($k+'='+$v) }; $origin=($lines | Where-Object {$_ -match '^FRONTEND_ORIGINS='} | Select-Object -First 1); if(-not $origin){Set-Line 'FRONTEND_ORIGINS' ('http://localhost:5173,http://127.0.0.1:5173,'+$public)} elseif($origin -notmatch [regex]::Escape($public)){Set-Line 'FRONTEND_ORIGINS' ($origin.Substring('FRONTEND_ORIGINS='.Length)+','+$public)}; Set-Content -Path $p -Value $lines -Encoding UTF8"
if errorlevel 1 (
  echo [ERROR] Could not update backend FRONTEND_ORIGINS.
  exit /b 1
)
exit /b 0

:wait_for_backend_ready
echo [INFO] Waiting for backend API to become ready...
for /L %%I in (1,1,90) do (
  powershell -NoProfile -ExecutionPolicy Bypass -Command "try { ^$res=Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2; if(^$res.StatusCode -ge 200 -and ^$res.StatusCode -lt 500){ exit 0 }; exit 1 } catch { exit 1 }" >nul 2>&1
  if not errorlevel 1 (
    echo [OK] Backend is ready on http://127.0.0.1:8000
    exit /b 0
  )
  timeout /t 1 >nul
)
echo [ERROR] Backend did not become ready within 90 seconds.
echo [FIX] Check the "Droid Cloud - Backend" window for the real error, then start again.
exit /b 1

:prepare_portal_start
call :check_folders
if errorlevel 1 (
  exit /b 1
)

if not exist "%BACKEND_DIR%\venv\Scripts\activate.bat" (
  echo [ERROR] Backend venv not found. Run option 1 first.
  exit /b 1
)

"%BACKEND_DIR%\venv\Scripts\python.exe" -c "import sys" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Backend venv is broken. Run option 1 to repair it safely.
  exit /b 1
)

"%BACKEND_DIR%\venv\Scripts\python.exe" -c "import uvicorn, fastapi" >nul 2>&1
if errorlevel 1 (
  echo [ERROR] Backend dependencies are missing from venv.
  echo [FIX] Run option 1 ^(One Click Install / Repair^) first.
  exit /b 1
)

call :ensure_backend_public_origin
if errorlevel 1 (
  exit /b 1
)
exit /b 0

:start_backend_process
call :prepare_portal_start
if errorlevel 1 exit /b 1
set "BACKEND_ALREADY_RUNNING=0"
set "BACKEND_PORT_CONFLICT=0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "try { ^$res=Invoke-WebRequest -Uri 'http://127.0.0.1:8000/health' -UseBasicParsing -TimeoutSec 2; if(^$res.StatusCode -ge 200 -and ^$res.StatusCode -lt 500){ exit 0 }; exit 2 } catch { try { ^$tcp=New-Object Net.Sockets.TcpClient; ^$iar=^$tcp.BeginConnect('127.0.0.1',8000,^$null,^$null); if(^$iar.AsyncWaitHandle.WaitOne(500)){ ^$tcp.EndConnect(^$iar); ^$tcp.Close(); exit 2 }; ^$tcp.Close() } catch {}; exit 1 }" >nul 2>&1
if errorlevel 2 set "BACKEND_PORT_CONFLICT=1"
if not errorlevel 1 set "BACKEND_ALREADY_RUNNING=1"

if "%BACKEND_PORT_CONFLICT%"=="1" (
  echo [ERROR] Port 8000 is already used by another server.
  echo [FIX] Close the other backend window, then choose Start Portal again.
  exit /b 1
) else if "%BACKEND_ALREADY_RUNNING%"=="1" (
  echo [OK] Backend is already running on http://127.0.0.1:8000
) else (
  echo [STEP] Starting backend...
  start "Droid Cloud - Backend" /D "%BACKEND_DIR%" cmd /k ""venv\Scripts\python.exe" -m uvicorn app.main:app --app-dir "%BACKEND_DIR%" --reload --host 127.0.0.1 --port 8000"
)
exit /b 0

:start_frontend_process
call :check_folders
if errorlevel 1 exit /b 1

call :wait_for_backend_ready
if errorlevel 1 (
  exit /b 1
)

echo [STEP] Starting frontend after backend is ready...
start "Droid Cloud - Frontend" /D "%FRONTEND_DIR%" cmd /k "npm run dev"
echo [OK] Frontend window launched.
echo [URL] Frontend: http://localhost:5173
echo [URL] Public:   %PUBLIC_PORTAL_URL%
echo [URL] Backend:  http://127.0.0.1:8000/health
exit /b 0

:start_portal
cls
echo.
echo  ==================================================================
echo                  START DROID CLOUD PORTAL
echo  ==================================================================
echo.
echo   [1] Start Backend only
echo   [2] Start Frontend only ^(waits for backend^)
echo   [3] Start Backend then Frontend ^(auto^)
echo   [4] Back to Main Menu
echo.
set "PORTAL_CHOICE="
set /p "PORTAL_CHOICE=Type 1, 2, 3, or 4 then press ENTER: "

if "%PORTAL_CHOICE%"=="4" goto menu
if "%PORTAL_CHOICE%"=="3" goto start_portal_auto
if "%PORTAL_CHOICE%"=="2" goto start_frontend_only
if "%PORTAL_CHOICE%"=="1" goto start_backend_only

echo [WARN] Invalid option. Please type 1, 2, 3, or 4.
timeout /t 1 >nul
goto start_portal

:start_backend_only
cls
echo [INFO] Starting backend only...
echo.
call :start_backend_process
if errorlevel 1 (
  pause
  goto start_portal
)
call :wait_for_backend_ready
if errorlevel 1 (
  pause
  goto start_portal
)
echo.
echo [SUCCESS] Backend is running and ready.
echo [NEXT] Come back here and choose option 2 to start frontend.
pause
goto start_portal

:start_frontend_only
cls
echo [INFO] Starting frontend only...
echo.
call :start_frontend_process
if errorlevel 1 (
  pause
  goto start_portal
)
echo.
echo [SUCCESS] Frontend command sent.
pause
goto start_portal

:start_portal_auto
cls
echo [INFO] Starting backend first, then frontend...
echo.
call :start_backend_process
if errorlevel 1 (
  pause
  goto start_portal
)
call :start_frontend_process
if errorlevel 1 (
  pause
  goto start_portal
)
echo.
echo [SUCCESS] Backend and frontend windows launched.
echo [NOTE] Keep both opened command windows running. If /api shows ECONNREFUSED, the backend window stopped or port 8000 is busy.
pause
goto menu

:end
echo.
echo Exiting Droid Cloud Master Tool...
timeout /t 1 >nul
endlocal
exit /b 0
