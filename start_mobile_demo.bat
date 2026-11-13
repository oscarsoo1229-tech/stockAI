@echo off
setlocal
cd /d "%~dp0"
if not exist .venv-mobile\Scripts\python.exe (
  py -3.13 -m venv .venv-mobile
  if errorlevel 1 goto failed
)
.venv-mobile\Scripts\python -c "import fastapi, uvicorn, pandas, httpx, pywebpush, tzdata, socksio" >nul 2>&1
if errorlevel 1 (
  .venv-mobile\Scripts\python -m pip install -r requirements_mobile.txt
  if errorlevel 1 goto failed
)
set "STOCKAI_MODE=demo"
set "STOCKAI_DB=mobile-demo.sqlite3"
set "STOCKAI_PASSWORD=StockAI-Demo-Local-1234"
set "VAPID_PUBLIC_KEY="
echo Open http://127.0.0.1:8765
echo Username: stockai
echo Password: StockAI-Demo-Local-1234
.venv-mobile\Scripts\python -m uvicorn mobile.server:app --host 127.0.0.1 --port 8765
if errorlevel 1 goto failed
exit /b 0
:failed
echo Check the error above and verify that Python 3.13 is installed.
pause
exit /b 1
