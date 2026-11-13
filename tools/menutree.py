#!/usr/bin/env python3
"""Print the object tree under a guest object of the running native build
(first child +0x08, next sibling +0x10), with each menu item's text (+0x148)
and command CRC (+0x110).  menutree.py <va> [--up N]  (--up walks to the
parent, +0x0C, N times first)"""
import struct, sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import peek
d = lambda va: struct.unpack('<I', peek.rd(va, 4))[0]
def text(va):
    t = d(va + 0x148)
    if not t or t > 0x8000000:
        return ''
    return peek.rd(t, 64).split(b'\0')[0].decode('latin1')
va = int(sys.argv[1], 16)
if '--up' in sys.argv:
    for _ in range(int(sys.argv[sys.argv.index('--up') + 1])):
        va = d(va + 0xC)
def walk(o, depth):
    n = 0
    while o and n < 200:
        print('%s%08X vt=%08X cmd=%08X %r' % ('  ' * depth, o, d(o), d(o + 0x110), text(o)))
        if depth < 4:
            walk(d(o + 8), depth + 1)
        o = d(o + 0x10); n += 1
print('%08X vt=%08X %r' % (va, d(va), text(va)))
walk(d(va + 8), 1)
