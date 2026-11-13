# AvP: Extinction (Xbox, USA) inventory, 2026-10-14

First look at the author's own disc. Everything below was measured from `default.xbe` and the disc files, not taken from the web. Estimates are labelled.

## The disc

- Source: `Aliens Versus Predator - Extinction (USA).xiso.iso`, 612,040,704 bytes, SHA-256 `efeabab72591c69d1ccb56ad033e19167ea2d1b279e72e38cb90277c8cbab543`.
- 203 files, 611 MB. Most of the size is filler: `cerbera.tvr` is 211.7 MB of zeros behind a 44-byte WAV header.
- Real content:
  - `default.xbe`, 2.6 MB: the whole game.
  - `tbd/`, 68 `.tbx` files in the root and 27 level folders: all game data (levels, meshes, textures, UI, sounds, scripts).
  - 129 `.wma` files: music, ambience, tutorial and mission voice.
  - 4 `.xmv` videos: Zono, Fox and EA logos, and the intro.

## The executable

- Title "AvP: Extinction", title ID `0x56550022` (VU-034), retail signing key.
- Built 2003-06-26 18:52 UTC, the documented final build date.
- Build path left in the header: `L:\avp\out\XBOX\release\avp.exe`. Zono's project was called `avp`, with per-platform output folders.
- **XDK 5455** for all seven libraries (XAPILIB, LIBCMT, XBOXKRNL, D3D8, XGRAPHC, DSOUND, XMV), QFE 1.
  - **No LTCG.** The library is plain `D3D8`, not `D3D8LTCG`. Functions compile independently, which helps both matching and per-function testing.
  - Compiler: XDK 5455 is an early-2003 kit, so most likely Visual C++ 7.0 (VS .NET 2002). **Unverified**; confirm by comparing CRT functions.
- Code: `.text` is 1.44 MB. Separate sections hold Microsoft library code: D3D, XGRPH, DSOUND, WMADEC, XMV, XPP, DOLBY (about 0.6 MB together).
- 109 kernel imports. That's the system-call surface a native port has to provide.

## Function count (estimate, recursive-descent scan with capstone)

- **`.text`: about 6,800 to 8,800 functions.**
  - 4,695 are confirmed: direct call targets or virtual methods.
  - The rest are only referenced through data pointers. 2,103 of them sit on a 16-byte boundary after padding, so they're probably real; the remainder may be jump-table targets.
  - This includes statically linked CRT and XAPI code, likely several hundred functions.
  - Ghidra will give the exact figure.
- Microsoft library sections: about 2,050 functions. These get identified by signature and replaced, not decompiled.
- For comparison: Halo 2 has 11,318 game functions, and AvP2's `lithtech.exe` 4,774.

## Names: RTTI survived

- **899 C++ class names** are in the binary as RTTI type descriptors.
- **886 vtables** resolve to their class, which attaches **2,967 virtual functions** to a named class (for example `GMarine::vf12`) before any reverse engineering. That's roughly 40% of the game code.
- Engine classes (`C*`): renderer, collision, animation, fonts, sound, the RIFF loader.
  - Examples: `CRenderer`, `CRendXBMesh`, `CHierarchyAnimator`, `CGameTimer`, `CFrameRate`, `CTbdLoad`, `CZRiffFile`.
- Game classes (`G*`): about 820, covering every unit, weapon, attack, AI task, trigger, action, menu and UI element.
  - Examples: `GMarineSmartGunner`, `GPredatorAI`, `GTaskGetResourcesAlien`, `GFOWManager`, `GPathfindingMover`, `GObjectiveManager`, `GTechTree`.
