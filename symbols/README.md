# Xbox symbol database

`xbox_symbols.csv` maps Xbox `default.xbe` function addresses to the real names
recovered from the unstripped PS2 build (`SLUS_201.47`, Metrowerks mangling).
Rebuild with `python3 -I tools/symdb.py . 48` (needs `vtmatch.json` and `funcs.json`).

Columns: `xbox` address, `tier`, `name` (PS2 mangled name), `ps2` address,
`evidence` (+-joined kinds), `alternatives` (competing candidates, never applied).

## Evidence kinds
- **vtable**: same slot of the same class's vtable on both platforms. All 871
  shared classes have identical slot counts.
- **registry**: the class-registration initializer passes this function as the
  `Create` factory alongside the class-name string.
- **callgraph**: a named function calls it at the aligned position where the PS2
  counterpart calls the PS2 function.
- **callees**: its ordered list of already-named callees matches exactly one PS2
  function, uniquely on both sides.
- **string**: it references a set of string literals unique to it on both sides.
- **callseq**: after naming, the full call lists agree position by position (>= 2 calls).

## Tiers
- **A**: vtable or registry. Exact structural identity.
- **B**: two or more independent kinds among callgraph, callees, string, callseq.
- **C**: exactly one of callgraph, callees, string.
- **D**: hypothesis. Conflicting or unconfirmed; the `name` is a candidate, do not rely on it.
  Includes every non-A name that matched more than one Xbox address (ambiguity pass).
- **F**: folded. MSVC merged identical functions into one copy; every PS2 name in
  `alternatives` maps here. Usually trivial bodies.

## Measured accuracy (holdout self-test, `HOLDOUT=0.2`)
20% of vtable-derived names hidden, then re-derived by the other methods only:
tier B 27/27 correct, tier C 34/35 correct. The single miss named the right
method on a sibling class with an identical body (`DoTerminalEffects` on
`GMarineCommTech` instead of `GSkullKeeper`). That is the known tier C failure
mode: confirm the class from the vtable or the `this` usage before relying on it.

## Current counts (after the ambiguity pass)
A 3,323 · B 48 · C 464 · D 106 · F 155. A name may sit on two addresses only when
both are tier A (two such cases: `GStraightMover::MoveTo`, `GProjectileMover::MoveTo`,
the same source function emitted twice). The holdout self-test is unchanged by
the pass (tier B 27/27, tier C 34/35).

## Used by the native build
`tools/apply_names.py` writes tiers A-C into xboxrecomp's `functions.json`, so
the lifted C carries them (`GAvPObject__DoFaceHug`, `GMarine__Create`). Free
functions get a `guest_` prefix so CRT names never collide with the host libc.
