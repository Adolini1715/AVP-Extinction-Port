# Aliens versus Predator: Extinction, PC port

A native PC port of **Aliens versus Predator: Extinction** (Zono, 2003, original Xbox), made by
static recompilation: the game's Xbox program is translated to C and compiled for x86-64, with
parts of it replaced by decompiled and modified C++.

**This repository contains no game data.** You build the port from your own copy of the game,
and the built game reads your own disc files when it runs.

## What you get

- **Runs natively on Windows**, OpenGL, no emulator. Any resolution, fullscreen or windowed,
  60 fps option.
- **Widescreen**: the battlefield fills a wide screen, with the HUD at the edges or centred, or
  the original 4:3 view.
- **Modern RTS controls**: left click to select (hold for the selection circle, drag for a box),
  right click for the default action, Shift+right click attack-move, Alt+right click waypoints,
  Ctrl+A select all on screen, control groups (Ctrl+1..4, twice to centre the camera), stance and
  order hotkeys, WASD or screen-edge scrolling, smooth wheel zoom, middle drag to turn and tilt.
  Every pad action has a key; a controller works at the same time.
- **Rebindable keys**: Options > Key Bindings in the game's own menus.
- **Mouse in the menus**: point, click, right click to go back, wheel to scroll; the button
  hints are clickable.
- **Saves**: quicksave (F5) and quickload (F9) in the game's own save list, plus autosaves.
- **Sharp fonts** at any resolution.
- **A launcher** for resolution, display, widescreen layout, frame cap and movies.
- **Optional session recording for problem reports** (off unless you tick it in the launcher): frame times, memory and game events, kept in the game
  folder, with no user name, PC name or paths; nothing is sent anywhere.

Gameplay is the Xbox game's: unit stats, difficulty and the missions are unchanged.

## Installing on Windows

You need the **USA retail** Xbox release of the game, as a disc image (`.iso`) or extracted to a
folder (the one with `default.xbe` in it). The PlayStation 2 release will not work.

1. Download this project (**Code > Download ZIP**, or the release's source zip) and unzip it to a
   folder whose path has no spaces, such as `C:\Games\AvPE` (the build tools cannot work in a
   path with spaces; the installer stops and says so).
2. Put your disc image, or your extracted disc folder, in the **`PUT YOUR GAME HERE`** folder.
3. Double-click **`Install.bat`**. If Windows asks whether to run it, choose Run.

The installer builds the game on your PC from your own copy. It downloads the build tools (about
300 MB: Python, GCC with CMake and Ninja, MinGit, SDL2, FFmpeg, each checked against a pinned
SHA-256), checks that your `default.xbe` is the release it supports, recompiles it with the
bundled toolkit (`xboxrecomp/`) and compiles `avpe.exe` with the decompilation
([AVP-Extinction-Decomp](https://github.com/Adolini1715/AVP-Extinction-Decomp), fetched at a pinned
commit). The first run takes 5 to 15 minutes; if it stops, run it again and it carries
on. If it fails, `install-log.txt` says where.

When it is done the launcher opens. Everything stays in the folder you unzipped: the build tools,
the game (in its **`AvP Extinction PC`** folder, with your game files copied into it) and your
saves. Next time start the game with **`Play AvP Extinction`** in that folder. Nothing is installed
on the system, nothing goes on the desktop and no administrator rights are needed. To uninstall,
delete the folder.

## Getting your game files

This project does not provide the game and does not link to copies of it. Make a disc image from
your own disc, with a modded Xbox or a PC drive that can read Xbox discs.

## Playing

Start the game with `Play AvP Extinction`. The `README.txt` in the game folder lists every key and
setting. To report a problem: press Ctrl+F12 when it happens, then run `Pack session.bat` in the
game folder and attach the zip it makes to an issue, with the build line from the top of
`avpe.log`.

## Safety

There are **no official prebuilt downloads**: build the port yourself from your own copy. Do not
run an `avpe.exe` someone else hands you. See `SECURITY.md`.

## How it works

- `xboxrecomp/` (a fork of [xboxrecomp](https://github.com/sp00nznet/xboxrecomp), MIT) lifts every
  function of `default.xbe` to C and supplies the Xbox kernel, memory layout and an NV2A
  pushbuffer renderer.
- [AVP-Extinction-Decomp](https://github.com/Adolini1715/AVP-Extinction-Decomp) holds decompiled C++ that replaces translated functions, checked against the
  original machine code, and its `platform/`: the PC layer (input, launcher, menus, fonts, saves,
  recording).
- `recomp/` and `symbols/` hold what the recompiler needs to know about this game (function
  seeds, calling conventions, names); `tools/` the analysis and verification tools.

## Status

Version 0.1.1 (0.1.0 was the first public release). [What this port changes, and what it took](docs/what-changed.md)
is the full record: what it adds, what it fixes, how it was built and checked, and what is known
not to work.

- All 7,994 of the game's functions are decompiled C++, each checked against the original
  machine code; 7,978 run in the port, the other 16 are served by the PC layer.
- 76 functions of Microsoft's Xbox libraries (files, saves, C runtime) are replaced the same
  way. The rest of the libraries are still translated from your disc at install time; replacing
  them is the current work.
- Every change is replayed against 16 recorded play sessions before it is kept.

## Known issues

- Only the USA retail release is supported.
- The installer is for Windows only. The port builds and runs on Linux, without an installer.
- A selection box released over the bottom HUD band, near the Dropship button, calls the
  Dropship, as the Xbox game's pointer does there.
