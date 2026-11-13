@echo off
rem Automated play test: Claude's play tester plays each mission through the real mouse and keyboard path
rem (selection, attacks, attack-move, stances, the clickable wheel, groups, abilities, camera, minimap, pause),
rem chases the minimap objectives, and records how far it got, what happened after the mission ended,
rem fps and crashes. Results: playtest\results.csv, playtest\logs, playtest\telemetry.
rem Leave the PC alone while it runs: it moves its own pointer inside the game window only.
cd /d "%~dp0"
set MINUTES=35
rem after WIN_AFTER minutes the tester uses the game's own Cheat Win, so every mission reaches its ending
set WIN_AFTER=25
set INVULN=1
set MISSIONS=M01 A01 P01 M02 A02 P02
set /a SECS=%MINUTES%*60
set /a WINSECS=%WIN_AFTER%*60

set AVPE_NO_SETTINGS=1
set AVPE_PC=1
set AVPE_PC_RES=2560x1440
set AVPE_PC_SIZE=1920x1080
set AVPE_PC_FULLSCREEN=0
set AVPE_PC_FPS=120
set AVPE_LEVEL_AT=2
set AVPE_SOAK=%SECS%
set AVPE_SOAK_LOAD=300
set AVPE_SOAK_OUT=%~dp0playtest\fps.csv
set AVPE_SOAK_SHOTS=%~dp0playtest\shots
set AVPE_SAVE_DIR=%~dp0playtest\saves
set AVPE_PLAYTEST=7
set AVPE_PLAYTEST_OUT=%~dp0playtest\results.csv
set AVPE_TELEMETRY_EVERY=300
set AVPE_PLAYTEST_WIN=%WINSECS%
set AVPE_PLAYTEST_DEBUG=1
if "%INVULN%"=="1" (set AVPE_PLAYTEST_CHEATS=invuln& set AVPE_DIFF_FROM_ALIEN=0& set AVPE_DIFF_FROM_MARINE=0& set AVPE_DIFF_FROM_PREDATOR=0)
if not exist playtest\logs mkdir playtest\logs
if not exist playtest\telemetry mkdir playtest\telemetry
if not exist playtest\saves mkdir playtest\saves
if not exist playtest\shots mkdir playtest\shots
echo playtest start %DATE% %TIME% (%MINUTES% min per mission, invincible=%INVULN%) >> playtest\summary.txt
for %%M in (%MISSIONS%) do call :run %%M
echo playtest done %DATE% %TIME% >> playtest\summary.txt
exit /b

:run
title AvPE play test: %1
set AVPE_LEVEL=%1/background.tbd
set AVPE_SOAK_TAG=%1
set AVPE_TELEMETRY=%~dp0playtest\telemetry\%1.jsonl
if exist avpe.log del avpe.log
echo %1 start %TIME% >> playtest\summary.txt
start "" /wait avpe-playtest.exe
echo %1 exit %ERRORLEVEL% %TIME% >> playtest\summary.txt
if exist avpe.log move /y avpe.log playtest\logs\%1.log > nul
exit /b
