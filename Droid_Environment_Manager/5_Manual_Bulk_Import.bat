@echo off
setlocal EnableExtensions
title Droid Cloud - Manual Bulk Import

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0Manual_Bulk_Import.ps1" %*
if errorlevel 1 (
  echo.
  echo Import finished with errors.
  pause
  exit /b 1
)
echo.
pause
