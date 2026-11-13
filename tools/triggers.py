#!/usr/bin/env python3
"""Dump the input triggers of the running native build: every CInputTrigger
on the list at 0x2F6820 (loaded from the controller layout .tbd), with the
action name (matched by its CRC against the strings in the XBE) and its keys
(button code, device mask, test: pressed/released/held/up/changed/read).

    triggers.py            (the game must be running: tools/peek.py attaches)
"""
import os, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()


def xrd(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]


T = struct.unpack('<256I', xrd(0x26AFA0, 1024))


def crc(s):
    c = 0xFFFFFFFF
    for b in s.encode():
        c = T[(c ^ b) & 0xFF] ^ (c >> 8)
    return c


names = {}
for m in re.finditer(rb'[A-Za-z0-9_ ]{3,64}\x00', xbe):
    s = m.group()[:-1].decode()
    names.setdefault(crc(s), s)
sys.argv = [sys.argv[0], '0']
import peek
TESTS = ['pressed', 'released', 'held', 'up', 'changed', 'read']
t = struct.unpack('<I', peek.rd(0x2F6820, 4))[0]
n = 0
while t and n < 500:
    vt, nxt, ident, c, nk, flag = struct.unpack('<IIIIBB', peek.rd(t, 18))
    keys = []
    for k in range(nk):
        button, dev, test = struct.unpack('<HBB', peek.rd(t + 0x14 + 4 * k, 4))
        keys.append('%04X/dev%02X/%s' % (button, dev, TESTS[test] if test < 6 else '%02X' % test))
    print('%08X %-34s C=%08X keys: %s' % (t, names.get(ident, '%08X' % ident), c, '  '.join(keys)))
    t = nxt
    n += 1
