#!/usr/bin/env python3
"""levelclasses.py: which classes each level file instantiates (TYPE tables of every
LIST XBOX block in disc/tbd/**.tbx), written to symbols/level_classes.json as
{class: [level, ...]}. The level of a file is its folder (M01, P11, ...) or, for the
shared files at the top, the file name. Class names: CRC-32 (no final xor) of the
names in classes.json and the XBE strings."""
import glob, json, os, re, struct, zlib, collections
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
h = lambda s: zlib.crc32(s.encode()) ^ 0xffffffff
names = {}
CLASSES = set(json.load(open(os.path.join(R, 'symbols', 'classes.json'))))
for c in json.load(open(os.path.join(R, 'symbols', 'classes.json'))):
    for v in (c, c[1:] if c[:1] in 'GC' else c):
        names.setdefault(h(v), c)
for line in open(os.path.join(R, 'strings.txt'), errors='ignore'):
    for w in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}', line):
        names.setdefault(h(w), w)
found = collections.defaultdict(set)
unknown = collections.Counter()
compressed = 0
def walk(d, o, end, lvl):
    global compressed
    while o + 8 <= end:
        cid = d[o:o + 4]; sz = struct.unpack_from('<I', d, o + 4)[0]
        if cid in (b'RIFF', b'LIST'):
            walk(d, o + 12, min(end, o + 8 + sz), lvl)
        elif cid == b'TYPE':
            n = struct.unpack_from('<I', d, o + 8)[0]
            for i in range(min(n, (sz - 4) // 8)):
                th = struct.unpack_from('<I', d, o + 12 + i * 8)[0]
                if th in names: found[names[th]].add(lvl)
                else: unknown[th] += 1
        elif cid == b'EXTA':
            # labels of the objects this block defines: Class_addr..._time..., or a class name alone
            for w in re.findall(rb'[A-Za-z_][A-Za-z0-9_]{2,}', d[o + 8:o + 8 + sz]):
                w = w.decode()
                for cand in (w, w.split('_addr')[0], w.split('_')[0]):
                    if cand in CLASSES:
                        found[cand].add(lvl); break
        elif cid in (b'BWJC', b'CMPR', b'ZLIB'):
            compressed += 1
        o += 8 + sz + (sz & 1)
files = sorted(glob.glob(os.path.join(R, 'disc', 'tbd', '**', '*.tbx'), recursive=True))
for f in files:
    rel = os.path.relpath(f, os.path.join(R, 'disc', 'tbd'))
    lvl = rel.split(os.sep)[0] if os.sep in rel else rel
    d = open(f, 'rb').read()
    walk(d, 0, len(d), lvl)
json.dump({k: sorted(v) for k, v in sorted(found.items())}, open(os.path.join(R, 'symbols', 'level_classes.json'), 'w'), indent=0)
print('%d files, %d classes found in level data, %d unknown type hashes, %d compressed chunks skipped' % (
    len(files), len(found), len(unknown), compressed))
