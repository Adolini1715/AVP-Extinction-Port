# What this port changes, and what it took

This is a record of everything in the v0.1 release of the Aliens versus Predator: Extinction PC
port: what it adds to the original Xbox game, what it fixes, and the work behind it. It only
covers what ships in this repository and in the decompilation it builds on. Things that were
tried and dropped are noted at the end.

## At a glance

- **A month of work**, October 14 to November 13, 2026.
- **225 commits** to the port and its tools and **348 commits** to the decompilation it builds on.
- **All 7,994 of the game's functions** rewritten as C++ by the decompilation, across 604 source
  files, and every one checked against the original machine code. **7,978** run in the port in
  place of their machine-translated versions; the other 16 (pause handling, controller input,
  level loading, the movie thread) are served by the PC layer's own versions.
- **76 functions of Microsoft's Xbox libraries** (files, save games, the C runtime) replaced
  and checked the same way.
- **18,763 passing function checks** on record, and **16 recorded play sessions** replayed
  after every batch of changes.
- **10 faults fixed in the recompiler toolkit and runtime**, and **4 behaviours of the original
  game** corrected for play on a PC.

The whole project was built with AI assistance (Claude), directed and play-tested by one person.
No leaked or proprietary source code was used. It is a first release. More eyes on it are
welcome, and so are bug reports.

## How the port works

The game ships as one Xbox program, `default.xbe`. The port does not emulate it. Instead:

1. **Static recompilation.** A fork of the [xboxrecomp](https://github.com/sp00nznet/xboxrecomp)
   toolkit translates every function in your copy of `default.xbe` into C, which is compiled
   for 64-bit Windows or Linux. The toolkit also stands in for the Xbox kernel and memory
   layout. On its own this already runs the game.
2. **Decompiled code swapped in.** A companion decompilation rewrites the game as C++ with the
   game's own structures and names (the names come from the symbol table the PlayStation 2
   release shipped with). Each rewritten function replaces its translated version. The whole
   game now runs this way; what is still translated is most of Microsoft's library code, which is generated on your PC from your own disc and never
   distributed.
3. **A PC layer.** Drawing, sound, input and timing are done by code written for the port:
   the game's Direct3D calls, with the 21 vertex shaders it assembles at run time, become
   OpenGL 3.3 and GLSL; its DirectSound calls go to an SDL mixer; movies are decoded by FFmpeg.

No game code or data is in this repository. You build the port from your own disc.

## What you get

### Running on a PC

- Native Windows build (and Linux), no emulator.
- Any resolution: the game is drawn at the size of your window or screen.
- Widescreen: the battlefield fills a wide screen with a wider view, not a stretched one. The
  HUD sits at the edges of the screen or stays centred, or you can keep the original 4:3
  picture. The title screen is pillarboxed and the menus spread to the screen's edges.
- A frame cap of your choice, 60 by default. The Xbox game held each frame for 2, 3 or 4
  screen refreshes, so a frame that took 33 ms was shown for 49. The mission clock follows
  real time at any rate.
- Borderless fullscreen that behaves on a modern desktop (Alt+Tab, the Snipping Tool and HDR
  displays), a window, or exclusive fullscreen. Changes apply at once.
- The logo and intro movies, from your own disc files. Any key skips the one playing.
- A Windows installer: put your disc image or extracted disc in a folder and double-click
  `Install.bat`. It fetches pinned, checksummed build tools into its own folder (no
  administrator rights), checks that the game is the release it supports and says so if it is
  not, builds the game from your copy, and leaves it in a game folder with a desktop shortcut.
- A launcher for resolution, display mode, widescreen layout, frame cap and movies. Dropping a
  newer package on it updates the game and keeps your saves and settings.

### Controls

- A modern RTS layout for mouse and keyboard. Left click selects (hold for the game's
  selection circle, drag for a box); right click is the default action; Shift+right click
  attack-moves; Alt+right click sets waypoints.
- Ctrl+A selects everything of yours on screen. Control groups: Ctrl+1 to 4 makes one,
  Shift+Ctrl adds the selection to it, the number selects it, and twice quickly centres the
  camera on it.
