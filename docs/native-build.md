# Native build (static recompilation): status 2026-10-15

Toolkit: xboxrecomp v0.13.1 (`193e299`) plus our patches
(`recomp/0001-*.patch` raw partition devices on POSIX, `recomp/0002-*.patch`
case-insensitive paths on POSIX, `recomp/0003-*.patch` RECOMP_WATCH_VA read
diagnostic, `recomp/0004-*.patch` NV2A executor: anti-aliasing, semaphores,
context DMA objects). Full rebuild: `recomp/build_linux.sh`;
re-lift after seed or name changes: `recomp/relift.sh`; incremental rebuild
after bridge edits: `recomp/rebuild.sh`. Overrides live in `recomp/avpe_bridge.c`
(listed for the lifter in `recomp/manual_functions.json` or auto-detected as
`sub_X_gen` wrappers).

## Result of the measurement run

Linux test bench (Xvfb, software GL, `AVPE_NOVSYNC=1`), 8-minute runs:

| Stage | Reached |
|---|---|
| Xbox runtime start (XAPI init, threads, heap, DVD auth, T:/U:/Z: drives) | yes |
| Game init, renderer init, Direct3D device created, vertex shaders assembled | yes |
| `LOGO.TBD`, `CURSORS.TBD`, `GAMEPADXBOXUSA.TBD`, `INTRO_MOVIE.TBD` loaded | yes |
| Logo videos (EA, Fox, Zono, Intro) | skipped by bridge (see below) |
| `MASTER.TBD`, `MAINMENU.TBD` loaded, main menu runs | yes |
| Attract demo level (`ATTRACT\BACKGROUND.TBD`) loads and runs | yes |
| Menu <-> attract cycling | 12 level changes, 14,400 frames, no crash (soak stopped by hand) |
| Stops at | nothing found yet; earlier unload crash fixed (see "Unload crash: solved") |
| Unresolved indirect calls | 0 |
| Pixels | yes, Linux, no emulator: title, "Press START", attract intro and demo (frames kept locally in `docs/frames/`, not committed) |
| Speed with pixels | 0.58 fps on the 2-core bench (software rasteriser) |

Runs are repeatable but not frame-exact (the same unload lands a few dozen
frames apart between runs; heap addresses can shift by a page).

## What it took (in order found)

1. **Code coverage, not graphics.** The first crash (`D3DDevice_CreateVertexShader`
   handed a pointer of -1) came from the game's runtime shader assembler
   (`XGAssembleShader`, XGraphics). It had never been disassembled: the pipeline
   ran with `--text-only`, so the Microsoft library sections were only partly
   covered and 339 functions became empty stubs. Proven with a new oracle:
   `tools/snap_replay.py` loads a 64 MB guest snapshot taken by the native
   build and runs the ORIGINAL x86 in Unicorn from the same state (minimal
   kernel stand-ins: critical sections, NtAllocate/FreeVirtualMemory). The
   original assembled the shader (S_OK, 2,084 bytes, 127 M instructions).
   Fix: disassemble all eight code sections. Functions 9,505 -> 10,291.
2. **Xbox SDK identification.** XbSymbolDatabase (Cxbx-Reloaded's signature
   database) identifies 293 symbols in this XBE in 2 s: 118 D3D8, 87 DSound,
   42 XAPI functions plus 45 variables. Applied as `xdk_*` names (247).
3. **Seeding.** 2,967 RTTI vtable slots and 830 registry `Create` factories
   (function starts by construction) plus 172 indirect-call targets observed
   at runtime (`tools.seed_from_log`). Functions 10,547; unresolved indirect
   calls 164 -> 0; unresolved stubs 339 -> 43.
4. **Minimal graphics bridge (3 functions):**
   - `D3D_BlockOnTime` returns at once: Direct3D waits for the GPU to write
     back a time stamp, which the stand-in GPU never does.
   - `D3DDevice_BlockUntilVerticalBlank` paces from the host (60 Hz, or
     immediate with `AVPE_NOVSYNC`): the vblank KEVENT lives in guest memory
     and the runtime's KeWaitForSingleObject expects host objects.
   - `D3DDevice_Swap` wrapped only to count frames.
