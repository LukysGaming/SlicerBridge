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
        --onedir ^
        --noconsole ^
        --icon=icon.ico ^
        --name SlicerBridge ^
        --collect-binaries python%PY_MAJOR%%PY_MINOR% ^
        main.py
) else (
    echo WARNING: icon.ico not found, building without icon.
    python -m PyInstaller ^
        --onedir ^
        --noconsole ^
        --name SlicerBridge ^
        --collect-binaries python%PY_MAJOR%%PY_MINOR% ^
        main.py
)

if errorlevel 1 (
    echo.
    echo === BUILD FAILED ===
    pause & exit /b 1
)

:: ── Copy Python DLL manually (fixes "Failed to load python3xx.dll") ──────────
echo.
echo Checking Python DLL...
set DLL_NAME=python%PY_MAJOR%%PY_MINOR%.dll
set DLL_SRC=%PYTHON_DIR%\%DLL_NAME%
set DLL_DEST=dist\SlicerBridge\_internal\%DLL_NAME%

if exist "%DLL_SRC%" (
    if not exist "%DLL_DEST%" (
        echo Copying %DLL_NAME% to _internal...
        copy /y "%DLL_SRC%" "%DLL_DEST%" >nul
        if errorlevel 1 (
            echo WARNING: Failed to copy DLL. Trying dist root...
            copy /y "%DLL_SRC%" "dist\SlicerBridge\%DLL_NAME%" >nul
        ) else (
            echo OK: DLL copied to _internal.
        )
    ) else (
        echo OK: DLL already present.
    )
) else (
    echo WARNING: %DLL_NAME% not found in %PYTHON_DIR%
    echo Searching in PATH...
    for /f "delims=" %%f in ('where %DLL_NAME% 2^>nul') do (
        echo Found: %%f
        copy /y "%%f" "%DLL_DEST%" >nul
        goto :dll_done
    )
    echo WARNING: Could not find %DLL_NAME% — the exe may fail to start.
)
:dll_done

:: ── Also copy from _internal to root as fallback ─────────────────────────────
if exist "%DLL_DEST%" (
    if not exist "dist\SlicerBridge\%DLL_NAME%" (
        copy /y "%DLL_DEST%" "dist\SlicerBridge\%DLL_NAME%" >nul
    )
)

:: ── Result ───────────────────────────────────────────────────────────────────
echo.
if exist "%~dp0dist\SlicerBridge\SlicerBridge.exe" (
    echo === BUILD SUCCESSFUL ===
    echo Output: %~dp0dist\SlicerBridge\SlicerBridge.exe
    echo.
    echo Next steps:
    echo   Run dist\SlicerBridge\SlicerBridge.exe
    echo   Pick your slicer, click Install, approve UAC
) else (
    echo === BUILD FAILED - exe not found ===
)

pause