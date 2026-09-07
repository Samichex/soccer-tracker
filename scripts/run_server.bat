@echo off
cd /d "%~dp0.."
if not exist logs mkdir logs
echo ---- starting %date% %time% ---- >> logs\server.log
".venv\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port 8000 >> logs\server.log 2>&1
echo ---- exited %date% %time% (code %errorlevel%) ---- >> logs\server.log
