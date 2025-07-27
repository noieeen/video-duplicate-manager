# Video Duplicate Manager

## Overview
Video Duplicate Manager is a Python-based application that identifies and manages duplicate video files using advanced machine learning techniques.

## Features
- Extracts frames from videos for comparison.
- Identifies duplicate videos based on similarity.
- Moves duplicates to a specified folder.
- Provides a user-friendly GUI built with PyQt5.

## Installation

### Prerequisites
- Python 3.12 or later
- Pip

### Steps
1. Clone the repository:
   ```bash
   git clone https://github.com/noieeen/video-duplicate-manager.git
   cd video-duplicate-manager
   ```
2. Create and activate a virtual environment:
   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```
3. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

## Usage

### Run the Application
1. Activate the virtual environment:
   ```bash
   .venv\Scripts\activate
   ```
2. Run the application:
   ```bash
   python ui.py
   ```

### Build Executable
1. Ensure PyInstaller is installed:
   ```bash
   pip install pyinstaller
   ```
2. Run the build script:
   ```bash
   build.bat
   ```
3. Check the `dist` folder for the output executable.

## License
This project is licensed under the MIT License.
