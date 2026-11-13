@echo off
cd /d "%~dp0"
call .venv\Scripts\activate.bat
python -m PyInstaller --noconfirm --clean --windowed --name StockAI --collect-all pyqtgraph --collect-all safetensors main.py
if errorlevel 1 (echo Build failed. & pause & exit /b 1)
echo dist\StockAI is ready.
pause
