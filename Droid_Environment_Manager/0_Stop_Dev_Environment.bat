@echo off
setlocal EnableExtensions

echo.
echo ========================================
echo   Stop DEV Environment Only
echo   (Live 8000 / 4173 will NOT be touched)
echo ========================================
echo.

set "STOPPED=0"

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8001 " ^| findstr LISTENING') do (
  echo Stopping Dev Backend PID %%P on port 8001...
  taskkill /PID %%P /F >nul 2>&1
  set "STOPPED=1"
)

for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":5173 " ^| findstr LISTENING') do (
  echo Stopping Dev Frontend PID %%P on port 5173...
  taskkill /PID %%P /F >nul 2>&1
  set "STOPPED=1"
)

if "%STOPPED%"=="0" (
  echo No Dev server found on ports 8001 or 5173.
) else (
  echo Dev servers stopped.
)

echo.
pause
