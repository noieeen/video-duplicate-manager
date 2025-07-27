@echo off
REM Ensure PyInstaller is installed
pip show pyinstaller >nul 2>nul
if %errorlevel% neq 0 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

REM Locate the missing file for clip
set CLIP_FILE_PATH="C:\Users\Noie\Desktop\dev\viedo-management\.venv\Lib\site-packages\clip\bpe_simple_vocab_16e6.txt.gz"
if not exist %CLIP_FILE_PATH% (
    echo Missing file: bpe_simple_vocab_16e6.txt.gz. Please verify the path.
    pause
    exit /b
)

REM Build .exe for Windows
pyinstaller --onefile --noconsole --add-data "%CLIP_FILE_PATH%;clip" ui.py --name VideoDuplicateManager

REM Output location
echo Build complete. Check the dist folder for the output.
pause
