@echo off
rem Starts the LogiTestHub web app + scheduler on http://localhost:5050.
rem Keep this window open: closing it stops the app and any scheduled runs.
title LogiTestHub server - keep this window open
cd /d F:\etail-test-ai\manager
rem The Anthropic key is kept as LTH_ANTHROPIC_API_KEY (setx), so only this server gets it as ANTHROPIC_API_KEY.
rem A global ANTHROPIC_API_KEY would make every Claude Code session on this PC bill the API instead of the subscription.
if defined LTH_ANTHROPIC_API_KEY set "ANTHROPIC_API_KEY=%LTH_ANTHROPIC_API_KEY%"
echo LogiTestHub: http://localhost:5050   (needs XAMPP MySQL running)
echo Close this window to stop the server.
echo.
python app.py
echo.
echo LogiTestHub stopped.
pause
