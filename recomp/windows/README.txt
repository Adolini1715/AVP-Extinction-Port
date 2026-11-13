Aliens versus Predator: Extinction - PC port, v0.1.0
================================================================

The Xbox game running as a native Windows program, with OpenGL, keyboard,
mouse and controller input, and SDL audio. This copy was built on your PC
from your own disc. It contains your game's data, so keep it to yourself:
anyone else builds their own from their own copy.

STARTING THE GAME
Use "Play AvP Extinction" in the installer folder (or avpe.exe in this
folder). The launcher opens first: Play, resolution, window or fullscreen,
widescreen layout, frame cap, movies, optional session recording, and links to all
settings (avpe.ini), the saves folder and the controls list. Hold Shift
while starting the game to see the launcher again if you turned it off.
Saves go to the "saves" folder here; the log to avpe.log.

REPORTING A PROBLEM
Anything that looks or behaves differently from the Xbox game is worth a
report. When something goes wrong press Ctrl+F12 at that moment (it marks
the time and takes a picture), keep playing or close the game, then run
"Pack session.bat" and attach the zip it names to an issue on the project's
GitHub page, with a line about what happened and the build line from the
top of avpe.log.

CONTROLS
Menus (front end, and the pause menu in a mission):
  Mouse              point at an item to highlight it, click to select;
                     right button = back; wheel = up / down; click a
                     slider's bar to move it (hold to keep moving); the
                     "A Select" / "B Back" hints at the bottom are clickable;
                     on a "Press A" screen, click anywhere
  Keyboard           arrows move, Enter or Space = A (select), Esc or
                     Backspace = B (back), P = Start, Tab = Back, X / Y
  A game controller also works.
Mission briefing:    click, Enter, Space or P to start the mission.
Mission intro:       Esc skips it (or click twice: the first click shows the skip prompt).
In a mission (a modern RTS layout, checked against the game's own
tutorials; each pad button combination is one key or a modifier + click):
  Left click         select; hold for the selection circle; drag for a box
  Ctrl+A             select everything of yours on screen
  Shift+left click   add a unit to the selection (pad: LT + A)
  Right click        the default action: move, attack, pick up, take a
                     trophy, repair, heal (pad: B)
  Shift+right click  attack-move (pad: LT + B)
  Alt+right click    set a waypoint, one per click (pad: RT + B)
  Wheel              zoom: smooth, 12% of the distance a notch, the same at
                     any frame rate (distance 10 to 52)
  Right drag         move the map with the mouse, like grabbing it
  Middle drag        turn (left / right) and tilt (up / down) the camera
  WASD or arrows     scroll; the pointer at a screen edge scrolls too
  Minimap            click or drag to move the camera there
  HUD buttons        click (e.g. the dropship)
  Q                  special ability (pad: Black): cloak, deploy, drop a
                     carried Sentry Gun, create Hive Nodes, transform; opens
                     and closes the Dropship Uplink and the Shrine menu
  E                  second ability (pad: LT + Black): Predator heal,
                     Queen egg sack, ...
  X                  in the Uplink / Shrine: confirm the order
  Z X C V            stance: Aggressive, Defensive, Stand Ground, No Attack
                     (on their own: no need to hold Alt, and Alt+Z opens the
                     NVIDIA overlay on PCs that have it)
  T F G              order: Patrol, Follow, Gather (pad: RT + A / X / Y)
  Alt (hold)         the order menu (pad: RT)
  Ctrl+1 .. Ctrl+4   make the selection group 1-4 (pad: White + D-pad)
  Shift+Ctrl+1 .. 4  add the selection to group 1-4
  1 .. 4             select group 1-4 (pad: D-pad); twice quickly: the
                     camera goes to the group
  Space              jump to the latest event (pad: Y)
  H                  jump to your base (pad: X)
  R                  camera presets          Keypad 7/9, 0/2  turn, tilt
  F5 / F9            quicksave / quickload
                     Autosave: "Autosave 1" to "Autosave 3" in turn in the game's
                     own save list, a minute into a mission and then every 10
                     minutes of play (AVPE_AUTOSAVE=minutes in avpe.ini, 0 = off)
  Esc                pause menu              Shift+Esc        quit
  Screens like the Dropship Uplink take the mouse like the menus do; there
  the keys work as in the menus.
  A controller works at the same time (Xbox layout: LB = White, RB = Black).