5. **Toolkit gaps on Linux (patched):** raw `\Device\Harddisk0\partition0`
   (XAPI utility-drive mount; missing on POSIX made the title return to the
   dashboard) and case-sensitive paths (`\TBD\ATTRACT\` vs `tbd/Attract`
   triggered the game's fatal disc-error screen).
6. **Videos.** The game plays XMV on a worker thread paced by the DirectSound
   clock. With no audio hardware behind DirectSound the clock never moves and
   the worker never ends; the menu waits on its thread handle forever. The
   toolkit's emulated APU (`RECOMP_AC97_READY`) needs its MMIO trap, which is
   Windows-only (VEH); on Linux it faults at 0xFE801100. Bridge: the worker
   (`0x120DE0`) takes its own "played to the end" path at once
   (`[ctx+0x204]=1`, `ExitThread(0)`). `AVPE_REAL_XMV=1` restores it.

Also identified along the way: `sub_00063190` is the game's fatal
disc-error screen (draws 4 frames, then `jmp $` at 0x633D0); `0xDEA10` is the
TBD loader (`TBFF`/`TBD2` RIFF forms); `CShell::MainLoop` and
`CShell::ShellLoadLevel` drive menus and levels.

## Unload crash: solved (oracle, 2026-10-14)

Symptom: segfault reading guest 0xBEE5CBAB in `sub_00138070` (texture release)
<- `CRendTexture` dtor (`0x127010`) <- `sub_000DFA20` <- `CTbdFile::~CTbdFile`
<- `CShell::ShellLoadLevel`, on the fourth level change (frame ~6,600).

Chain of evidence:
1. The lifted `sub_00138070`/`sub_00127010` match the original instruction for
   instruction; the bad value (field +0x24 of the texture record at 0x015982B0)
   is real data.
2. Host hardware watchpoints (gdb, all four RAM aliases) saw no CPU write
   after frame 29, so the write came through the kernel. `RECOMP_WATCH_VA`
   (patch 0003) plus field polling in the bridge found it: at frame ~973 the
   game allocator legitimately handed 0x01597EC0 to a 1,060-byte `PUBL` chunk
   of the attract level and `NtReadFile` filled it, while an attract
   `CRendTexture` still pointed at the old record inside it.
3. Oracle replay of the whole menu teardown (`CTbdFile` dtor, about 1 M
   instructions, 41 `MmFreeContiguousMemory`) against the lifted run: the XAPI
   heap call sequence (`0x1442D7`, `0x144D64`, `0x145BAD`) matched for 149
   calls, then diverged. Replaying the single function at the divergence
   gave the lifted answer, so the state was already different; diffing the
   original's state at call 149 against the lifted one showed the original
   heap recording an uncommitted range `{0x01000000, 0x4000}`.
4. Cause: the toolkit's default `NtFreeVirtualMemory` hands guest pointer
   slots to the host `VirtualFree`, so every `MEM_DECOMMIT` fails. The XAPI
   heap takes its decommit-failed path and its layout drifts from the
   console's at the first unload. `RECOMP_HEAP_RECLAIM` implements decommit
   (pages zeroed, as the console returns them).
5. Fix: the Linux host sets `RECOMP_HEAP_RECLAIM=1`. Verified: menu teardown
   (#2) and the teardown that used to crash (#4) now match the original
   exactly, all heap calls (483 and 487) and all final memory except one
   timer word another thread updates. Soak: 12 level changes, 14,400 frames.

Not lifting bugs: every divergence found so far came from the environment
(missing code coverage, kernel semantics), never from the translation of an
instruction.

Also found: with ASLR off (gdb's default) the toolkit's 4 GB guest window can
map so high that the fake kernel page at 0x80010000 lands past the top of
user space and the title faults at startup (`sub_00142BC9`). Debug with
`set disable-randomization off` until the window placement is bounded.

### Oracle tooling (tools/)
- `snap_replay.py snap.bin <stop> <entry> [--log-calls=a,b] [--dump-at-call=N]
  [--watch=va,...] [--last]`: replays original code from a native snapshot
  with the same graphics bridges as the build (`D3D_BlockOnTime`, vblank) and
  kernel stand-ins (allocate/free virtual memory with decommit zeroing,
  `MmFreeContiguousMemory`, critical sections).
- `snap_diff.py before.bin lifted_after.bin`: word-level diff of original vs
  lifted end state.
- `xdis.py va end|+len`: disassemble any XBE section (was `dis.py`, which
  shadowed Python's own `dis` module).
- Bridge switches: `AVPE_SNAP_DTOR=N` (snapshot around the Nth level
  teardown), `AVPE_SNAP_CALL=N` (around the Nth logged heap call inside it),
  `AVPE_WATCH=va` (poll a word, print host stack on change), `AVPE_COVER=va`
  (which allocation covers an address), `AVPE_TRACE_CONTIG=1`.

## Graphics on Linux (2026-10-14)

Premise corrected: the toolkit has no full xemu renderer on Windows either.
Its pieces: a platform-neutral software NV2A executor
(`src/kernel/nv2a_pb_exec.c` + vertex-program interpreter + register
combiners, about 4,500 lines), a D3D8 HLE layer with a first-cut Linux
OpenGL backend, and an early D3D11 translator (the only Windows-only part).
The executor needs no MMIO trap: the runtime's ack thread polls DMA_PUT in
the plain-memory aperture, executes new commands, advances DMA_GET. So
graphics bring-up stays on the Linux bench.

Fixes needed for this title (toolkit patch 0004):
1. **Anti-aliasing.** AvP:E renders to a 2x horizontal AA surface
   (SET_SURFACE_FORMAT 0x1128, mode 1, pitch 5120 = 1280 px) and resolves
   into a 640x480 front buffer (0x2DC000, format 0x128). The executor ignored
   the mode, so everything landed in the left half and the resolve halved it.
   Now clip and positions are stretched by the AA factor, as the GPU does.
2. **GPU fences.** D3D_BlockOnTime polls a semaphore the GPU writes with
   BACK_END_WRITE_SEMAPHORE_RELEASE (0x1D70); the executor ignored it, so our
   bridge had to fake completion, which lets D3D overwrite unexecuted commands.
   Now executed in stream order, an exact fence; the bridge runs the original
   wait whenever the executor is on.
3. **Context DMA objects.** The semaphore DMA object is based at physical
   0x1000 (fence at 0x80001000). Handles are now resolved through RAMHT to the
   DMA object in PRAMIN, exactly as the hardware reads them (all 11 context
   DMAs of this title resolve).

Measured per presented frame (attract demo): about 28,000 command words,
about 3,200 triangles drawn, about 1.2 M pixels written (2x overdraw of the
1280x480 AA surface), 0.58 fps. Defaults now: `RECOMP_PB_EXEC=1` (off with
`AVPE_NO_GPU=1`); frame dumps with `RECOMP_FB_DUMP=<prefix>` and
`RECOMP_FB_DUMP_FLIPS=1` (every presented frame; timed dumps can catch a
frame half drawn).

Open graphics issues: speed (software); a few hundred thousand
degenerate/NaN triangles per minute to audit against the vertex-program
interpreter; no window presentation on the bench (headless dumps only).

## Missions (2026-10-14)

Marine mission 1 runs natively, reached through the real menus:
title START, New Profile, Create, Continue, Start Campaign, Marine Campaign,
Marine 1, then the Mission Goals briefing (closed with START). Profiles save.

- **Virtual controller.** The Xbox USB stack talks to OHCI registers that
  are plain memory on Linux (the toolkit's register trap is Windows-only), so
  no pad enumerates. The bridge replaces the XAPI entry points the title
  calls (XGetDevices, XGetDeviceChanges, XInputOpen/Close/GetCapabilities/
  GetState; gamepad type = g_DeviceType_Gamepad at 0x1FD9B0) with one pad on
  port 0, driven live from a file: `AVPE_PAD=<file>`, lines like
  `a:300`, `start`, `lright:800`, `a+lright:600`.
- **The game claims its controller at the title's PRESS START.** Skipping the
  title leaves no active pad, and the mission briefing then sits in
  GGamepadDisconnectHandler's wait. The briefing is a modal loop inside the
  level load (GExitMissionGoalsButton's constructor, 0x69F50) that ends on
  START.
- **Saves.** The toolkit mapped E: (TDATA/UDATA) into the game directory,
  so saves landed in the disc image. Patch 0005 maps it under the save dir;
  `AVPE_SAVE_DIR` picks one per run (fresh = no profile). Stray save folders
  moved out of the disc dump.
- **Scripts.** `recomp/nav_m01.sh` drives boot to in-mission unattended
  (about 3 minutes without graphics); `recomp/play_m01.sh` presses through
  pointer, camera, command and pause inputs. `AVPE_LEVEL` / `AVPE_LEVEL_AT`
  substitute a level for a given load (skips the title, so no controller).
  `AVPE_GAME_DIR` runs from an overlay of the disc (mods).

## Run-speed mod, checked natively

`mod/FD090663.tbx` (MarineMover MoverData.MaxRunSpeed 2.5 -> 10.0) through an
overlay game dir; memory compared 100 frames into the mission against an
unmodified run. Of 107 GUnitMover objects, exactly 8 change, at +0xA4, from
2.5 to 10.0; every other value of that field is identical between runs. The
game parses the patched file and the live Marine movers carry the new
maximum run speed (GUnitMover +0xA4 = MaxRunSpeed). Seeing them run faster
needs commanded movement (controls), still to do.

## Coverage (which game code runs where)

Instrumented build (`recomp/build_coverage.sh`: translated code compiled
with -finstrument-functions, `recomp/coverage_rt.c`), checkpoints at every
level load, N frames into a mission, and on demand (`AVPE_COVERAGE_NOW`).
`tools/coverage.py` -> `symbols/coverage.json`.

| Part of the game | Game functions first run there |
|---|---|
| Boot (to the menu load; includes MASTER.TBD class setup) | 2,067 |
| Menus (title, profile, campaign) | 151 |
| Attract demo | 940 |
| Marine mission 1, idle | 772 |
| Marine mission 1, scripted button presses | +83 |
| **Ran at least once** | **3,619 of 5,991 (60.4%; 69.4% of code bytes)** |

Engine, world, projectile and AI files are 60-95% covered (Mover.cpp
103/148, UnitAI.cpp 63/92, Targeter.cpp 74/121, Predator.cpp 155/245).
The pointer/command code is not: AvPPointer.cpp 23/72, because commands need
a unit under the cursor, which needs someone looking at the screen.

## Options for the next decision

1. ~~Explain the unload crash.~~ Done (above).
2. ~~**Pixels.**~~ Done in software (above). Next for graphics: speed, via a
   hardware backend for the executor (NV2A state to OpenGL/Vulkan, the
   approach xemu takes) instead of the per-pixel software path. Old note: (a) Execute the command buffers: the toolkit's
   xemu-derived NV2A path, whose MMIO trap is Windows-only today. (b) Replace
   the Direct3D API (HLE) with the toolkit's D3D8 layer: 118 identified D3D
   functions, but Xbox D3D resources are structs the game touches directly.
   Measure first: per-frame D3D call surface and pushbuffer volume.
3. ~~**Platform for graphics.**~~ Linux (decided 2026-10-14). Audio still
   needs the APU MMIO trap ported to SIGSEGV or a DirectSound HLE. Old note: The toolkit's GPU and APU traps work
   on Windows now; the Linux bench needs a SIGSEGV-based MMIO trap port.
   Either port the trap or move graphics bring-up to the author's PC.
4. **Audio** (videos, sound): APU trap (as 3) or a DirectSound HLE.

## PC platform layer (2026-10-15)

`AVPE_PC=1` runs the game on SDL2 and OpenGL 3.3 instead of the Xbox
libraries: `avpe-decomp/platform/` (`pc_render.cpp`, `pc_input.cpp`,
`pc_timing.cpp`, `pc_platform.h`). Every replaced entry point still runs the
translated Xbox library body (`sub_X_gen`), so the game's view of Direct3D,
input and timing stays as it was; the PC layer mirrors the calls and draws,
reads and paces on the host. Off (the default), verification runs are
unchanged.

**Renderer.** The game's Direct3D calls (21 runtime-assembled xvs.1.1
vertex shaders, push-buffer vertex constants, texture stage cascade, three
kind of draw) are translated to GLSL and GL state. Title, menus, briefings
and missions render; the software NV2A executor is not run in PC mode (it
drew every frame a second time on the CPU). D3D fog is never enabled by
this game (its fog is in the vertex shaders), so there is nothing to do for
it. Linear textures (the minimap's fog of war, written in place a few
texels at a time) are hashed whole once a frame.

**Keyboard and mouse.** The disc's input layouts still carry Zono's PC
bindings (devices 1 = keyboard with DirectInput key codes, 2 = mouse). The
Xbox Poll reported only the pad; the PC layer reports all three, answers
key codes, mouse buttons and relative axes, and the PC build's triggers
work again: arrows scroll the camera, keypad pitch/rotate/zoom, Esc pause,
Shift+Esc quit, Ctrl/Shift with F-keys and digits for groups, mouse pointer
with left select and right command, typed text into the character queue.
The front end binds only pad buttons, so keys no active keyboard trigger
claims also press pad buttons (Enter A, Esc B, arrows d-pad, P Start, Tab
Back, X, Y). SDL game controllers drive the Xbox pad. The mission pointer
applies motion twice (`0x2EBE0` then `0xF0650`); motion is halved so it
tracks the mouse 1:1 (`AVPE_PC_MOUSE` scales). `AVPE_PC_INPUT=<file>`
scripts keys, clicks and motion for tests.

**Frame pacing.** The Xbox present step (`0x12DF90`) pads each frame to
0.032, 0.049 or 0.065 s (2, 3 or 4 refreshes at 60 Hz), so a 33 ms frame
became 49. PC mode caps at `AVPE_PC_FPS` (default 60, 0 = Xbox stepping)
and sleeps the wait. Attract demo on the 2-core bench (llvmpipe): 23.5 ->
41 fps, now bound by the software rasteriser. Mission clock tracks wall
time (1:27 after 90 s). Also: the emulated vertical blank waited a full
period instead of to the next tick, and the stand-in GPU's ack thread spun
on a core (`RECOMP_NV2A_ACK_SLEEP_MS`, toolkit commit `376bc78`).

**Widescreen.** A window wider than 4:3 gets a wider view (Hor+), with
the 640x480 layout centred on a virtual screen -ox..640+ox (ox = 106 at
16:9):
- perspective matrices are built for the virtual aspect (`0xEA670`, the
  game and menu cameras pass 4:3 as an immediate);
- full-screen cameras get the virtual rectangle (`0xE95F0`), so culling,
  picking and the camera orthographic matrices follow; the one directly
  built 2D ortho (`0xEA6F0`) too;
- the screen viewport (list at `0x2F6CC4`) spans it: the pointer reaches
  the window edges and border panning (`0x2E3B0`, worked out from the
  viewport's width as if it started at 0) is recomputed from its origin;
- CRendAPI's 2D layer (`0x12A390`, `0x12AA30`) maps pixels from the
  camera's left edge, so those draws, told apart by an unscaled z row, move
  right by ox in the renderer: the HUD is centred and still hit-tested where
  it is drawn;
- the minimap's sub-viewport maps like the 2D layout; untextured
  full-screen fades still cover the window.
Verified at 16:9: menus centred and in proportion, mission HUD and minimap
aligned, pointer 1:1 to the window edges, a unit at the far left picked by
click, no stray panning. 4:3 output unchanged. `AVPE_PC_WIDE=0` turns it
off.

**Audio.** `pc_audio.cpp` mirrors the game's DirectSound calls into an
SDL2 mixer: sound buffers (PCM, Xbox ADPCM) with volume, frequency, loop
region, position and a listener-relative 3D pan; GetStatus answered from
the mixer; music and voice-over streams (WMA decoded by the game's own,
translated decoder into eight 8 KB packets) queued by Process and completed
as played. The menu music the game decodes matches ffmpeg's decode of
`Menu01.wma` within 2 LSB. Menus, attract demo and Marine 1 (briefing
voice-over `MISMAR\M1T1.wma`, mission music `marmus1.wma`, unit voices)
play. No effects image (`D:\dsstdfx.bin` is not on the disc; the game
carries on without it).

**Lifting fix found through audio.** The game never asked for a sound: of
331 sound definitions loaded, one was left in the lookup table. A
coincidental `0x000E1007` in DirectSound's data made the function finder
start a function inside CSoundDefinition's constructor, between
`test al,al` and its `je`; the lifted second half took the `je` on a
fallback flag and cleared the table at every registration. `tools/
false_starts.py` (list in `recomp/false_starts.json`) merges such starts
back into their owners before lifting (`--coalesce-functions` refuses them:
an immediate-reference start counts as strong evidence). Audit of every
function that branches on the fallback flag: the other two fall-through
splits are in data mis-decoded as code (XGraphics, the skipped XMV player);
eight flag-at-entry functions are tail-jump aliases inside owners that
still contain the whole range, never entered. Also: the manual-code scan
no longer reads the AVPE-INDEX comment blocks (it treated every listed
function as hand-referenced and protected it).

**Windows build (2026-10-15).** `recomp/build_windows.sh` cross-builds the
PC build with MinGW-w64 from the same generated C: the toolkit built for
Windows (`/home/claude/xr-build-win`, its native Win32 path), SDL2's MinGW
package, glad for OpenGL. `recomp/main_windows.c`: runs from the exe's
folder (game files in `game\`, saves in `saves\`), PC layer on by
default, log to `avpe.log` (`AVPE_CONSOLE=1` for a console), settings from
`avpe.ini`, 1 ms timer resolution. Tested under Wine (software GL): menu
and attract cycling for 5 minutes, Marine 1 at 16:9 by keyboard. Not yet
on a real Windows PC. Test package: avpe.exe, SDL2.dll, README.txt,
avpe.ini (no game data). Also: the game renders into an offscreen
framebuffer at the desktop's resolution and is scaled into the window
(letterboxed if its shape differs); Alt+Enter / F11 fullscreen.

Making sound robust exposed the Xbox DirectSound library's voice code
faulting with no APU behind it (a divide by zero in voice setup, a garbage
voice in volume programming, the voice list in CommitDeferredSettings);
with AVPE_PC on, buffers and streams are now emulated whole by the PC
layer and the library's voice code never runs.

**Mouse in the menus (2026-10-15).** `platform/pc_menu.cpp`. Picking in
this engine is a per-frame list of screen rectangles (0x002FFDF0, end
pointer 0x002CC290): a mesh, sprite or skeleton whose CRender names an
owner object (+0x48) adds its on-screen bounds through 0x00138FB0 (which
also wakes the owner), and GfsPointer::GetMenuItem and box selection read
it. Menu items never name an owner, which is why the PC build's
GMenuPointer would have needed level data. The PC layer instead hooks
0x00138FB0 and keeps the rectangles of menu items' mesh workspaces
(GPhysical +0x90, CMeshWorkspace +4 = the CRender) in its own list,
leaving the game's list and the items untouched. While no GfsPointer
exists (front end) or a GBaseMenu does (pause and what it opens in a
mission), the system cursor is shown and the item under it is focused
(GMenuItem::Focus, vtable +0xB0; GMenu::ItemFocusChanged unfocuses the
previous one) and activated on a click (Activate(true/false), +0xC0), the
calls GfsPointer::MenuCheck and Input_Action make. Parts of compound items
(a GSliderControl's bar) resolve to the row the menu lists; a click on the
bar's left or right half is a d-pad tap (held: repeats). GNonCursorableButton
hints ("A Select", "B Back") are clickable. A click on a screen with
nothing to point at is A; right button B; wheel d-pad up/down. Item flags
(+0x10C): 0x4 and 0x10 both set = focusable (Focus checks them), 0x20
focused, 0x100 part of a compound item, 0x200 pressed. The mission
briefing (GExitMissionGoalsButton, 0x00069F50) is a modal loop in input
mode 8 ending on START; a click, Enter or Space are START there. That
loop polls input (CXBOXInput::Poll via GInputDevice::Process) but never
presents, and SDL's events were only read in Present: on a real window the
briefing could not be closed (and the window not either; scripted input
had hidden it). Poll now reads the window's events and shows the last frame
again when nothing was presented since the previous poll, sleeping 5 ms so
the loop does not spin a core. Windows log: both streams append to
avpe.log unbuffered (the _dup2 of stderr onto stdout lost stderr under
Whisky's Wine).

**Briefing picture, fog of war (2026-10-15).** The NV2A samples linear
(unswizzled) textures with texel coordinates, not 0..1; the minimap's fog
(256x256 LIN_A4R4G4B4, alpha 0 where revealed, over a 128x128 terrain
texture) was sampled at its clamped edge and stayed black. Linear stages
now divide by textureSize. Direct3D's Clear without rectangles clears the
viewport: the briefing draws its picture (six 256x256 tiles) for four
frames, then clears and redraws only its text panel (viewport 335,30
265x395) each frame; clearing the whole surface had erased the picture.
The briefing's modal loop redraws "Press START" without presenting, and
the idle-window path now shows it. Sub-viewports get no 2D widescreen
shift (it clipped the briefing text), and a widescreen frame that draws
nothing outside the 4:3 layout gets black side bands. Checked on Marine 1,
Predator 1 and Alien 1, Linux and Wine.

**HUD buttons by mouse (2026-10-15).** In play the game's pointer is the
cursor; its picking never reaches HUD buttons (no owner, as in the menus).
A left click whose pointer position (GfsPointer GetPos, vtable +0x4C) lies
in a HUD item's captured rectangle (an item under the GAvPMenu, minimap
excepted, smallest rectangle wins, the GInterfaceButton unit panel is a
container and not a target) focuses and activates it and is hidden from
the game, release frame included. The dropship button opens the Dropship
Uplink (GOrderingMenu : GMenu under GInGameMenu, not a GBaseMenu); during
it the game's pointer is asleep (SleepyTime -1e10), which now also turns
the menu mouse on: rows, Order/Clear/Exit hints, and a click on the intro
skips it. Checked: reinforcement ordered by mouse only, Linux and Wine.

**Minimap click, wheel zoom (2026-10-15).** A left click or drag inside
the minimap's screen rectangle (GMiniMap +0xF88/+0xF8C origin, +0xF70/+0xF74
size) converts the map point to the world the way GMiniMap's own helper
`0x75CF0` does (world = u x +0xF6C - +0xF80) and calls
`GAvPCamera::SetFocus` (`0x29810`, ecx = the camera singleton at
`0x2F6AF4`). The minimap is tested before the HUD buttons. The wheel pushes
the camera through `0x29F50`, the function the PC build's Keypad + / -
handler (`0x29D50`) calls each held frame, at 6x the key's rate for 5
frames a notch.

`0x29F50` keeps the eye Offset (+0x13C) in spherical form and adds the
zoom to its length with no limit at all (the keypad zoom was a developer
control). Out past about 2.2x the starting distance the far plane cuts the
terrain off from the top of the screen (measured on Marine 1: fine at 74.7,
cut at 78.6, start 34.25); in past zero the length goes negative and the eye
flips under the map. The PC layer hooks `0x29F50` and holds the length
within 0.6x..1.75x of the camera's starting distance (taken the first frame
each new camera exists), rescaling Offset so the angles are kept; a flip is
undone to the near limit. The limits cover the keypad too.
`AVPE_PC_ZOOMRANGE=near,far` (0 removes them).

Next: optional HUD anchoring (minimap to the right edge) through the layout
objects, then the Mac build. Main track is the decompilation again.
