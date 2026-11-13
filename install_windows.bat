@echo off
setlocal
cd /d "%~dp0"
py -3.13 --version >nul 2>&1
if errorlevel 1 (echo Python 3.13 required. Install 64-bit Python with launcher. & pause & exit /b 1)
if not exist .venv py -3.13 -m venv .venv
call .venv\Scripts\activate.bat
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if errorlevel 1 (echo Dependency installation failed. & pause & exit /b 1)
echo Installed. NVIDIA CUDA setup is described in README.md.
pause
