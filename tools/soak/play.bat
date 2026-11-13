@echo off
rem Optional: avpe.exe records a play-test session by itself every time it runs (sessions\<date_time>\).
rem This file only starts the game and shows where the session went. Nothing here reads your user name,
rem PC name or folders: the recorder never writes them, and removes them from anything that slips in.
cd /d "%~dp0"
start "" /wait avpe.exe
echo.
echo The session is in the "sessions" folder next to the game. Run "Pack session.bat" to zip the newest one.
pause
