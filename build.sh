#!/bin/bash

# Ensure required tools are installed
if ! command -v pyinstaller &> /dev/null
then
    echo "PyInstaller could not be found. Installing..."
    pip install pyinstaller
fi

# Create .exe for Windows
pyinstaller --onefile --noconsole ui.py --name VideoDuplicateManager

# Create .dmg for MacOS
if [[ $(uname) == "Darwin" ]]; then
    echo "Building .dmg for MacOS..."
    pyinstaller --onefile --noconsole ui.py --name VideoDuplicateManager
    hdiutil create -volname "VideoDuplicateManager" -srcfolder dist/VideoDuplicateManager.app -ov -format UDZO -o dist/VideoDuplicateManager.dmg
else
    echo "Skipping .dmg creation as this is not a MacOS system."
fi

# Output location
echo "Build complete. Check the 'dist' folder for the output."
