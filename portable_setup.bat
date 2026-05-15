@echo off
setlocal EnableExtensions EnableDelayedExpansion

title Droid Cloud Portable Setup
color 0B

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"
set "PROJECT_DATA_DIR=%ROOT%Project_Data"

echo.
echo  ================================================================
echo                 DROID CLOUD PORTABLE SETUP
echo  ================================================================
echo.
echo  This will repair/install dependencies after moving this project.
echo  It keeps your Project_Data and existing environment files safe.
echo.

call :check_layout
if errorlevel 1 goto fail

call :ensure_project_data
if errorlevel 1 goto fail

call :find_python
if errorlevel 1 goto fail

call :setup_backend
if errorlevel 1 goto fail

call :setup_frontend
if errorlevel 1 goto fail

call :check_gdal

echo.
echo  ================================================================
echo  DONE: Portable setup completed successfully.
echo  ================================================================
echo.
echo  Next:
echo   1. Run droid_manager.bat
echo   2. Choose option 3 to start the portal
echo.
pause
exit /b 0

:check_layout
if not exist "%BACKEND_DIR%" (
  echo [ERROR] backend folder not found: "%BACKEND_DIR%"
  exit /b 1
)
if not exist "%FRONTEND_DIR%" (
  echo [ERROR] frontend folder not found: "%FRONTEND_DIR%"
  exit /b 1
)
if not exist "%BACKEND_DIR%\requirements.txt" (
  echo [ERROR] backend\requirements.txt not found.
  exit /b 1
)
if not exist "%FRONTEND_DIR%\package.json" (
  echo [ERROR] frontend\package.json not found.
  exit /b 1
)
exit /b 0

:ensure_project_data
echo [STEP] Preparing Project_Data folders...
if not exist "%PROJECT_DATA_DIR%" mkdir "%PROJECT_DATA_DIR%"
if not exist "%PROJECT_DATA_DIR%\projects" mkdir "%PROJECT_DATA_DIR%\projects"
if not exist "%PROJECT_DATA_DIR%\uploads" mkdir "%PROJECT_DATA_DIR%\uploads"
if not exist "%PROJECT_DATA_DIR%\uploads\chunks" mkdir "%PROJECT_DATA_DIR%\uploads\chunks"
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
  echo [ERROR] Python was not found. Install Python 3.10+ and run this again.
  exit /b 1
)
echo [OK] Python command: %PYTHON_CMD%
exit /b 0

:setup_backend
echo.
echo [STEP] Setting up backend...
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
    echo [WARN] Existing backend venv looks broken or was moved.
    echo [INFO] Renaming it to venv_broken_!STAMP!
    ren venv "venv_broken_!STAMP!"
    if errorlevel 1 (
      echo [ERROR] Could not rename old venv. Close terminals using it and retry.
      popd
      exit /b 1
    )
  )
  echo [STEP] Creating backend virtual environment...
  %PYTHON_CMD% -m venv venv
  if errorlevel 1 (
    echo [ERROR] Failed to create backend venv.
    popd
    exit /b 1
  )
) else (
  echo [OK] Existing backend venv works.
)

call "venv\Scripts\activate.bat"
python -m pip install --upgrade pip setuptools wheel
if errorlevel 1 (
  echo [ERROR] Failed to update pip tools.
  popd
  exit /b 1
)

python -m pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to install backend dependencies.
  popd
  exit /b 1
)

call :repair_backend_env
if errorlevel 1 (
  popd
  exit /b 1
)

python -m py_compile app\main.py app\core\database.py
if errorlevel 1 (
  echo [ERROR] Backend compile check failed.
  popd
  exit /b 1
)

popd
exit /b 0

:repair_backend_env
set "BACKEND_ENV=%BACKEND_DIR%\.env"
set "LOCAL_DATA_POSIX=%PROJECT_DATA_DIR:\=/%"
if not exist "%BACKEND_ENV%" (
  echo [STEP] Creating backend .env...
  > "%BACKEND_ENV%" echo LOCAL_DATA_PATH=%LOCAL_DATA_POSIX%
  >> "%BACKEND_ENV%" echo UPLOAD_DISK_HEADROOM_MB=512
  >> "%BACKEND_ENV%" echo POINTCLOUD_SRS_IN=
  >> "%BACKEND_ENV%" echo POINTCLOUD_SRS_OUT=4978
  >> "%BACKEND_ENV%" echo SESSION_TTL_SECONDS=604800
  for /f %%S in ('powershell -NoProfile -Command "[Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48))"') do set "SESSION_SECRET=%%S"
  >> "%BACKEND_ENV%" echo SESSION_SIGNING_SECRET=!SESSION_SECRET!
  >> "%BACKEND_ENV%" echo FRONTEND_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
  exit /b 0
)

