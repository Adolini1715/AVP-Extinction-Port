@echo off
rem Overnight soak (20 minutes per mission) of the PC port: every mission in real time on your GPU.
rem Each mission skips the menus, closes its briefing, lets the engine's AI play your side,
rem sweeps the camera, and runs SECS seconds. Results: soaklong\results.csv, soaklong\logs, soaklong\shots.
rem A run that stops drawing for 30 s or never reaches its mission is ended by the game itself.
cd /d "%~dp0"
set SECS=1200
set RENDER=3840x2160
set WINDOW=1920x1080
set FPSCAP=1000
set MISSIONS=M02 A02 P08 M09

set AVPE_NO_SETTINGS=1
set AVPE_PC=1
set AVPE_PC_RES=%RENDER%
set AVPE_PC_SIZE=%WINDOW%
set AVPE_PC_FULLSCREEN=0
set AVPE_PC_FPS=%FPSCAP%
set AVPE_NOVSYNC=1
set AVPE_FATAL_EXIT=1
set AVPE_LEVEL_AT=2
set AVPE_REPLAY_PAD=%~dp0soak\soak.pad
set AVPE_SOAK=%SECS%
set AVPE_SOAK_OUT=%~dp0soaklong\results.csv
set AVPE_SOAK_SHOTS=%~dp0soaklong\shots
set AVPE_SAVE_DIR=%~dp0soaklong\saves
set AVPE_BOT=600
set AVPE_BOT_MODE=ai
if not exist soaklong\logs mkdir soaklong\logs
if not exist soaklong\shots mkdir soaklong\shots
if not exist soaklong\saves mkdir soaklong\saves
echo mission,result,live_s,frames,avg_fps,p50_ms,p99_ms,worst_ms,hitch50,hitch100,hitch250,load_s,peak_mb,gpu > soaklong\results.csv
echo soak start %DATE% %TIME% (%SECS% s, render %RENDER%, cap %FPSCAP%) > soaklong\summary.txt
for %%M in (%MISSIONS%) do call :run %%M
echo soak done %DATE% %TIME% >> soaklong\summary.txt
exit /b

:run
title AvPE long soak: %1
set AVPE_LEVEL=%1/background.tbd
set AVPE_SOAK_TAG=%1
if exist avpe.log del avpe.log
echo %1 start %TIME% >> soaklong\summary.txt
start "" /wait avpe-soak.exe
echo %1 exit %ERRORLEVEL% %TIME% >> soaklong\summary.txt
if exist avpe.log move /y avpe.log soaklong\logs\%1.log > nul
exit /b