- Right drag grabs the map: the ground under the pointer follows your hand, in any direction,
  at any spot on the screen.
- Stance and order hotkeys, WASD or screen-edge scrolling, middle drag to turn and tilt the
  camera, and a wheel zoom that moves the same amount at any frame rate.
- Clicking the minimap moves the camera there, and the HUD's buttons answer the mouse: the
  Dropship button opens the Uplink, where every row can be clicked.
- Every mission action can be rebound, and the mouse buttons swapped.
- A controller works at the same time as the keyboard and mouse.

### Menus and interface

- **Mouse in every menu**: point to highlight, click to choose, right click to go back, wheel
  to scroll. The button hints at the bottom of the screen are clickable too.
- **Options > PC Settings and Key Bindings**, built as pages of the game's own menu system, in
  the main menu and the pause menu. Changes apply at once.
- **A Controls page** with a keyboard and mouse picture that follows the keys bound now.
- **PC button prompts**: tutorials and the HUD name keys instead of pad buttons, and follow
  your bindings.
- **Sharp fonts**: the game's letters, 8 to 12 pixels tall on the Xbox, drawn again at 4x.
- **Esc skips a mission intro**, and there is a Quit Game item on the main menu.

### Saves

- Quicksave (F5) and quickload (F9), kept in the game's own save list.
- Autosaves: three slots used in turn, a minute into a mission and then every 10 minutes of
  play.

### Unchanged on purpose

Unit stats, costs, difficulty, missions and AI are the original's. Nothing about how the game
plays was rebalanced.

### For problem reports

- Optional session recording, off unless you tick it in the launcher: frame times, memory and game events, with F12 pictures and
  Ctrl+F12 marks, packed by `Pack session.bat`. It is kept in the game folder, records no user
  name, PC name or outside path, and sends nothing anywhere.

## Bugs fixed

### In the original game

These never showed on an Xbox, or showed less. A PC makes them plain.

- **The pointer moved twice.** In missions the game applies the pointer's motion in two
  places each frame. With a pad's stick that is just a speed. With a mouse the pointer ran
  ahead of the hand at double rate. The port halves the motion it reports, so the pointer
  tracks the mouse one to one.
- **A zoom with no limits.** The camera's zoom was a developer control on the keypad, and it
  adds to the eye's distance with no bounds at all. Zoomed out past about twice the starting
  distance the far clipping plane cuts the terrain away from the top of the screen; zoomed in
  past zero the distance goes negative and the eye flips under the map. The port holds the
  distance within fixed limits and keeps the viewing angles.
- **Three logo movies at once.** The front end starts the three logo movies on three threads
  at the same moment and leaves the Xbox's movie player to show one at a time. The port plays
  them in the order the game made them, so the next logo no longer starts over the one
  showing.
- **Uneven frame pacing.** Described above: the game's presentation step rounds every frame
  up to a whole number of 60 Hz refreshes, which turned a steady 30 frames a second into a
  mix of 49 ms and 33 ms frames whenever a frame ran slightly long.

### In the recompiler toolkit and runtime

These are faults in the translation and its runtime, found by running the port and tracing
them back. The fixes are in the fork of the toolkit this repository carries (`xboxrecomp/`)
and in the tools next to it.

- **A game with no sound effects.** Of 331 sound definitions loaded, one survived. A four-byte
  value in the sound library's data happened to look like the address of an instruction in
  the middle of a constructor, so the function finder began a function there, between a test
  and the jump that uses it. The translated second half ran with the wrong flag and emptied
  the lookup table at every registration. The tools now find such false starts and merge them
  back before translating.
- **A mission crash from the same cause.** `GEnviroSwarm::Process` was split in two at a byte
  that only looked like a function start.
- **A crash on unloading a level.** The runtime failed to honour a memory release
  (`MEM_DECOMMIT`), which slowly drifted the Xbox heap until a level unload crashed. Replaying
  the original code against the port's found it on the first day.
