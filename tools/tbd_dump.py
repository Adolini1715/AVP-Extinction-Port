#!/usr/bin/env python3
"""Readable dump of one TBD block: objects (from TYPE), their public labels (PUBL),
and the named field records inside each object (decompressed DATX or DATA).

Usage: tbd_dump.py <file.tbx> <list-index> [--raw]
  --raw: also print the words of objects with no named fields, and EXTA strings
"""
import struct, sys, re, os
sys.path.insert(0, os.path.dirname(__file__))
import bwj
exec(open(os.path.join(os.path.dirname(__file__), 'tbd_fields.py')).read().split("if __name__")[0])

def bits_for(win):
    b = 0
    while not (win << b) & 0x8000: b += 1
    return b

def block_data(d, ch, key='DATX'):
    if key in ch:
        st, n = ch[key]; size, win = struct.unpack_from('<IH', d, st)
        out, _, ended = bwj.decompress(d[st + 6:st + n], bits_for(win))
        assert ended and len(out) == size
        return out
    plain = key.replace('X', '').replace('DATA', 'DATA')
    st, n = ch['DATA' if key == 'DATX' else plain]
    return d[st:st + n]

def dump(path, li, out=sys.stdout):
    d = open(path, 'rb').read(); ch = blocks(d)[li]
    data = block_data(d, ch)
    ex = ch.get('EXTA')
    labels = [x.decode() for x in re.findall(rb'[\x20-\x7e]{3,}', d[ex[0]:ex[0] + ex[1]])] if ex else []
    L = {}
    for l in labels:
        for v in (l, l.upper(), l.lower()): L[h(v)] = l
    st, n = ch['TYPE']; cnt = struct.unpack_from('<I', d, st)[0]
    objs = []
    for i in range(cnt):
        th, off = struct.unpack_from('<II', d, st + 4 + 8 * i)
        if not off & 0x20000000:                  # 0x2000xxxx entries live in EXTA, not data
            objs.append((off, NAMES.get(th, f'{th:08X}')))
    pub = {}
    if 'PUBL' in ch:
        st, n = ch['PUBL']; pc = struct.unpack_from('<I', d, st)[0]
        for i in range(pc):
            ph, off = struct.unpack_from('<II', d, st + 4 + 8 * i)
            pub[off] = L.get(ph, f'#{ph:08X}')
    objs.sort()
    # CDefaultFillData records carry no PUBL label; the debug labels list the
    # matching _G<Class>_DefaultParams in source order. Pair by order only when
    # the counts agree (verified, not assumed), and mark the name as inferred.
    dp = [l for l in labels if re.match(r'^_\w+_DefaultParams$', l)]
    dfl = [o for o, c in objs if c == 'CDefaultFillData' and o not in pub]
    if len(dp) == len(dfl):
        for o, l in zip(dfl, dp): pub[o] = l + '  (label by order)'
    bounds = [o for o, _ in objs] + [len(data)]
    for k, (off, cls) in enumerate(objs):
        print(f'\n[{off:05x}] {cls}  {pub.get(off, "")}', file=out)
        named = 0
        for roff, nm, v in records(data, off, bounds[k + 1] - off):
            if nm: print(f'    {nm:42s} = {fmt(v)}', file=out); named += 1
        if RAW and not named:                     # plain structs: show the words
            words = struct.unpack_from('<%dI' % ((bounds[k + 1] - off) // 4), data, off)
            print('    raw: ' + ' '.join('%08X' % w for w in words), file=out)
    if RAW and labels:
        print('\nEXTA strings: ' + ' | '.join(labels), file=out)
    return data

RAW = '--raw' in sys.argv
if __name__ == '__main__':
    dump(sys.argv[1], int(sys.argv[2]))
