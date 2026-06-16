@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "DROID_ROOT=%CD%"
popd

set "DROID_EPT_TOOLS=%DROID_ROOT%\Tools\EPT"
set "DROID_EPT_CONDA=%USERPROFILE%\miniforge3\envs\droid-ept\Library\bin"
set "DROID_QGIS_UNTWINE=C:\Program Files\QGIS 3.22.8\apps\qgis-ltr\untwine.exe"
set "DROID_EPT_FOUND=0"

if exist "%DROID_EPT_TOOLS%" (
  set "PATH=%DROID_EPT_TOOLS%;%PATH%"
)
if exist "%DROID_EPT_CONDA%" (
  set "PATH=%DROID_EPT_CONDA%;%PATH%"
)

if not defined UNTWINE_EXE (
  if exist "%DROID_QGIS_UNTWINE%" (
    set "UNTWINE_EXE=%DROID_QGIS_UNTWINE%"
  )
)
if not defined UNTWINE_EXE (
  if exist "%DROID_EPT_TOOLS%\untwine.exe" (
    set "UNTWINE_EXE=%DROID_EPT_TOOLS%\untwine.exe"
  )
)
if not defined UNTWINE_EXE (
  if exist "%DROID_EPT_CONDA%\untwine.exe" (
    set "UNTWINE_EXE=%DROID_EPT_CONDA%\untwine.exe"
  )
)
if defined UNTWINE_EXE (
  set "DROID_EPT_FOUND=1"
  for %%I in ("%UNTWINE_EXE%") do set "PATH=%%~dpI;%PATH%"
  echo [OK] EPT converter: Untwine only
  echo      %UNTWINE_EXE%
)
if "%DROID_EPT_FOUND%"=="0" if not defined UNTWINE_EXE (
  where untwine >nul 2>&1
  if not errorlevel 1 (
    set "DROID_EPT_FOUND=1"
    set "UNTWINE_EXE=untwine"
    echo [OK] EPT converter: Untwine from PATH
  )
)

if "%DROID_EPT_FOUND%"=="0" (
  echo [WARN] EPT converter not found.
  echo        Expected "%DROID_QGIS_UNTWINE%" or put untwine.exe in "%DROID_EPT_TOOLS%".
  echo        Entwine and PDAL fallback are disabled for point cloud EPT conversion.
)

endlocal & (
  set "PATH=%PATH%"
  if defined UNTWINE_EXE set "UNTWINE_EXE=%UNTWINE_EXE%"
)
exit /b 0
