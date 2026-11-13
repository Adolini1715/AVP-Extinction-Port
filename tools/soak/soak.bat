@echo off
rem Unattended soak test of the PC port: every mission in real time on your GPU.
rem Each mission skips the menus, closes its briefing, lets the engine's AI play your side,
rem sweeps the camera, and runs SECS seconds. Results: soak\results.csv, soak\logs, soak\shots.
rem A run that stops drawing for 30 s or never reaches its mission is ended by the game itself.
cd /d "%~dp0"
set SECS=90
set RENDER=3840x2160
set WINDOW=1920x1080
set FPSCAP=1000
set MISSIONS=M01 M02 M03 M05 M06 M09 M10 A01 A02 A03 A04 A06 A07 A08 P01 P02 P03 P04 P06 P08 P11 T01 T02 T03 T04 T05

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
set AVPE_SOAK_OUT=%~dp0soak\results.csv
set AVPE_SOAK_SHOTS=%~dp0soak\shots
set AVPE_SAVE_DIR=%~dp0soak\saves
set AVPE_BOT=600
set AVPE_BOT_MODE=ai
if not exist soak\logs mkdir soak\logs
if not exist soak\shots mkdir soak\shots
if not exist soak\saves mkdir soak\saves
echo mission,result,live_s,frames,avg_fps,p50_ms,p99_ms,worst_ms,hitch50,hitch100,hitch250,load_s,peak_mb,gpu > soak\results.csv
echo soak start %DATE% %TIME% (%SECS% s, render %RENDER%, cap %FPSCAP%) > soak\summary.txt
for %%M in (%MISSIONS%) do call :run %%M
echo soak done %DATE% %TIME% >> soak\summary.txt
exit /b

:run
title AvPE soak: %1
set AVPE_LEVEL=%1/background.tbd
set AVPE_SOAK_TAG=%1
if exist avpe.log del avpe.log
echo %1 start %TIME% >> soak\summary.txt
start "" /wait avpe-soak.exe
echo %1 exit %ERRORLEVEL% %TIME% >> soak\summary.txt
if exist avpe.log move /y avpe.log soak\logs\%1.log > nul
exit /b