copy "%BACKEND_ENV%" "%BACKEND_ENV%.before_portable_setup" >nul
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$p='%BACKEND_ENV%'; $local='%LOCAL_DATA_POSIX%'; $lines=Get-Content $p -ErrorAction SilentlyContinue; if(-not $lines){$lines=@()}; $keys=@('LOCAL_DATA_PATH','UPLOAD_DISK_HEADROOM_MB','SESSION_TTL_SECONDS','FRONTEND_ORIGINS'); function Set-Line($k,$v){ $script:lines=@($script:lines | Where-Object {$_ -notmatch ('^'+[regex]::Escape($k)+'=')}); $script:lines += ($k+'='+$v) }; Set-Line 'LOCAL_DATA_PATH' $local; if(-not ($lines -match '^UPLOAD_DISK_HEADROOM_MB=')){Set-Line 'UPLOAD_DISK_HEADROOM_MB' '512'}; if(-not ($lines -match '^SESSION_TTL_SECONDS=')){Set-Line 'SESSION_TTL_SECONDS' '604800'}; if(-not ($lines -match '^FRONTEND_ORIGINS=')){Set-Line 'FRONTEND_ORIGINS' 'http://localhost:5173,http://127.0.0.1:5173'}; if(-not ($lines -match '^SESSION_SIGNING_SECRET=')){Set-Line 'SESSION_SIGNING_SECRET' ([Convert]::ToBase64String([Security.Cryptography.RandomNumberGenerator]::GetBytes(48)))}; Set-Content -Path $p -Value $lines -Encoding UTF8"
if errorlevel 1 (
  echo [ERROR] Failed to repair backend .env.
  exit /b 1
)
echo [OK] Backend .env repaired for this folder.
exit /b 0

:setup_frontend
echo.
echo [STEP] Setting up frontend...
pushd "%FRONTEND_DIR%"

where npm >nul 2>&1
if errorlevel 1 (
  echo [ERROR] npm was not found. Install Node.js LTS and run this again.
  popd
  exit /b 1
)

if not exist ".env.local" (
  if exist ".env.example" (
    copy ".env.example" ".env.local" >nul
    echo [OK] Created frontend .env.local from .env.example.
  ) else (
    > ".env.local" echo VITE_API_BASE_URL=http://localhost:8000
    >> ".env.local" echo VITE_S3_TILE_BASE_URL=http://localhost:8000/tiles
    >> ".env.local" echo VITE_FLOOD_TILE_BASE_URL=http://localhost:8000/tiles/flood
    echo [OK] Created frontend .env.local defaults.
  )
) else (
  echo [OK] Frontend .env.local exists.
)

if exist package-lock.json (
  echo [STEP] Installing frontend dependencies with npm ci...
  npm ci
) else (
  echo [STEP] Installing frontend dependencies with npm install...
  npm install
)
if errorlevel 1 (
  echo [ERROR] Failed to install frontend dependencies.
  popd
  exit /b 1
)

echo [STEP] Running frontend build check...
npm run build
if errorlevel 1 (
  echo [ERROR] Frontend build failed.
  popd
  exit /b 1
)

popd
exit /b 0

:check_gdal
echo.
echo [STEP] Checking optional QGIS/GDAL tools...
set "DEFAULT_OSGEO4W=C:\Program Files\QGIS 3.44.8\OSGeo4W.bat"
if exist "%DEFAULT_OSGEO4W%" (
  echo [OK] QGIS/GDAL shell found: %DEFAULT_OSGEO4W%
) else (
  echo [WARN] QGIS/GDAL shell not found at:
  echo        %DEFAULT_OSGEO4W%
  echo        TIFF to XYZ tile conversion needs QGIS/GDAL installed.
  echo        Install QGIS or set OSGEO4W_BAT in backend\.env if your path is different.
)
exit /b 0

:fail
echo.
echo  ================================================================
echo  FAILED: Portable setup did not complete.
echo  Read the error above, then run this file again.
echo  ================================================================
echo.
pause
exit /b 1
