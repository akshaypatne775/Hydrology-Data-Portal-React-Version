@echo off
setlocal EnableExtensions

if /I "%~1" NEQ "run" (
  start "Droid Survair Master Tool" cmd /k ""%~f0" run"
  exit /b 0
)

set "ROOT=%~dp0"
set "BACKEND_DIR=%ROOT%backend"
set "FRONTEND_DIR=%ROOT%frontend"

color 0B
title DROID SURVAIR MASTER TOOL

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
echo                   DROID SURVAIR MASTER TOOL
echo  ==================================================================
echo.
echo   [1] Full Fresh Install
echo   [2] Save New Dependencies ^(Update^)
echo   [3] Start the Portal
echo   [4] Exit
echo.
choice /c 1234 /n /m "Select an option (1-4): "

if errorlevel 4 goto end
if errorlevel 3 goto start_portal
if errorlevel 2 goto save_dependencies
if errorlevel 1 goto fresh_install

goto menu

:fresh_install
cls
echo [INFO] Starting Full Fresh Install...
echo.

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
  pause
  goto menu
)

if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend directory not found: "%FRONTEND_DIR%"
  pause
  goto menu
)

echo [STEP] Installing backend dependencies...
pushd "%BACKEND_DIR%"
if exist venv rmdir /s /q venv
py -3.10 -m venv venv
if errorlevel 1 (
  echo [ERROR] Failed to create Python virtual environment.
  popd
  pause
  goto menu
)

call "venv\Scripts\activate.bat"
pip install -r requirements.txt
if errorlevel 1 (
  echo [ERROR] Backend dependency installation failed.
  popd
  pause
  goto menu
)
popd
echo [SUCCESS] Backend setup complete.
echo.

echo [STEP] Installing frontend dependencies...
pushd "%FRONTEND_DIR%"
npm install
if errorlevel 1 (
  echo [ERROR] Frontend dependency installation failed.
  popd
  pause
  goto menu
)
popd
echo [SUCCESS] Frontend setup complete.
echo.
echo [DONE] Full Fresh Install completed successfully.
pause
goto menu

:save_dependencies
cls
echo [INFO] Saving new dependencies...
echo.

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
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
pip freeze > requirements.txt
if errorlevel 1 (
  echo [ERROR] Failed to save backend dependencies.
  popd
  pause
  goto menu
)
popd

echo [SUCCESS] Backend requirements.txt updated.
echo [INFO] Frontend packages are auto-saved to package.json.
pause
goto menu

:start_portal
cls
echo [INFO] Starting Droid Survair Portal...
echo.

if not exist "%BACKEND_DIR%" (
  echo [ERROR] Backend directory not found: "%BACKEND_DIR%"
  pause
  goto menu
)

if not exist "%FRONTEND_DIR%" (
  echo [ERROR] Frontend directory not found: "%FRONTEND_DIR%"
  pause
  goto menu
)

if not exist "%BACKEND_DIR%\venv\Scripts\activate.bat" (
  echo [ERROR] Backend venv not found. Run option 1 first.
  pause
  goto menu
)

start "Droid Survair - Backend" cmd /k "cd /d ""%BACKEND_DIR%"" && call venv\Scripts\activate.bat && uvicorn app.main:app --reload"
start "Droid Survair - Frontend" cmd /k "cd /d ""%FRONTEND_DIR%"" && npm run dev"

echo [SUCCESS] Backend and frontend windows launched.
pause
goto menu

:end
echo.
echo Exiting Droid Survair Master Tool...
timeout /t 1 >nul
endlocal
exit /b 0
