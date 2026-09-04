@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo Starting PEA transformer data collection...
echo Keep this window open. The app will prevent sleep and screen-off.
echo Do not close the laptop lid.
python -m pip install -r requirements.txt -q
start "" cmd /c "timeout /t 2 /nobreak >nul && start http://127.0.0.1:5050"
python server.py
pause