- **Cut multiplayer is still in the binary**, confirming the developer interview: `GAvPMultiPlay`, `GAvPMultiReceiver`, `GMultiPlay`, `GMultiplayerSynchronizingMenu`, `GNoHostPotentialTrigger`.
- **Developer tools are in it too**: `GDebug`, `GDebugManager`, `GParticleEdit`, `GEmitterEdit`, `GHausSnooper`, and a `DebugAIButton`.
- **Undocumented cheats**: besides the known cheat menu entries, the string table has `Cheats_Debug`, `Cheats_CameraMode`, `Cheats_Layer7`, `Cheats_LightTree` and `Cheats_Outline`.
- PS2 code compiled into the Xbox build (`CPS2Icon3D`, a DualShock 2 controller layout file on the disc) confirms one shared multi-platform codebase.
- **Engine lineage confirmed**: the data format is "TBD", the same name as Metal Fatigue's `TBD\` data folder.

## Data format: TBD2

- Every `.tbx` is a RIFF file of form `TBD2`, containing `LIST XBOX` blocks.
- **Filenames are hashed**: uppercase name, CRC-32 without the final inversion. Solved for 11 of the 16 distinct names.
  - `370BC447` = `MASTER.TBD`.
  - In each mission folder (`M01` to `M10`, `P01` to `P11`, `A01` to `A08`, `T01` to `T05`, `Attract`): `FD090663` = `BACKGROUND.TBD` (the level) and `06F4F187` = `LOGO.TBD`.
  - Also: `CURSORS.TBD`, `MAINMENU.TBD`, `INTRO_MOVIE.TBD`, the three ending menus, `GAMEPADXBOXUSA.TBD` and `GAMEPADDUALSHOCK2PS2.TBD`.
- It is a serialized object database. Each block has:
  - `TYPE`: class hash and offset per object;
  - `DATA`/`DATX`: the object bytes;
  - `OFFS`: pointer fixups;
  - `PUBL`/`EXTN`: exported and imported symbols;
  - `TEMP`/`TEMX`: templates;
  - `EXTA`, `PLAT`/`PLAX`, `HNDL`.
- Class hashes use the same CRC, so they resolve to the RTTI names. Across all 68 files, **197,338 of 197,368 typed objects resolve to a named class** (21 classes; one hash still unknown).
  - Examples: `CRendXBMesh` (10,222), `CRendTexture`, `CHierarchyAnimation`, `CSoundSample`, `CRecordedScript` (31), and the "Haus" template and fill-data classes that appear to instantiate game objects.
- Consequence: levels are data, not code. A dumper can list every object, unit, trigger and script in every mission. The data is loaded in place as 32-bit structures with pointer fixups, so the first native build stays 32-bit, just as with Concrete Jungle.

## The PS2 build: full symbols

- Source: `Aliens Versus Predator - Extinction (USA).bin/.cue` (CD, MODE2/2352, 268,924 sectors), converted to ISO here.
- `SYSTEM.CNF`: `BOOT2 = cdrom0:\SLUS_201.47;1`, `VER = 1.00`, NTSC.
- Disc contents: `SLUS_201.47` (4.8 MB), IOP modules in `IRX/`, `IOPRP242.IMG`, and all game data in one archive, `TBD/TBF.TBF` (76 MB). That archive is RIFF form `TBFF` with an `INDX` chunk of name hashes, offsets and sizes.
- **`SLUS_201.47` shipped unstripped.** ELF "not stripped", with `.symtab` and `.strtab` intact and a `.relmain` relocation section.
  - 27,704 symbols: 9,923 functions and 16,843 data objects.
  - About 6,900 of the functions are methods of the game's `G*` and `C*` classes. 246 are Sony SDK.
  - The names are mangled C++ with full parameter types, for example `DoFaceHug__10GAvPObjectFP18CFaceHuggedMessageb` = `GAvPObject::DoFaceHug(CFaceHuggedMessage*, bool)`.
  - 898 vtables are named (`__vt__7GMarine`, and so on).
  - Compiler: Metrowerks CodeWarrior for PS2 (a `.mwcats` section).
  - The PC-era platform shims are still present (`QueryPerformanceCounter__FPl`).
