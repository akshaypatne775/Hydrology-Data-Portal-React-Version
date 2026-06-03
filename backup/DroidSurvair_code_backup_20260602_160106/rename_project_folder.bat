@echo off
setlocal EnableExtensions

set "NEW_NAME=Droid Survair Cloud Portal"
set "CURRENT_DIR=%~dp0"

for %%I in ("%CURRENT_DIR%.") do (
  set "CURRENT_NAME=%%~nxI"
  set "PARENT_DIR=%%~dpI"
)

echo.
echo  ================================================================
echo                  RENAME DROID SURVAIR PROJECT
echo  ================================================================
echo.
echo [INFO] Current folder: %CURRENT_NAME%
echo [INFO] New folder:     %NEW_NAME%
echo.
echo [IMPORTANT] Close backend, frontend, VS Code terminals, and any Explorer
echo             window opened inside this folder before continuing.
echo.
pause

cd /d "%PARENT_DIR%"
if errorlevel 1 (
  echo [ERROR] Could not move to parent folder.
  pause
  exit /b 1
)

if /I "%CURRENT_NAME%"=="%NEW_NAME%" (
  echo [OK] Folder already has the correct name.
  pause
  exit /b 0
)

if exist "%NEW_NAME%" (
  echo [ERROR] A folder named "%NEW_NAME%" already exists.
  echo [FIX] Rename or move that folder first.
  pause
  exit /b 1
)

ren "%CURRENT_NAME%" "%NEW_NAME%"
if errorlevel 1 (
  echo [ERROR] Rename failed. Close apps using this folder and try again.
  pause
  exit /b 1
)

echo.
echo [SUCCESS] Folder renamed to "%NEW_NAME%".
echo [NEXT] Open the project from:
echo        %PARENT_DIR%%NEW_NAME%
echo.
pause
endlocal
