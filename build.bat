@echo off
cd /d "%~dp0"

echo === SlicerBridge Build ===
echo.

python -m PyInstaller --version >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo Building...
python -m PyInstaller --onefile --noconsole --name SlicerBridge main.py

echo.
if exist "%~dp0dist\SlicerBridge.exe" (
    echo === BUILD SUCCESSFUL ===
    echo Output: %~dp0dist\SlicerBridge.exe
    echo.
    echo Next steps:
    echo   Run SlicerBridge.exe - it will ask where to install itself
    echo   Pick your slicer, click Install, approve UAC
) else (
    echo BUILD FAILED - check output above
)

pause