@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "DROID_ROOT=%CD%"
popd

set "DROID_EPT_TOOLS=%DROID_ROOT%\Tools\EPT"
set "DROID_EPT_CONDA=%USERPROFILE%\miniforge3\envs\droid-ept\Library\bin"
set "DROID_EPT_FOUND=0"

if exist "%DROID_EPT_TOOLS%" (
  set "PATH=%DROID_EPT_TOOLS%;%PATH%"
)
if exist "%DROID_EPT_CONDA%" (
  set "PATH=%DROID_EPT_CONDA%;%PATH%"
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
if not defined ENTWINE_EXE (
  if exist "%DROID_EPT_TOOLS%\entwine.exe" (
    set "ENTWINE_EXE=%DROID_EPT_TOOLS%\entwine.exe"
  )
)
if not defined ENTWINE_EXE (
  if exist "%DROID_EPT_CONDA%\entwine.exe" (
    set "ENTWINE_EXE=%DROID_EPT_CONDA%\entwine.exe"
  )
)
if not defined PDAL_EXE (
  if exist "%DROID_EPT_TOOLS%\pdal.exe" (
    set "PDAL_EXE=%DROID_EPT_TOOLS%\pdal.exe"
  )
)
if not defined PDAL_EXE (
  if exist "%DROID_EPT_CONDA%\pdal.exe" (
    set "PDAL_EXE=%DROID_EPT_CONDA%\pdal.exe"
  )
)

if not defined PDAL_EXE if defined OSGEO4W_ROOT if exist "%OSGEO4W_ROOT%\bin\pdal.exe" (
  set "PDAL_EXE=%OSGEO4W_ROOT%\bin\pdal.exe"
)
if not defined PDAL_EXE if exist "C:\OSGeo4W\bin\pdal.exe" (
  set "PDAL_EXE=C:\OSGeo4W\bin\pdal.exe"
)
if not defined PDAL_EXE if exist "C:\Program Files\QGIS 3.44.8\bin\pdal.exe" (
  set "PDAL_EXE=C:\Program Files\QGIS 3.44.8\bin\pdal.exe"
)

if defined ENTWINE_EXE (
  set "DROID_EPT_FOUND=1"
  echo [OK] EPT converter: Entwine
  echo      %ENTWINE_EXE%
) else (
  where entwine >nul 2>&1
  if not errorlevel 1 (
    set "DROID_EPT_FOUND=1"
    echo [OK] EPT converter: Entwine from PATH
  )
)

if "%DROID_EPT_FOUND%"=="0" if defined UNTWINE_EXE (
  set "DROID_EPT_FOUND=1"
  echo [OK] EPT converter fallback: Untwine
  echo      %UNTWINE_EXE%
)
if "%DROID_EPT_FOUND%"=="0" if not defined UNTWINE_EXE (
  where untwine >nul 2>&1
  if not errorlevel 1 (
    set "DROID_EPT_FOUND=1"
    echo [OK] EPT converter fallback: Untwine from PATH
  )
)

if "%DROID_EPT_FOUND%"=="0" if defined PDAL_EXE (
  set "DROID_EPT_FOUND=1"
  echo [OK] EPT converter: PDAL writers.ept
  echo      %PDAL_EXE%
)
if "%DROID_EPT_FOUND%"=="0" if not defined PDAL_EXE (
  where pdal >nul 2>&1
  if not errorlevel 1 (
    set "DROID_EPT_FOUND=1"
    echo [OK] EPT converter: PDAL from PATH
  )
)

if "%DROID_EPT_FOUND%"=="0" (
  echo [WARN] EPT converter not found.
  echo        Put untwine.exe in "%DROID_EPT_TOOLS%" or install Untwine/Entwine/PDAL.
  echo        Point cloud upload will be accepted, but conversion needs one EPT converter.
)

endlocal & (
  set "PATH=%PATH%"
  if defined UNTWINE_EXE set "UNTWINE_EXE=%UNTWINE_EXE%"
  if defined ENTWINE_EXE set "ENTWINE_EXE=%ENTWINE_EXE%"
  if defined PDAL_EXE set "PDAL_EXE=%PDAL_EXE%"
)
exit /b 0
