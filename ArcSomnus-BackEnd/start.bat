@echo off
chcp 65001 >nul
setlocal EnableExtensions
title ArcSomnus Backend
echo =======================================
echo     ArcSomnus Backend Starting...
echo =======================================
echo.

set "PY_CMD="
python --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=python"
    goto :FoundPython
)
py --version >nul 2>&1
if %errorlevel% equ 0 (
    set "PY_CMD=py"
    goto :FoundPython
)
echo [ERROR] No Python found.
pause
exit /b 1

:FoundPython
cd /d "%~dp0"
%PY_CMD% -m pip install -r requirements.txt
%PY_CMD% -c "import uvicorn" >nul 2>&1
if %errorlevel% neq 0 (
    echo [ERROR] uvicorn install failed.
    pause
    exit /b 1
)
echo [OK] Serving on http://0.0.0.0:9003
%PY_CMD% -m uvicorn server:app --host 0.0.0.0 --port 9003
pause
