@echo off
setlocal

echo Checking Python...
python --version
if errorlevel 1 (
    echo Python was not found. Install Python 3.10+ and make sure it is available on PATH.
    exit /b 1
)

if not exist venv\Scripts\activate.bat (
    echo Creating virtual environment...
    python -m venv venv
    if errorlevel 1 (
        echo Failed to create virtual environment. Make sure Python is installed and available on PATH.
        exit /b 1
    )
) else (
    echo Virtual environment already exists. Reusing venv.
)

echo Activating virtual environment...
call venv\Scripts\activate.bat
if errorlevel 1 (
    echo Failed to activate virtual environment.
    exit /b 1
)

echo Upgrading pip...
python -m pip install --upgrade pip
if errorlevel 1 (
    echo Failed to upgrade pip.
    exit /b 1
)

echo Installing dependencies...
pip install -r requirements.txt
if errorlevel 1 (
    echo Failed to install dependencies.
    echo.
    echo If rasterio or rio-cogeo fails on Windows, try:
    echo python -m pip install --upgrade pip wheel setuptools
    echo pip install -r requirements.txt
    exit /b 1
)

echo Creating data folders...
if not exist uploads mkdir uploads
if not exist cogs mkdir cogs
if not exist terrain3d mkdir terrain3d
if not exist templates mkdir templates
if not exist static mkdir static

echo.
echo Setup complete. Run run.bat to start the portal.
endlocal
