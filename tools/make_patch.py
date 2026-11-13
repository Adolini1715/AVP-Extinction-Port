#!/usr/bin/env python3
"""Build a same-size stat patch for M01: patch.py <object_start> <object_end> <old> <new> <field> [field...]
First test mod: MarineMover MoverData.MaxRunSpeed 2.5 -> 10.0 (Infantry, Comm Tech, Medic).

Same-size patch: the DATX chunk is recompressed with the Zono compressor and
zero-padded to its original length (the decoder stops at the end token), so the
.tbx and the XISO keep their exact sizes and only one byte range changes.
Every step is verified; the script aborts on any surprise.
"""
import struct, sys, os, hashlib, subprocess
sys.path.insert(0, os.path.dirname(__file__)); import bwj
exec(open(os.path.join(os.path.dirname(__file__), 'tbd_fields.py')).read().split("if __name__")[0])

OBJ, END, OLD, NEW = int(sys.argv[1], 16), int(sys.argv[2], 16), float(sys.argv[3]), float(sys.argv[4])
FIELDS = sys.argv[5:]
SRC = 'disc/tbd/M01/FD090663.tbx'
d = open(SRC, 'rb').read()
ch = blocks(d)[13]; st, n = ch['DATX']
size, win = struct.unpack_from('<IH', d, st)
data, used, ended = bwj.decompress(d[st + 6:st + n], 5)
assert ended and len(data) == size

patched = bytearray(data); hits = []
for fld in FIELDS:
    key = struct.pack('<II', h(fld), 4)
    p = patched.find(key, OBJ, END)
    assert p >= 0 and patched.find(key, p + 1, END) < 0, fld
    old = struct.unpack_from('<f', patched, p + 8)[0]; assert abs(old - OLD) < 1e-6, old
    struct.pack_into('<f', patched, p + 8, NEW); hits.append((fld, p + 8))
stream = bwj.compress_zono(bytes(patched), win)
assert len(stream) <= n - 6, f'does not fit: {len(stream)} > {n - 6}'
# verify: the new stream decodes to exactly the patched data
chk, _, ok = bwj.decompress(stream, 5); assert ok and chk == bytes(patched)
newchunk = struct.pack('<IH', size, win) + stream + b'\0' * (n - 6 - len(stream))
out = d[:st] + newchunk + d[st + n:]
assert len(out) == len(d)

# verify: only the DATX payload changed; every block still decodes; diff is the 2 floats
for li, (a, b) in enumerate(zip(blocks(d), blocks(out))):
    for c in a:
        sa, na = a[c]; sb, nb = b[c]
        assert (sa, na) == (sb, nb)
        if not (li == 13 and c == 'DATX'): assert d[sa:sa + na] == out[sb:sb + nb]
dec, _, _ = bwj.decompress(out[st + 6:st + n], 5)
diff = [i for i in range(len(data)) if dec[i] != data[i]]
assert all(any(o <= i < o + 4 for _, o in hits) for i in diff), diff
os.makedirs('mod', exist_ok=True)
open('mod/FD090663.tbx', 'wb').write(out)
print(f'fields: {hits}; changed decoded bytes: {len(diff)}; stream {len(stream)} of {n - 6} bytes')

# XISO offset of this file's DATX chunk (file is stored contiguously in the XISO)
xiso = open('avpe.xiso', 'rb').read()
fo = xiso.find(d[:4096]); assert fo >= 0 and xiso.find(d[:4096], fo + 1) < 0
assert xiso[fo:fo + len(d)] == d
patch_off = fo + st
open('mod/m01_patch.bin', 'wb').write(newchunk)
newx = xiso[:patch_off] + newchunk + xiso[patch_off + n:]
print('XISO patch offset', patch_off, 'length', n)
print('original XISO sha256', hashlib.sha256(xiso).hexdigest())
print('patched  XISO sha256', hashlib.sha256(newx).hexdigest())
open('mod/patch_info.txt', 'w').write(f'offset={patch_off}\nlength={n}\n'
    f'orig_sha256={hashlib.sha256(xiso).hexdigest()}\npatched_sha256={hashlib.sha256(newx).hexdigest()}\n'
    f'orig_bytes_sha256={hashlib.sha256(xiso[patch_off:patch_off+n]).hexdigest()}\n')
