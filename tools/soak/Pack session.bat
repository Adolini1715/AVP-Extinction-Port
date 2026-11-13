@echo off
rem Zips the newest session folder (sessions\<date_time>.zip) so it can be sent. Reads only that folder.
cd /d "%~dp0"
if not exist sessions\ (echo No sessions folder yet: play the game first. & pause & exit /b)
set NEWEST=
for /f "delims=" %%d in ('dir /b /ad /o-n sessions') do if not defined NEWEST set NEWEST=%%d
if not defined NEWEST (echo No session found. & pause & exit /b)
if not exist "sessions\%NEWEST%\privacy.txt" (echo That session is still open or was cut off. Start and close the game once more, then run this again. & pause & exit /b)
type "sessions\%NEWEST%\privacy.txt"
findstr /c:"clean" "sessions\%NEWEST%\privacy.txt" > nul || (echo Not packed: the privacy check did not pass. & pause & exit /b)
powershell -NoProfile -Command "Compress-Archive -Path 'sessions\%NEWEST%\*' -DestinationPath 'sessions\%NEWEST%.zip' -Force"
echo Ready to send: sessions\%NEWEST%.zip
pause
