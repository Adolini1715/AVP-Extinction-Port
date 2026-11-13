# Marine mission 1: how the level is assembled (first pass)

File: `tbd/M01/FD090663.tbx` = `M01/BACKGROUND.TBD`, 5.6 MB, 23 `LIST XBOX` blocks.

## Each block is one compiled source script

The `EXTA` chunk of every block keeps the path of the script it was compiled from
and the labels of the objects it defines. Zono's sources were `.kfc` and `.tss`
text scripts under `P:\avp\code\TBDScripts\` (shared) and `P:\avp\art\maps\M01\`
(this mission). A mission is the shared scripts plus three of its own.

| Block | Source script | What it defines |
|---|---|---|
| 0 | `maps\M01\Objective.kfc` | Mission goals text and layout |
| 1 | `TBDScripts\MarineUIStuff.kfc` | Marine in-game interface: buttons, minimap, unit cap, dropship timer (207 labels) |
| 2-5 | `tbdscripts\mainmenuincludes.tss` | Menu templates: pause, options, load/save, bestiary, warnings |
| 6 | `TBDScripts\CommTowerUI.kfc` | Comm tower ordering and upgrade screen, one button pair per Marine unit type |
| 7-8 | (no path) | Shared decor and props: cave entrances, Rynth bones, termite hills, rocks, desert plants |
| 9 | `TBDScripts\Params_Weapons.kfc` | Every weapon's effects: muzzle flashes, beams (354 labels) |
| 10 | `TBDScripts\Params_Vehicles.kfc` | Dropship, drop pod, APC, landing beacons, tire tracks |
| 11 | `TBDScripts\Params_Unit.kfc` | Base unit defaults |
| 12 | `TBDScripts\Params_Alien.kfc` | Every Alien type: AI, eggs, hive, spores (265 labels) |
| 13 | `TBDScripts\Params_Marine.kfc` | Every Marine type: targeter, AI, mover, weapons, per class (131 labels) |
| 14 | `TBDScripts\Params_Predator.kfc` | Every Predator type: movers by speed, blades, plasma caster (156 labels) |
| 15 | `TBDScripts\Params_NPC.kfc` | Wildlife and civilians: Rynth, Kurn, Kriltic, Niloc |
| 16 | `TBDScripts\Params_Structures.kfc` | Structures, doors, alarms, demolition kit, comm tower |
| 17 | `TBDScripts\levelshare1.kfc` | Global managers: input, game timer, recorder, objectives, debug manager, multiplayer receiver |
| 18 | `tbdscripts\playermanager.tss` | Player managers per species, attract mode, tech tree, Marine start point |
| 19 | `maps\M01\map.kfc` | Terrain: tile textures (desert, cliffs, landing pad), fog of war, section manager |
| 20 | `TBDScripts\MapLoaded.kfc` | Post-load hook |
| 21 | `tbdscripts\EnvLight_DesertDay.tss` | Lighting, fog, steam and smoke, camera, minimap, Alien AI player |
| 22 | `maps\M01\LayoutParams.kfc` | Everything placed on the map (below) |

`Params_*` are the unit definition files. Every unit class is a bundle of
named parts (`MarineTargeter`, `MarineAI`, `MarineMover`, `MarineShadow`, and so
on) plus a `_G<Class>_DefaultParams` record. That is where stats and tuning
values will be.

## What is placed in Marine mission 1 (block 22)

| Count | Class |
|---|---|
| 624 | `GDecor` (scenery) |
| 64 | `GAlienDrone` |
| 27 | `GAlienRunner` |
| 17 | `GDecorDecal` |
| 11 | `GWaypoint` in 3 `GWaypointManager` routes |
| 5 | `GMarineInfantry` |
| 4 | `GDropShipLandingSpot` |
| 4 | `GAIDefensePoint` |
| 4 | `GFenceNode` |
| 3 | `GMiner`, 3 `GWorker` (colonists) |
| 2 | `GMarineCommTech`, 2 `GAIMeetingPoint` |
| 1 | `GMarineMedic` |
| 1 | `GMarineRecon` (see below) |

Plus named entries: `Alien_First_Wave`, `dozer01`, `dozer02`, `footprints1` to `footprints5`.

## Leftovers

- **Edit history.** Placement labels embed the editor's memory address and a
  Unix timestamp. The 378 timestamped placements in M01 run from 2001-09-07 to
  2002-04-23, so the mission's layout was finished more than a year before the
  game shipped (June 2003).
- **A cut unit, probably.** One placed object is labelled
  `GMarineRecon_addr31303336_time1008866288` (placed 2001-12-20). No class
  `GMarineRecon` exists in either the Xbox or the PS2 executable, so the level
  still references a Marine Recon unit whose code was removed. Inference from
  the label; not yet confirmed from the object data.

## Compression and field records (decoded 2026-10-14)

- `DATX`, `TEMX` and `PLAX` are `DATA`, `TEMP` and `PLAT` compressed with Zono's
  `CBWJCompressor`: a 6-byte header (u32 decompressed size, u16 window in words)
  then 16-bit-word LZ77. A control word carries 16 flags (high bit first): 0 =
  literal word, 1 = token `(distance << bits) | length`, where `bits` = leading zero
  bits of the window (5 for the usual 0x07FF). Token 0 ends the stream.
  - Decoder (`tools/bwj.py`) verified on all 1,097 compressed chunks on the disc:
    exact declared size, every byte consumed, end token present.
  - Compressor ported from the Xbox `CompressCore` (0x001331B0) reproduces the
    original compressed bytes exactly for chunks up to 16 KB, except the final
    token in 82 of them (Zono's search reads stale buffer bytes past the input
    end; the decoded output is identical). Chunks over 16 KB differ because the
    original works in 16 KB buffers; all of them still decode identically.
  - Oracle: `tools/xbox_decode.py` runs Zono's own `Decompress`/`Read` x86 code
    from `default.xbe` in Unicorn (only the allocator stubbed). It agrees with
    `bwj.py` on the original and the patched chunk.
- Field records inside the decompressed data are `u32 CRC(fieldname), u32 length,
  value`. Field names are the executable's strings (`Health`, `MaxHealth`,
  `MoverData.MaxRunSpeed`, `UnitStats.Defense.Kinetic`, `UpgradeCost` ...).
- Objects: `TYPE` gives class and offset; `PUBL` gives (label hash, offset) for
  labelled parts. The unlabelled `CDefaultFillData` objects are the
  `_G<Class>_DefaultParams` records; their labels come from the debug label list
  in source order (15 of 15 in the Marine block).
- `tools/tbd_dump.py <file> <block>` prints every object with its fields.

### Marine stats (Marine mission 1 copy, `Params_Marine.kfc`)

`_GMarineInfantry_DefaultParams`: Health 200, MaxHealth 200, UpgradeCost 450,
Defense.Kinetic 0.25, SkullValue 75, FormationPriority 7; upgraded:
HealthBonus +100, Defense.Kinetic 0.5, Defense.Acid 0.25.
`MarineMover` (shared by Infantry, Comm Tech, Medic): MaxWalkSpeed 0.8,
WalkAcceleration 50, MaxRunSpeed 2.5, RunAcceleration 5, TurnRate 10.
Each mission file carries its own copy of the `Params_*` blocks (MASTER.TBD has
none; MAINMENU.TBD has one for the menus).

## First test mod: Marine run speed 2.5 -> 10.0 (Marine mission 1)

- `tools/make_patch.py 54 a8 2.5 10.0 MoverData.MaxRunSpeed`: one byte of the
  decoded data changes; recompressed to 4,102 bytes and zero-padded to the
  original 4,104, so the file and XISO keep their sizes. All other chunks byte-identical.
- Same-size budget: health 200 -> 2000 did not fit (4,110 > 4,104; an optimal
  parser did not help), 600 did. Larger edits need the file-growth route
  (RIFF and directory-entry sizes) or an XISO rebuild.
- XISO: one 4,110-byte write at offset 415,704,400. Original SHA-256
  `efeabab7...cbab543`, patched `f84f3d2c...0d01b79`. Patchers:
  `apply_m01_speed.ps1` (PC) and `apply_m01_speed.sh` (Mac, tested end to end here).
- Status: verified by Zono's own decoder; **not yet confirmed in game**.

## Not decoded yet

Positions, waypoint routes and trigger logic in the layout block, the
`TEMX` templates and `PLAX` placement data, and the meaning of the
non-field words between records (object headers and pointers fixed up by `OFFS`).
