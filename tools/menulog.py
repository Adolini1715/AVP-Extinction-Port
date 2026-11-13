#!/usr/bin/env python3
"""Translate [MENU] lines in a run log (item name CRCs) into names: every
string in the XBE's .rdata is hashed with the engine CRC (CRC-32 without the
final xor).  menulog.py <run.log> [--tail N]"""
import json, os, re, sys, zlib
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
x = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
names = {}
for s in info['sections']:
    if s['name'] in ('.rdata', '.data'):
        for m in re.finditer(rb'[\x20-\x7e]{2,64}', x[s['raw']:s['raw'] + s['rsize']]):
            t = m.group().decode()
            names[(zlib.crc32(m.group()) ^ 0xFFFFFFFF) & 0xFFFFFFFF] = t
lines = [l for l in open(sys.argv[1], errors='ignore') if l.startswith('[MENU]')]
if '--tail' in sys.argv:
    lines = lines[-int(sys.argv[sys.argv.index('--tail') + 1]):]
for l in lines:
    print(re.sub(r'(focus|activate) ([0-9A-F]{8})', lambda m: '%s %s' % (m.group(1), names.get(int(m.group(2), 16), m.group(2))), l.rstrip()))
