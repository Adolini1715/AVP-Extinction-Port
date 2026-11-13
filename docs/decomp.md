# Decompilation: method and status (2026-10-15)

Target: verified-equivalent decompilation, as close to Zono's source as the
evidence allows; then a PC build in Zono's own structure (CRendAPI platform
back end, the existing mouse/keyboard pointer code); then modernisation
(widescreen, performance). Source lives in the private repo
`/home/claude/avpe-decomp` (README there has the workflow).

## What the discs give us

- **Scale:** 8,004 game functions (1.29 MB of code). Above 0x141000 the
  Microsoft C runtime and system libraries (778 functions) are replaced by the
  platform layer, not decompiled; the 2,102 static initialisers the compiler
  put up there are game code and counted as such (`tools/srcmap.py` finds them
  through the CRT initialiser table at 0x265D94). 92 entry points the function
  finder made inside other functions' bodies are marked `alias`, not counted.
- **Files:** 588 source files. 93 carry Zono's real names (PS2 symbol table);
  the rest are named after their main class. Placement evidence per function
  in `symbols/source_map.csv`.
- **Names:** `symbols/xbox_symbols.csv`, 4,100+ named from the PS2 build
  (3,593 tier A: vtable or registry identity). Two parser bugs fixed on
  2026-10-15: weak symbols (1,434 inline methods defined in headers) were
  dropped, and MSVC's reverse order for overloaded virtuals was not
  accounted for (34 swapped names, e.g. both `MoveTo` overloads). Both are
  handled in `tools/vtmatch.py`.
- **Field names:** every class describes its editable members to Zono's level
  editor in `SpecifyEditable`: name (the string, e.g.
  `MoverData.MaxRunSpeed`), offset, size, C type. `tools/editables.py`
  extracts 2,322 fields of 297 classes (`symbols/editables.json`). These are
  Zono's own member names; decompiled classes use them (no `m_` prefix;
  `m_XXX` marks names we chose).
- **Classes:** 883 matched between the Xbox RTTI and the PS2 vtables, single
  inheritance. GObject is 0x34 bytes (the size of every direct subclass
  without members).
- **Calling conventions:** link-time code generation; `tools/abi_regs.py`
  computes every function's register inputs (`symbols/abi.json`).
- **PC heritage:** the pointer/control code was written for a mouse
  (GAvPPointer Input_PressMouse1/2, CheckBorderPan edge scrolling,
  GetFirstObjectUnderCursor, menu HotKeyActivate, bGamePadMouse); the renderer
  is split by platform (CRendAPI::PlatformInit/ChangeDisplayMode).

## Verification

