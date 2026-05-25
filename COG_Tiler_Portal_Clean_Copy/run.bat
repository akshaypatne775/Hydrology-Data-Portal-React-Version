@echo off
setlocal

if not exist venv\Scripts\activate.bat (
    echo Virtual environment not found. Run install.bat first.
    exit /b 1
)

call venv\Scripts\activate.bat
uvicorn main:app --reload --port 8000

endlocal
