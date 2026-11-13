#!/usr/bin/env python3
"""deadcode.py [--json OUT]: which game functions can run at all.

Static reachability over default.xbe. Roots: the XBE entry point, and every
function whose address is stored anywhere in the image's data (vtables,
function tables, the class registry). From the roots, follow direct calls and
jumps and every function address used as an immediate operand (callbacks
passed in code: push offset f / mov [x], offset f), to a fixed point.

Each game function (source_map, minus "runtime") with no verified check is
then labelled:
  unreferenced  nothing calls it, jumps to it or stores its address: dead code,
                or not a function at all (a false start inside data or padding)
  vtable-only   reachable only as a virtual method of classes whose every
                vtable sits in data; listed with its classes, so the level
                census can say whether any level spawns them
  reachable     reached from the entry point through calls, jumps or
                addresses taken in reachable code
False starts: a start that is not the target of any call or jump, not stored
anywhere, and whose first instruction does not decode or sits in a run of
zero/int3 bytes is flagged "not code".
"""
import collections, csv, json, os, struct, sys
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
f = json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
by = {int(x['start'], 16): x for x in f}
starts = set(by)
sm = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv')))}
game = {a for a in by if a in sm and sm[a]['file'] != 'runtime'}
ver = {int(k, 16) for k in json.load(open('/home/claude/avpe-decomp/verified.json'))}
classes = json.load(open(os.path.join(R, 'symbols', 'classes.json')))

entry = info['hdr']['entry_enc'] ^ 0xA8FC57AB
secs = info['sections']
CODE = {'.text', 'D3D', 'XGRPH', 'DSOUND', 'WMADEC', 'XMV', 'XPP', 'DOLBY'}


def rd(va, n):
    for s in secs:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]
    return b''


# addresses stored in data sections (4-byte aligned words equal to a start)
in_data = collections.defaultdict(set)      # function -> data addresses holding it
for s in secs:
    if s['name'] in CODE:
        continue
    d = xbe[s['raw']:s['raw'] + s['rsize']]
    for i in range(0, len(d) - 3, 4):
        v = struct.unpack_from('<I', d, i)[0]
        if v in starts:
            in_data[v].add(s['va'] + i)

# vtables: data addresses that are vtable slots, by class
vslot = {}
for cname, c in classes.items():
    for vt in c.get('vtables', []):
        base = int(vt['va'], 16)
        for k, sl in enumerate(vt['slots']):
            vslot[base + 4 * k] = cname

# immediates in code: per function, the function addresses it takes
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True
imm_refs = collections.defaultdict(set)
jumps = collections.defaultdict(set)
vt_bases = {}
for cname, c in classes.items():
    for vt in c.get('vtables', []):
        vt_bases[int(vt['va'], 16)] = (cname, [int(x, 16) for x in vt['slots']])
vt_refs = collections.defaultdict(set)      # function -> vtables whose address it uses (constructors)
for a, x in by.items():
    e = int(x['end'], 16)
    for ins in md.disasm(rd(a, e - a), a):
        for op in ins.operands:
            v = None
            if op.type == capstone.x86.X86_OP_IMM:
                v = op.imm & 0xFFFFFFFF
            elif op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 and op.mem.index == 0:
                v = op.mem.disp & 0xFFFFFFFF
            if v in vt_bases:
                vt_refs[a].add(v)
            if v in starts and v != a:
                if ins.mnemonic in ('call', 'jmp') or ins.mnemonic.startswith('j'):
                    jumps[a].add(v)
                else:
                    imm_refs[a].add(v)

edges = collections.defaultdict(set)
for a, x in by.items():
    edges[a] |= {int(c, 16) for c in x['calls_to']} | jumps[a] | imm_refs[a]

# reachability from the entry point plus non-vtable data references
roots = {entry} if entry in starts else set()
vtable_only = set()
for fn, where in in_data.items():
    if all(w in vslot for w in where):
        vtable_only.add(fn)
    else:
        roots.add(fn)
# fixed point: a vtable is live once reachable code stores its address (its
# constructor ran), and then its slots are reachable
reach, live_vt = set(), set()
st = list(roots)
while st:
    a = st.pop()
    if a in reach or a not in by:
        continue
    reach.add(a)
    st += list(edges[a])
    for v in vt_refs[a]:
        if v not in live_vt:
            live_vt.add(v)
            st += vt_bases[v][1]
dormant_cls = sorted({vt_bases[v][0] for v in vt_bases if v not in live_vt})
vreach = set()
st = [v for v in vtable_only if v not in reach]
while st:
    a = st.pop()
    if a in reach or a in vreach or a not in by:
        continue
    vreach.add(a)
    st += list(edges[a])

referenced = set()
for a in by:
    referenced |= edges[a]
referenced |= set(in_data)


def looks_like_code(a):
    b = rd(a, 8)
    if not b or b[:4] in (b'\0\0\0\0', b'\xcc\xcc\xcc\xcc'):
        return False
    return next(md.disasm(b, a), None) is not None


import bisect
_st = sorted(by)
def container(a):
    for p in _st[max(0, bisect.bisect_left(_st, a) - 40):bisect.bisect_left(_st, a)]:
        if p < a < int(by[p]['end'], 16):
            return p
    return None

def re_lab(l):
    return 'not a function (inside another)' if l.startswith('not a function') else l

out = {}
for a in sorted(game - ver):
    cont = container(a)
    if cont is not None:
        lab = 'not a function: inside %08X%s' % (cont, ' (verified)' if cont in ver else '')
    elif a in reach:
        lab = 'reachable'
    elif a in vreach:
        lab = 'virtual method of classes never constructed'
    elif a not in referenced:
        lab = 'unreferenced' + ('' if looks_like_code(a) else ' (not code)')
    else:
        lab = 'referenced only by unreachable code'
    cls = sorted({vslot[w] for w in in_data.get(a, ()) if w in vslot})
    out['%08X' % a] = {'label': lab, 'name': by[a]['name'], 'classes': cls}

cnt = collections.Counter(re_lab(v['label']) for v in out.values())
print('entry point %08X; reachable %d, vtable-only %d of %d functions' % (entry, len(reach), len(vreach), len(by)))
print('live vtables %d of %d; classes never constructed: %d' % (len(live_vt), len(vt_bases), len(dormant_cls)))
print('unverified game functions:', len(out))
for k, n in cnt.most_common():
    print('  %-40s %d' % (k, n))
nf = {k: v['label'] for k, v in out.items() if v['label'].startswith('not a function')}
json.dump(nf, open(os.path.join(R, 'symbols', 'not_functions.json'), 'w'), indent=0, sort_keys=True)
if '--json' in sys.argv:
    json.dump(out, open(sys.argv[sys.argv.index('--json') + 1], 'w'), indent=1)