Every decompiled function is checked against the original machine code: the
native build snapshots guest state at entry to and exit from the decompiled
function; the oracle (Unicorn, `tools/snap_replay.py`, with the game's x87
precision 0x023F) replays the original from the entry state; registers the
function returns and all of memory must match (`tools/verify_lib.py`:
except dead stack below esp and what other threads change meanwhile:
worker stacks, the loader's countdown and busy flag).

- `tools/verify.py <va>`: one function per game run: a natural call (the
  n-th time the game calls it), a probe (called on purpose with chosen
  registers, arguments and memory pokes, for paths the game does not take),
  or a check at the moment it calls a given callee.
- `tools/verify_multi.py`: many functions per game run. The bridge streams a
  memory image and, for each captured entry/exit, the registers and the 4 KB
  pages that changed (`verify/multi.stream`); each function is then checked
  as above. Natural calls (the n-th call of every listed function) or a file
  of probes run back to back. 451 Create functions verify from one boot; 3,586
  probes from one run at frame 40.
- `tools/reverify.py <File.cpp>`: re-runs every recorded check after a
  refactor. It caught a field mix-up in the mover hierarchy refactor (a base
  class field shadowing the intended one).
- Mission code: `AVPE_LEVEL=M01/background.tbd AVPE_LEVEL_AT=3` loads Marine 1
  in place of the attract demo; `tools/mission_pad.sh` closes its briefing
  and works the pad, so a verify run reaches play (the briefing's modal loop
  stops the frame count until START). Probes run at a frame after that
  (`--frame 1150`); `[2F6990]+OFF` style values reach live objects, and
  `@C<Create va>` the last object a class made (the run log lists them).
- Callees the oracle cannot run (a kernel call inside them, such as a sound
  starting): `--stop-at <callee>` compares at the call.
- The replay stops at the caller's return address only when the stack has
  unwound past the entry frame (a nested call from the same call site, a
  Create whose constructor creates another object of its class, reached it
  first and made 33 checks fail spuriously).

## Generated code

Much of every class is the same few shapes compiled with different
constants. `tools/classgen.py` reads each such function's machine code,
accepts it only when it is exactly a known shape (interpreting it: e.g. every
class id must reach `true` and any other value the parent's test), and
writes C++ instances of generic code (`include/avpe/classgen.h`) into the
class's source file between AVPE-CLASSGEN markers:

| Kind | Functions |
|---|---|
| Create (factory: operator new, construct, PostConstruction) | 698 |
| IsDerivedFromClassId | 828 |
| GetParentClassId | 57 |
| SpecifyEditable (field tables, recovered by running each in Unicorn: straight-line code, so one run is every run) | 294 |
| Static initialisers: class registration (GObject::AddClassId) | 830 |
| Static initialisers: name CRC constants | 904 |
| Static initialisers: message types (MessageTypeEntry::Add) | 92 |
| Message factories (CXxxMessage::Create, placed beside their registration) | 92 |
| Static initialisers: the name table (Behavior_Fight style handles) | 67 |
| Static initialisers: atexit | 47 |
| GetParentClassId the symbol table left unnamed (`return <class id>`) | 80 |
| Create with its constructor compiled in (stores, calls, `if (!ClassTableBuild)` blocks), by symgen | 108 |
| Static initialisers that copy data or register a static object, by symgen | 104 |
| GTask subclass constructors and straight-line twins (notes/twins.md rows), by symgen | 130 |
| The long tail (tools/symgen_tail.txt: accessors, setters, flag tests, short branching methods), by symgen, every path verified | 274 (14,105 bytes) |

`tools/symgen.py` covers the variants: it runs a function's machine code
symbolically along every path (forward branches, a few tests: the build
flag, a loaded field, an x87 compare), records the calls (registers and
stack arguments the callee reads, abi.json), stores and result, and writes
them as C++ statements, each under the tests of the paths that run it.
Anything it cannot model exactly rejects the function. `tools/classgen_skip.txt`
lists the ones that failed verification or could not be verified, with why.
`classgen.py --exclude A.cpp,...` (and `gen_decomp.py --exclude`) leaves files
someone is editing untouched.

A function written by hand replaces its generated entry (the generator skips
functions defined elsewhere, and those the bridge wraps for tracing).

### The long tail (`tools/symgen_tail.txt`)

Small functions of every kind (accessors, setters, flag tests, short
methods with a few branches and calls) that symgen writes out exactly are
listed in `tools/symgen_tail.txt` and generated by classgen like the
shapes above (`plain(..., exact=True)`). Only functions whose every path
passed a check are listed; the rest stay lifted. symgen's docstring lists
what it models for them (input registers, partial registers, register
arithmetic, every jcc/setcc after cmp/test, x87 compares through fnstsw as
one C++ compare, results per path, tail calls, a stack in guest memory for
functions that pass the address of a local, a check that every reachable
instruction is on some path); loops, indirect jumps and string
instructions still reject. Two rules matter for exactness beyond memory:
eax is returned exactly as the original leaves it (callers read it: one
generated function's garbage eax made another's verification fail), and a
function whose eax cannot be known (fnstsw status bits) is rejected.

Path coverage: `AVPE_PATHS=1` makes verify_multi print, for each passing
check, the function's instructions the original ran (`range-trace:`, from
`snap_replay.py --trace-range`); a symgen path counts as verified when a
passing check ran exactly its instructions. `tools/symprobe.py` writes
probes that drive each path: values for what the path's tests read, found
by searching around the constants they compare with; `this` and every
pointer the function follows point into the probe scratch block @S (each
pointer its own region), or, for functions that call other code,
`symprobe.probes_for(va, live=True)` probes a live object of the method's
class (`@C<Create>`, pokes only on the words the tests read).

Workflow for a batch: add VAs to symgen_tail.txt, `classgen.py --exclude
<files being edited>`, rebuild, natural calls at boot (`pipe.sh multi`) and
in Marine 1 for the rest (`pipe.sh mission`), probes, then keep only the
VAs whose every path passed and regenerate, all in one sitting (unverified
generated code must not stay in the tree between runs).

## The PS2 build as a cross-reference

`tools/ps2dis.py <mangled name>` disassembles a function of the PS2 release
(its symbol table is intact) with globals and callees named. Same source, a
different compiler with no link-time code generation, so it shows what the
Xbox build inlined or merged: GAvPCamera::Move inlined into its callers,
CTbdFixupManager lookups folded to one call, the real names of statics
(`NowhereVector`, `GMiniMap::pMiniMapMenu`, `GFOWManager::pThe`), and which
TU a weak function was emitted in. Field offsets differ by the size changes
of the classes in between (GMenuItem is 0x240 bytes there, 0x250 here).

## Coverage

`recomp/campaign.py` plays the campaigns unattended with the instrumented
build: random pad input for four minutes per mission, then the cheat code
(R L R R L R L L R on the triggers while paused: the pause menu walks the
string "RLRRLRLLX" at 0x241D08), Invulnerable, Clear Fog Of War, Cheat Win,
the win screen, Next Level; at a campaign's end it quits to the main menu and
starts the next. All three campaigns (21 missions): 6,658 of 8,004 game
functions have run (83.2%, 86.5% of code bytes; `tools/coverage.py`, per
level). Never run: mostly menus not visited (save/load/options/profile),
specific unit abilities, editor and debug code.

## Status

`avpe-decomp/tools/progress.py`, 2026-10-15 (evening):

| | Functions | Code bytes |
|---|---|---|
| Game total | 8,004 | 1,290 KB |
| Decompiled | 4,085 (51.1%) | 405 KB (30.7%) |
| Verified | 4,085 (all of them) | 405 KB |

Every decompiled function passes at least one check. Generated functions:
Create by its natural call at boot (all 698) and, for 95 classes the attract
demo instantiates, the full path (PostConstruction) as well;
IsDerivedFromClassId with one probe per class id plus one id that matches
none (3,586 probes); GetParentClassId by probe; SpecifyEditable and the static
initialisers by their natural call at boot. CGameTimer::Create is checked up
to its constructor call: the constructor reads the hardware timer, which the
oracle cannot reproduce.

Hand-decompiled so far: FrameRate.cpp and Minimap.cpp (complete: GMiniMap,
its HUD panel and button, the tutorial minimap triggers, MiniMapMarker; every
branch verified in Marine 1 by natural calls and probes), and in Mover.cpp
GUnitMover (15 functions, every branch verified: UpdateSpeed is what the
run-speed mod feeds), the CMover inline methods and the small
GPathfindingMover methods.
