@echo off
title LogiTestHub - start dev server
echo Starting LogiTestHub manager on http://localhost:5050
echo   Needs: MySQL (XAMPP) running, and manager\requirements.txt installed for this Python.
echo   Close this window to stop the server.
echo.

cd /d %~dp0manager
if defined LTH_ANTHROPIC_API_KEY set "ANTHROPIC_API_KEY=%LTH_ANTHROPIC_API_KEY%"
python app.py

echo.
echo LogiTestHub stopped.
pause
