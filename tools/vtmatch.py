#!/usr/bin/env python3
"""Name Xbox virtual functions from the PS2 build's vtables.

Both compilers lay a class's virtual functions out in declaration order, so
slot i of a class's Xbox vtable (funcs.json, found through RTTI) is the
function named in slot i of that class's PS2 vtable (__vt__<len><Class>, whose
first two words are the RTTI pointer and an offset, hence +2).

Output: vtmatch.json, read by symdb.py:
  names      Xbox address -> PS2 name (one name everywhere it appears)
  conflicts  Xbox address -> every PS2 name seen in its slots, when they
             differ (identical code folded by the Xbox linker: one function,
             many names; symdb leaves these unnamed or tier F)
  classes    classes matched on both sides

PS2 symbols are read with the binding column's quirk handled: weak symbols
(inline methods defined in headers: CMover::Moving, ::Stationary, ...) print
"<processor specific>: 13" there, so the name is always the last field.
"""
import json, os, struct, subprocess, collections
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
ELF = os.path.join(R, 'ps2', 'SLUS_201.47')
elf = open(ELF, 'rb').read()
e_phoff = struct.unpack_from('<I', elf, 0x1C)[0]
e_phnum = struct.unpack_from('<H', elf, 0x2C)[0]
ph = [struct.unpack_from('<8I', elf, e_phoff + 32 * i) for i in range(e_phnum)]


def rd32(va):
    for _t, o, v, _p, fs, _ms, _fl, _al in ph:
        if v <= va < v + fs:
            return struct.unpack_from('<I', elf, o + va - v)[0]
    return None


funcs, vts = {}, {}
for line in subprocess.run(['readelf', '-s', '-W', ELF], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) < 8 or not p[0][:-1].isdigit():
        continue
    name = p[-1]
    if name in ('DEFAULT', 'UND'):
        continue
    if p[3] == 'FUNC' and int(p[2], 0) > 0:
        funcs.setdefault(int(p[1], 16), name)
    elif p[3] == 'OBJECT' and name.startswith('__vt__'):
        vts[name] = (int(p[1], 16), int(p[2], 0))


def mangle(c):
    return '%d%s' % (len(c), c)


# Xbox vtables from RTTI (funcs.json: vtable address -> [decorated class
# name, slots]); classes with more than one vtable (multiple inheritance) are
# left out, their secondary tables do not line up with the PS2 layout.
xvt = collections.defaultdict(list)
for va, (deco, slots) in json.load(open(os.path.join(R, 'funcs.json')))['vtables'].items():
    if deco.startswith('.?AV') and deco.endswith('@@') and '@' not in deco[4:-2]:
        xvt[deco[4:-2]].append(slots)
seen = collections.defaultdict(set)
matched = []
for cls, tabs in xvt.items():
    v = vts.get('__vt__' + mangle(cls))
    if not v or len(tabs) != 1:
        continue
    pva, psize = v
    xs = tabs[0]
    n = min(len(xs), psize // 4 - 2)
    if n <= 0:
        continue
    matched.append(cls)
    pn = [funcs.get(rd32(pva + 4 * (i + 2))) for i in range(n)]
    # MSVC puts a class's overloaded virtuals next to each other in REVERSE
    # declaration order; the PS2 compiler keeps declaration order. Reverse
    # each run of same-named slots (GUnitMover's MoveTo(HGOBJECT) /
    # MoveTo(const CVector &) at Xbox slots 23/24 confirm it: 0x7FFF0 stores a
    # handle, 0x7FF10 copies a vector).
    i = 0
    while i < n:
        j = i
        meth = pn[i].split('__')[0] if pn[i] else None
        while j + 1 < n and meth and pn[j + 1] and pn[j + 1].split('__')[0] == meth:
            j += 1
        if j > i:
            pn[i:j + 1] = pn[i:j + 1][::-1]
        i = j + 1
    for i in range(n):
        if pn[i]:
            seen[int(xs[i], 16)].add(pn[i])
names = {'0x%x' % x: sorted(ns)[0] for x, ns in seen.items() if len(ns) == 1}
conflicts = {'0x%x' % x: sorted(ns) for x, ns in seen.items() if len(ns) > 1}
json.dump({'names': names, 'conflicts': conflicts, 'classes': sorted(matched)},
          open(os.path.join(R, 'vtmatch.json'), 'w'), indent=0)
print('classes matched: %d; Xbox virtual functions named: %d; folded (several names): %d' % (
    len(matched), len(names), len(conflicts)))
