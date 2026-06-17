@echo off
setlocal EnableExtensions

pushd "%~dp0.."
set "DROID_ROOT=%CD%"
popd

set "DROID_EPT_TOOLS=%DROID_ROOT%\Tools\EPT"
set "DROID_EPT_CONDA=%USERPROFILE%\miniforge3\envs\droid-ept\Library\bin"
set "DROID_OSGEO4W_BAT=C:\OSGeo4W\OSGeo4W.bat"
set "DROID_OSGEO4W64_BAT=C:\OSGeo4W64\OSGeo4W.bat"
set "DROID_QGIS_328_BAT=C:\Program Files\QGIS 3.28.0\OSGeo4W.bat"
set "DROID_QGIS_322_BAT=C:\Program Files\QGIS 3.22.8\OSGeo4W.bat"
set "DROID_QGIS_UNTWINE=C:\Program Files\QGIS 3.22.8\apps\qgis-ltr\untwine.exe"
set "DROID_EPT_FOUND=0"
set "DROID_COPC_FOUND=0"

if exist "%DROID_EPT_TOOLS%" (
  set "PATH=%DROID_EPT_TOOLS%;%PATH%"
)
if exist "%DROID_EPT_CONDA%" (
  set "PATH=%DROID_EPT_CONDA%;%PATH%"
)

if not defined OSGEO4W_BAT (
  if exist "%DROID_OSGEO4W_BAT%" set "OSGEO4W_BAT=%DROID_OSGEO4W_BAT%"
)
if not defined OSGEO4W_BAT (
  if exist "%DROID_OSGEO4W64_BAT%" set "OSGEO4W_BAT=%DROID_OSGEO4W64_BAT%"
)
if not defined OSGEO4W_BAT (
  if exist "%DROID_QGIS_328_BAT%" set "OSGEO4W_BAT=%DROID_QGIS_328_BAT%"
)
if not defined OSGEO4W_BAT (
  if exist "%DROID_QGIS_322_BAT%" set "OSGEO4W_BAT=%DROID_QGIS_322_BAT%"
)
if defined OSGEO4W_BAT (
  for %%I in ("%OSGEO4W_BAT%") do (
    if exist "%%~dpIbin" set "PATH=%%~dpIbin;%PATH%"
  )
  echo [OK] OSGeo shell:
  echo      %OSGEO4W_BAT%
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

if defined PDAL_EXE (
  call :CheckCopc "%PDAL_EXE%"
) else (
  call :TryCopcPdal "%DROID_EPT_TOOLS%\pdal.exe"
  call :TryCopcPdal "%USERPROFILE%\miniforge3\envs\droid-copc\Library\bin\pdal.exe"
  call :TryCopcPdal "%USERPROFILE%\miniforge3\envs\droid-ept\Library\bin\pdal.exe"
  call :TryCopcPdal "C:\OSGeo4W\bin\pdal.exe"
  call :TryCopcPdal "C:\OSGeo4W64\bin\pdal.exe"
  call :TryCopcPdal "C:\Program Files\QGIS 3.28.0\bin\pdal.exe"
  call :TryCopcPdal "C:\Program Files\QGIS 3.22.8\bin\pdal.exe"
)

if not defined PDAL_EXE (
  where pdal >nul 2>&1
  if not errorlevel 1 (
    for /f "delims=" %%P in ('where pdal') do if not defined PDAL_EXE call :TryCopcPdal "%%P"
  )
)

if defined PDAL_EXE (
  for %%I in ("%PDAL_EXE%") do set "PATH=%%~dpI;%PATH%"
  if "%DROID_COPC_FOUND%"=="1" (
    echo [OK] COPC converter: PDAL with writers.copc
    echo      %PDAL_EXE%
  ) else (
    echo [WARN] PDAL found, but writers.copc is not available.
    echo        COPC output will not be created until a COPC-capable PDAL is installed.
    echo        Current PDAL: %PDAL_EXE%
  )
) else (
  echo [WARN] PDAL not found. COPC output is disabled.
  echo        Install OSGeo4W/PDAL with writers.copc, then set PDAL_EXE.
)

endlocal & set "PATH=%PATH%" & set "OSGEO4W_BAT=%OSGEO4W_BAT%" & set "UNTWINE_EXE=%UNTWINE_EXE%" & set "PDAL_EXE=%PDAL_EXE%" & set "DROID_COPC_FOUND=%DROID_COPC_FOUND%"
exit /b 0

:TryCopcPdal
if defined PDAL_EXE exit /b 0
if not exist "%~1" exit /b 0
set "PDAL_EXE=%~1"
call :CheckCopc "%~1"
exit /b 0

:CheckCopc
if not exist "%~1" exit /b 0
"%~1" --drivers 2>nul | findstr /I /C:"writers.copc" >nul
if not errorlevel 1 set "DROID_COPC_FOUND=1"
exit /b 0