- **A divide by zero in DirectSound** voice setup, for voices with no channels.
- **A shader assembler that was never translated.** The disassembler read only some of the
  program's code sections, so library code was missing, including the routine that assembles the game's vertex shaders
  at run time. Reading all eight raised the function count from 9,505 to 10,291.
- **80-bit floating-point loads and stores** were translated as 32-bit reads, or dropped. All
  29 of them are constants in the C runtime's maths (pi/2, infinity, not-a-number).
- **Return to the dashboard on Linux.** The Xbox's raw disk partition, which the game mounts
  as its utility drive, did not exist in the runtime outside Windows, and the title quit.
- **The game's disc-error screen on Linux.** The game asks for `\TBD\ATTRACT\` and the disc
  says `tbd/Attract`. The runtime's file layer now matches names without regard to case.
- **A wasted processor core, and a slow vertical blank.** The stand-in GPU's acknowledgement
  thread spun without sleeping, and the emulated vertical blank waited a whole period instead
  of until the next tick.
- **Creating a folder that already exists** reported success where the Xbox reports a name
  collision, which the game's save code tests for.

One more is known and not fixed in the translator: in 32 functions it loses a processor flag
across a call, on paths that handle infinities and not-a-number values. No recorded play
reaches them. The replacements written for those functions follow the machine code, not the
translation.

## The work behind it

Most of the month went into proving that changes were safe, not into the changes.

**Proving each function is the same function.** A rewritten function counts only when it
passes a check. The running game is paused at a real call of that function. The new C++ runs
from that state in the build, and the original Xbox instructions run from the same state in a
CPU emulator. The return value, every memory write, the floating-point results and every call
made, with its arguments, must agree; one difference fails it. Code that ordinary play never
reaches is driven by probes: the function is called on purpose, on live game objects, with
inputs chosen to take each branch.

**Proving the whole game is the same game.** A function can match the original exactly and
still break the game, and several did. So sixteen play sessions were recorded (tutorials,
missions for all three species, the pause menu, the front end) and are replayed with a fixed
clock and scripted input on two builds: the mixed one, and one made only of translated code.
Every page of the game's memory is hashed as they run, and the two must match page for page.
They run after every batch of changes. On top of that, twenty-one missions are played by
random input for 20,000 frames each to look for crashes.

**Getting to all of them.** The first 4,000 functions came quickly, many generated by tools
from repeating patterns (constructors, the code that describes each class to the editor).
Then progress slowed: what was left was large floating-point code, code no test level
reached, and functions blocked by small differences in the ones they call. A census of which
functions each recorded session calls picked the cheapest session to check each one. Scripted
tutorial runs reached the order panels and upgrades; front-end runs reached profiles and
saves. For the last few hundred, an explorer built inputs by itself: it ran the original,
watched where it failed for want of an object or a pointer, supplied one, and tried again
until every branch had been taken. The count went 4,085 on October 15, 6,161 on the 18th,
7,033 on the 25th, and the last functions written passed on November 3.

**Testing the testers.** On November 6 faults were planted on purpose in verified functions:
a constant off by one, a comparison flipped, an assignment removed. No fault that ran and
changed the game's state got past the check. But 10 of 16 sat on lines the recorded checks
never ran, which is the honest limit of the whole method: verified means every path that was
checked, not every path. About 40% of the game's functions are verified on every one of their
paths. The record of which paths were checked is published with the code.

**The checker had blind spots of its own.** Functions that take a floating-point value in a
processor register never had their results compared. Arguments handed to the Xbox kernel were
not compared either, and a first version of a file function passed while giving the kernel
pointers 12 bytes off. Both gaps were found by real work, not by the planted faults, and both
were closed.

**The game reads memory it never wrote.** The function check ignores the stack memory a
function leaves behind, because by the calling convention nobody may rely on it. The game
does. An experiment overwrote that dead memory after every call, and the game's long-lived
state changed from the first seconds of start-up: the original copies a half-filled
stack object into lasting memory, for one. So code that replaces Microsoft's libraries has to leave the
stack exactly as the original does, and some of it reads like annotated machine code for that
reason.