- **Cross-match to Xbox, already done for virtual functions** (`tools/vtmatch.py`):
  - 871 classes exist in both builds. **Every one has the same number of vtable slots** on both platforms, which confirms the two builds come from the same source.
  - Carrying names slot by slot gives **2,800 Xbox virtual functions a real name**.
  - 155 Xbox addresses received several names. These are almost all tiny identical functions (empty bodies, one-line returns) that Microsoft's linker merged into one copy. That's expected.
- **Symbol database built** (`symbols/xbox_symbols.csv`, project copy `claude/avp-extinction-xbox-symbols.csv`): 3,947 Xbox functions named, each graded by evidence.
  - Tier A (exact structure): 3,323. Vtable slots plus 830 `Create` factories found through the class-registration initializers.
  - Tier B (two independent kinds of evidence): 48. Tier C (one kind): 508. Tier D (hypothesis, not applied): 68. Tier F (functions MSVC merged): 155.
  - Holdout self-test: with 20% of vtable names hidden, the other methods re-derived them correctly 27/27 at tier B and 34/35 at tier C. The one miss named the right method on a sibling class.
  - Limits: Microsoft's compiler inlined many small helpers that CodeWarrior called, and leaf functions without calls or strings can't be reached by call-graph matching. Those get named during decompilation.
- Marine mission 1's assembly is mapped in `claude/avp-extinction-m01-level.md`.

## What this means

- **Size**: medium. Roughly 6,000 to 8,000 functions of game and engine code, smaller than Halo 2 and above AvP2's engine alone.
- **Head start**: exceptional. The PS2 symbols name essentially every function with its real C++ signature, and the Xbox build lines up with them class for class. Together with the RTTI and the fully typed data format, the decompilation can work in Zono's own names, types and file layout from day one.
- Metal Fatigue drops to a secondary reference: still useful to watch the shared engine running on PC, no longer needed for names.
- Not yet verified: the simulation timestep, determinism, the random number generator, and the main loop. `CGameTimer`, `CFrameRate` and `GFrameHistory` are the starting points.

## Recommended route

1. **Static recompile with xboxrecomp for a native 32-bit Windows build first.** Replace the Microsoft library sections with the runtime's D3D8, DirectSound and XAPI layers. WMA audio and XMV video need replacements, likely via FFmpeg.
2. **Decompile into readable C++ class by class**, using the RTTI hierarchy as the file layout. Verify each function against the original by running both in an emulator (unicorn) on the same inputs; byte-matching only where cheap.
3. **Priority**: timing and main loop, then camera and UI scaling (widescreen), then input (mouse and keyboard), then the renderer.

## First milestones

1. Ghidra project (built from source, since release downloads are blocked here) with XDK libraries labelled, the PS2 names applied (virtual functions now, call-graph propagation next), and an exact function count.
2. TBD dumper: a full object listing for `M01` and `MASTER.TBD`, so we can see how levels and units are defined.
3. xboxrecomp lift, and a native build that boots to the Zono logo or main menu.
4. Reference captures in xemu on the author's PC, driven remotely.

## Risks

- xboxrecomp is young; its runtime may not cover everything this game uses.
- XMV video and the WMA decoder need replacements.
- The compiler version is unconfirmed; it only matters if we chase byte-matching.
- RTS determinism is unknown until we find the timer and the random number generator.

## Workspace notes

- Files live in `/home/claude/avpe` (git: tools only; game files are gitignored). The workspace can reset; the XISO can be re-staged from the author's own machine (split into `xiso_part_aa` and `xiso_part_ab`).
- The PS2 image re-stages the same way (`ps2_part_aa`, `ps2_part_ab`; rejoin, then strip each 2352-byte sector to its 2048 data bytes at offset 24).
- Metal Fatigue: the GOG installer `setup_metal_fatigue_1.04_hotfix_(21665).exe` is staged (`mf_part_aa`, `mf_part_ab`) but not yet unpacked.
- Not yet available: the May 2003 Xbox prototype.
