#!/usr/bin/env python3
"""Diff the oracle's memory after replaying ORIGINAL code (snapshot.bin.after,
from snap_replay.py) against the native build's own state after running the
LIFTED code from the same snapshot (avpe_snapshot at return).

Usage: snap_diff.py <before.bin> <lifted_after.bin>
Prints registers and every differing range, word by word, with the value
before, the original's value and the lifted value. Stack bytes below the final
esp are dead scratch and reported separately."""
import struct, sys
before, lifted = sys.argv[1], sys.argv[2]
B = open(before, 'rb').read(); L = open(lifted, 'rb').read(); O = open(before + '.after', 'rb').read()
hb = struct.unpack_from('<12I', B); hl = struct.unpack_from('<12I', L)
names = 'eax ecx edx ebx esp ebp esi edi'.split()
print('lifted regs after:', ' '.join(f'{n}={v:08X}' for n, v in zip(names, hl[1:9])))
hdrb = 64 if hb[0] == 0x32414E53 else 48; hdrl = 64 if hl[0] == 0x32414E53 else 48
b, l = B[hdrb:hdrb + hb[10]], L[hdrl:hdrl + hl[10]]
esp = hl[5]
diffs = []
for off in range(0, len(O), 4):
    if O[off:off + 4] != l[off:off + 4]:
        diffs.append(off)
live = [d for d in diffs if not (esp - 0x10000 <= d < esp)]
dead = len(diffs) - len(live)
print(f'{len(diffs)} differing words ({dead} in dead stack below esp); live differences:')
w = lambda buf, o: struct.unpack_from('<I', buf, o)[0]
for d in live[:200]:
    print(f'  {d:08X}  before={w(b, d):08X}  original={w(O, d):08X}  lifted={w(l, d):08X}')
