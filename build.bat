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

:: ── Result ───────────────────────────────────────────────────────────────────
echo.
if exist "%~dp0dist\SlicerBridge.exe" (
    echo === BUILD SUCCESSFUL ===
    echo Output: %~dp0dist\SlicerBridge.exe
    echo.
    echo Next steps:
    echo   Run dist\SlicerBridge.exe
    echo   Pick your slicer, click Install, approve UAC
) else (
    echo === BUILD FAILED - exe not found ===
)

pause