Window:  Alt+Enter or F11 toggles fullscreen.

SETTINGS
In the game's menus (main menu or pause menu): Options > PC Settings has
resolution, display (Window, Borderless fullscreen, the default, or Exclusive
fullscreen), widescreen, frame cap, mouse speed and the button prompts (press a row to step it; changes apply at once and are saved
to avpe.ini), and Key Bindings has every rebindable key.
Holding Shift while starting avpe.exe opens a Windows settings window
before the game instead; AVPE_SHOW_SETTINGS=1 in avpe.ini shows it always.

SETTINGS (avpe.ini, or environment variables of the same name)
  AVPE_PC_FULLSCREEN=1   start fullscreen
  AVPE_PC_EXCLUSIVE=1    fullscreen is exclusive (the display's own mode; Alt+Tab
                         minimises the game, screenshot tools may not see it)
  AVPE_PC_SIZE=1920x1080 window size (and render size)
  AVPE_PC_RES=3840x2160  render resolution only (default: the desktop's)
  AVPE_PC_WIDE=0         keep the original 4:3 view, stretched
  AVPE_PC_FPS=60         frame cap; 0 = the Xbox's own pacing (about 30)
  AVPE_PC_MOUSE=1.0      pointer speed
  AVPE_PC_WHEELZOOM=6    wheel zoom strength: 6 = 12% a notch (0 turns it off)
  AVPE_PC_ZOOMRANGE=10,52 camera distance limits (0 removes them)
  AVPE_CONSOLE=1         show a console with the log instead of avpe.log
  AVPE_MOVIES=0          skip the logo and intro movies

SAVING
F5 quicksaves and F9 quickloads, in a mission (not while paused). The
quicksave is one of the game's own save slots, named "Quicksave": it also
shows in Esc > Load and the main menu's Load Game, and it keeps working with
later builds. F5 again overwrites it. Loading brings up the mission briefing
first, as the game's own Load does; Enter continues. A line at the top of
the screen says "Quicksaved" or "Quickloading". F5 needs a free save slot the
first time (delete an old save in Esc > Load if the slots are full).
Esc > Save and Esc > Load work as on the Xbox.

WHICH BUILD
The first lines of avpe.log name the build: its date and the commits it was
built from. Quote that line when reporting a problem.

MOVIES
The EA, Fox and Zono logos and the intro play from the game's own files, as
on the Xbox (decoded by FFmpeg: the avcodec, avformat, avutil and swscale
DLLs; LGPL, see FFMPEG-LICENSE.txt and FFMPEG-BUILD.txt). Enter (A or Start
on a pad) skips the intro, as on the Xbox.

KNOWN LIMITS
- On a Mac, run it with Wine or CrossOver (it is an x86-64 Windows program;
  on Apple Silicon that goes through Rosetta).
- Only the USA retail Xbox release is supported.
- A selection box released over the bottom HUD band, near the Dropship
  button, calls the Dropship, as the Xbox game's pointer does there.

SESSION RECORDING (OPTIONAL, OFF UNLESS YOU TURN IT ON)
Tick "Help test: record my play sessions" in the launcher to turn it on. Then each run of avpe.exe
saves one folder in "sessions" (date_time) so problems can be found: frame
times and memory every 10 seconds, one line per level played, game events (missions starting,
stutters, stalls, crashes), unit and resource counts, your avpe.ini settings, the CPU / RAM /
graphics card model, and the engine log. A bad stutter saves a picture of the game screen.
It records no user name, PC name or paths outside this folder, and nothing is sent anywhere. When
the game closes (or crashes, or is killed and then started again) the folder is checked and
privacy.txt says "clean". Open sessions\README.txt for the list.
  F12 = take a picture      Ctrl+F12 = mark a problem (logs the moment, takes a picture)
To send a session run "Pack session.bat" and send sessions\<date_time>.zip.
Untick the box (or set AVPE_RECORD=0 in avpe.ini) to stop recording.
