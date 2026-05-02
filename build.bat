@echo off
cd /d "%~dp0"

echo === SlicerBridge Build ===
echo.

:: ── Detect Python ────────────────────────────────────────────────────────────
for /f "delims=" %%i in ('python -c "import sys; print(sys.executable)"') do set PYTHON_EXE=%%i
for /f "delims=" %%i in ('python -c "import sys; print(sys.version_info.major)"') do set PY_MAJOR=%%i
for /f "delims=" %%i in ('python -c "import sys; print(sys.version_info.minor)"') do set PY_MINOR=%%i
for /f "delims=" %%i in ('python -c "import os,sys; print(os.path.dirname(sys.executable))"') do set PYTHON_DIR=%%i

echo Python: %PYTHON_EXE%
echo Version: %PY_MAJOR%.%PY_MINOR%
echo Python dir: %PYTHON_DIR%
echo.

:: ── Extract VERSION from main.py ─────────────────────────────────────────────
for /f "tokens=3 delims= " %%v in ('findstr /R "^VERSION " main.py') do set APP_VERSION=%%v
:: Strip surrounding quotes
set APP_VERSION=%APP_VERSION:"=%
echo App version: %APP_VERSION%
echo.

:: ── PyInstaller ──────────────────────────────────────────────────────────────
python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller --quiet
    if errorlevel 1 (
        echo ERROR: Failed to install PyInstaller.
        pause & exit /b 1
    )
)

:: ── Clean ────────────────────────────────────────────────────────────────────
echo Cleaning previous build...
if exist build           rmdir /s /q build
if exist dist            rmdir /s /q dist
if exist SlicerBridge.spec del SlicerBridge.spec

:: ── Build ────────────────────────────────────────────────────────────────────
echo Building...
if exist icon.ico (
    python -m PyInstaller ^
        --onefile ^
        --noconsole ^
        --icon=icon.ico ^
        --name SlicerBridge ^
        main.py
) else (
    echo WARNING: icon.ico not found, building without icon.
    python -m PyInstaller ^
        --onefile ^
        --noconsole ^
        --name SlicerBridge ^
        main.py
)

if errorlevel 1 (
    echo.
    echo === BUILD FAILED ===
    pause & exit /b 1
)

:: ── Rename artifact to versioned name ────────────────────────────────────────
set VERSIONED=dist\SlicerBridge_v%APP_VERSION%.exe
if exist "dist\SlicerBridge.exe" (
    rename "dist\SlicerBridge.exe" "SlicerBridge_v%APP_VERSION%.exe"
    echo Renamed to: %VERSIONED%
)

:: ── Result ───────────────────────────────────────────────────────────────────
echo.
if exist "%~dp0%VERSIONED%" (
    echo === BUILD SUCCESSFUL ===
    echo Output: %~dp0%VERSIONED%
    echo.
    echo Next steps:
    echo   1. Run %VERSIONED%
    echo   2. Pick your slicer, click Install, approve UAC
    echo   3. Tag release on GitHub as v%APP_VERSION%
    echo   4. Upload %VERSIONED% as release asset
) else (
    echo === BUILD FAILED - exe not found ===
)

pause