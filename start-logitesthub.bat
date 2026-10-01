@echo off
rem Starts the LogiTestHub web app + scheduler on http://localhost:5050.
rem Keep this window open: closing it stops the app and any scheduled runs.
title LogiTestHub server - keep this window open
cd /d F:\etail-test-ai\manager
echo LogiTestHub: http://localhost:5050   (needs XAMPP MySQL running)
echo Close this window to stop the server.
echo.
python app.py
echo.
echo LogiTestHub stopped.
pause
