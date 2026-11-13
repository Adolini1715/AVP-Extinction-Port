#!/usr/bin/env python3
"""Decode the (hash, length, value) field records in a TBD block's DATX/DATA chunk.

Usage: tbd_fields.py <file.tbx> <list-index> [--raw]
Field-name hashes are resolved against every string in the executable (same
CRC-32-without-final-xor as filenames and class names). Records that do not
parse as (hash,len,value) are reported as gaps so nothing is silently skipped.
"""
import struct, sys, zlib, re

h = lambda s: zlib.crc32(s.encode()) ^ 0xffffffff
NAMES = {}
for line in open('strings.txt', errors='ignore'):
    line = line.strip()
    for w in set(re.findall(r'[A-Za-z_][A-Za-z0-9_.]{2,}', line)) | {line}:
        NAMES.setdefault(h(w), w)

def blocks(d):
    o = 12; out = []
    while o < len(d):
        sz = struct.unpack_from('<I', d, o + 4)[0]
        p = o + 12; ch = {}
        while p < o + 8 + sz:
            c = d[p:p + 4].decode('latin1'); s = struct.unpack_from('<I', d, p + 4)[0]
            ch[c] = (p + 8, s); p += 8 + s + (s & 1)
        out.append(ch); o += 8 + sz + (sz & 1)
    return out

def fmt(v):
    if len(v) == 4:
        i = struct.unpack('<I', v)[0]; f = struct.unpack('<f', v)[0]
        if i in NAMES: return f'#{NAMES[i]}'
        if i < 0x10000: return str(i)
        if 1e-4 < abs(f) < 1e7: return f'{f:g}f'
        return f'0x{i:08X}'
    if len(v) in (8, 12, 16) and all(1e-6 < abs(x) < 1e7 or x == 0 for x in struct.unpack(f'<{len(v)//4}f', v)):
        return '(' + ', '.join(f'{x:g}' for x in struct.unpack(f'<{len(v)//4}f', v)) + ')'
    s = v.split(b'\0')[0]
    if len(s) >= 2 and all(32 <= c < 127 for c in s): return repr(s.decode())
    return v.hex()

def records(d, st, n):
    """Yield (offset, name, raw value) or ('gap', bytes)."""
    p = st; end = st + n
    while p + 8 <= end:
        hh, ln = struct.unpack_from('<II', d, p)
        if hh in NAMES and 0 < ln <= 4096 and p + 8 + ln <= end:
            yield p - st, NAMES[hh], d[p + 8:p + 8 + ln]; p += 8 + ln
        else:
            yield p - st, None, d[p:p + 4]; p += 4

if __name__ == '__main__':
    d = open(sys.argv[1], 'rb').read(); li = int(sys.argv[2])
    ch = blocks(d)[li]
    key = 'DATX' if 'DATX' in ch else 'DATA'
    st, n = ch[key]
    named = gaps = 0
    for off, name, v in records(d, st, n):
        if name: named += 1; print(f'{off:05x}  {name:40s} = {fmt(v)}')
        else:
            gaps += 1
            if '--raw' in sys.argv: print(f'{off:05x}  ?? {v.hex()}')
    print(f'# {named} named fields, {gaps} unparsed words', file=sys.stderr)
