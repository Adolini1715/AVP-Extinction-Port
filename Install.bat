@echo off
rem Builds AvP: Extinction for PC from your own Xbox copy of the game.
rem 1. Put your disc image (.iso) or extracted disc folder in "PUT YOUR GAME HERE"
rem    (the installer reads only that folder).
rem 2. Double-click this file. The first run takes 5 to 15 minutes and needs the internet.
rem Everything stays in this folder; when it is done the launcher opens.
title AvP: Extinction PC installer
cd /d "%~dp0"
if not exist "%~dp0installer\install.py" (
  echo The installer files are missing. Unzip the whole download first, then run Install.bat
  echo from the unzipped folder.
  pause
  exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0installer\bootstrap.ps1"
if errorlevel 1 (
  echo.
  echo The installer stopped. See install-log.txt in this folder.
) else (
  echo.
  echo All done: the launcher is opening. Next time start the game with
  echo "Play AvP Extinction" in this folder.
)
echo.
pause
