@echo off
REM Ensure PyInstaller is installed
pip show pyinstaller >nul 2>nul
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Build .exe for Windows
pyinstaller --onefile --noconsole ui.py --name VideoDuplicateManager

REM Output location
echo Build complete. Check the dist folder for the output.
pause