**Giving a console RTS a mouse.** Picking in this engine is a list of screen rectangles
rebuilt every frame, and only things that belong to a unit put themselves on it. Menu items
and HUD buttons never do, so the mouse had nothing to hit. The port keeps its own list of
where the game drew each menu item and HUD button, and drives the game's own focus and
activation from it; the game's list is left alone. The disc's input tables still carried the
developer's PC key bindings, which the Xbox build ignored, and the first keyboard controls
were simply those, switched back on.

**Widening the view without stretching it.** The game lays everything out on a 640 by 480
screen. On a wide window the port builds the game's cameras for the real shape, so culling
and picking follow, and places the 640 by 480 layout on a wider virtual screen. The 2D layer
is told apart from the 3D scene in the renderer and shifted or spread, so the HUD is still
hit-tested where it is drawn and a unit at the far edge can be clicked.

**Building it on your PC.** The download carries no game code, so your PC translates and
compiles the program from your disc. That only works if the result is the same everywhere. A
clean machine with only the public files and a disc was shown to regenerate the translated
code byte for byte before the installer was written, and again for this release. Two hidden
dependencies on the development workspace were found that way. Every tool the installer
downloads is pinned to a checksum.

**Playing it.** Builds went to a real PC from late October and tester packages from November 5,
and play found what no test did:
that tracing the game's own letters does not sharpen them, that a pad left plugged in
overrode the Alt and Shift keys through its resting triggers, that Alt+Z for a stance opened
NVIDIA's overlay, that keys scrolled the camera after a delay and would not go diagonally,
that the pointer stayed with the pad once the pad had moved it, and that a slow right drag
stepped along the screen's axes. The installer learned from the first tester to name every
file it turned down and why.

**Starting on Microsoft's code.** The last four days replaced the first of the Xbox
libraries: file and save calls, the maths functions, the string formatting engine. On the
last day a bulk edit slipped a statement between an `if` and the call it guarded, so a block
of memory was freed on every start-up. All sixteen replay checks failed within 50 frames,
which is how it was found, and what they are for.

## Tried and dropped

- **Tracing the game's fonts.** The first sharp fonts were vector traces of the original
  letters. At 8 to 12 pixels tall there is no detail to trace, and they still looked smudged.
  The letters were drawn again from scratch.
- **A second selection box.** A drag box was written for the port before it turned out the
  game already has one for its mouse pointer. The game's own is used; the port's stays as an
  option.
- **An installer that searched for the game.** An early installer looked through the desktop,
  downloads, documents and drives for a disc image. It now reads one folder, the one you put
  the game in, and its log names files by name only.
- **Autosaving on the wall clock.** It made one of the replay checks fail at random, because
  a fast run saved at a different frame than a slow one. Autosave counts game time.

## Changes in v0.1.1

Found by playing the public v0.1.0 on the day it went out.

- **Right drag was inverted up and down.** Left-right grabbed the map and up-down pushed it the
  other way, so diagonal drags went off along the wrong diagonal. Up-down drags also moved the
  map only three quarters as far as the hand, because the tilted view was not allowed for. The
  pan now moves the view by exactly what the ground under the pointer moves.
- **Updating a GitHub install failed** with "file already exists". The installer could not
  clear the old copy of the decompilation, whose git files Windows marks read-only.
- **A crash or black screen when skipping the logo movies** on a fast PC: the game could read a
  movie its worker thread had just closed. Confirmed fixed by play on the PC that hit it.
- **Session recording is off unless you tick it** in the launcher.

## Known issues


- Only the USA retail Xbox release is supported. The installer names the release it found
  and says why it turned a copy down, including the PlayStation 2 disc.
- The installer is for Windows. The port builds and runs on Linux, without an installer.
- A selection box released over the bottom HUD band, near the Dropship button, calls the
  Dropship. The Xbox game's pointer does the same there; a mouse just reaches that spot far
  more often.
- The first install takes 5 to 15 minutes, because your PC translates and compiles the game.
- Four finished replacements (the memory allocator's allocate and free, and two for run-time
  type information) are switched off in this release until every one of their paths has been
  checked.
- Updates are manual: drop a newer package on the launcher. There is no update check yet.
