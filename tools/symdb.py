#!/usr/bin/env python3
"""Confidence-graded symbol database: Xbox function -> PS2 (real) name.

Evidence kinds, each independent:
  vtable    the Xbox function sits in the same vtable slot of the same class
            as the PS2 function (classes verified to have identical slot counts)
  callgraph a named Xbox function calls it at the same position (aligned on
            already-named callees) as the PS2 counterpart calls the PS2 function
  string    both functions reference the same string literal(s), and that
            string set is unique to them on each side
  registry  the class-registration initializer passes this function as the
            Create factory together with the class-name string
  callees   an unnamed function's ordered list of already-named callees matches
            exactly one PS2 function
  callseq   after naming, the two functions' complete call lists agree
            (every resolvable position names the same callee, >=2 calls)

Tiers:
  A  vtable or registry evidence (exact structural identity)
  B  two or more independent kinds among callgraph / string / callseq
  C  exactly one of callgraph / string
  D  hypothesis: conflicting or unconfirmed candidate, recorded but not applied
"""
import json, struct, subprocess, sys, collections, difflib
import capstone

ROOT = sys.argv[1] if len(sys.argv) > 1 else '.'
INLINE_MAX = int(sys.argv[2]) if len(sys.argv) > 2 else 48
XBE = f'{ROOT}/disc/default.xbe'; ELF = f'{ROOT}/ps2/SLUS_201.47'

# ---------------------------------------------------------------- Xbox side
info = json.load(open(f'{ROOT}/xbe_info.json'))
xd = open(XBE, 'rb').read()
secs = info['sections']
def xsec(va):
    for s in secs:
        if s['va'] <= va < s['va'] + s['vsize']: return s
def xraw(va, n):
    s = xsec(va)
    if not s: return b''
    o = va - s['va']
    if o >= s['rsize']: return b'\0' * n
    return xd[s['raw'] + o: s['raw'] + min(o + n, s['rsize'])]
def is_xcode(va):
    s = xsec(va); return bool(s) and s['name'] == '.text'
def cstr_at(raw_fn, va):
    b = raw_fn(va, 256)
    if not b or b'\0' not in b: return None
    s = b[:b.index(b'\0')]
    if len(s) < 4 or any(c < 0x20 or c > 0x7e for c in s): return None
    return s.decode('latin1')
def xstr(va):
    s = xsec(va)
    if not s or s['name'] not in ('.rdata', '.data'): return None
    return cstr_at(xraw, va)

md86 = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32); md86.detail = True
funcs = json.load(open(f'{ROOT}/funcs.json'))
xfuncs = [f for f in funcs['funcs'] if is_xcode(f)]
# Only confirmed starts may end another function's body: direct call targets,
# vtable entries and the entry point. Data-pointer-only hits are often jump-table
# targets inside a function and must not truncate it.
_t = [s for s in secs if s['name'] == '.text'][0]
_buf = xd[_t['raw']:_t['raw'] + _t['rsize']]
_calls = set()
for _i in range(len(_buf) - 5):
    if _buf[_i] == 0xE8:
        _calls.add((_t['va'] + _i + 5 + struct.unpack_from('<i', _buf, _i + 1)[0]) & 0xffffffff)
_virt = set(int(m, 16) for _, (n, ms) in funcs['vtables'].items() for m in ms)
_entry = info['hdr']['entry_enc'] ^ 0xA8FC57AB
xfset = set(f for f in xfuncs if f in _calls or f in _virt or f == _entry)
xfuncs = sorted(set(f for f in funcs['funcs'] if is_xcode(f)))

def xbody(start):
    """Instructions of one function (follow branches, stop at ret/jmp)."""
    ins_at = {}; todo = [start]
    while todo:
        pc = todo.pop()
        while pc not in ins_at and is_xcode(pc):
            ins = next(md86.disasm(xraw(pc, 16), pc), None)
            if ins is None: break
            ins_at[pc] = ins
            m = ins.mnemonic
            if m.startswith('j'):
                op = ins.operands[0]
                if op.type == capstone.x86.X86_OP_IMM:
                    if m == 'jmp' and op.imm in xfset and op.imm != start:
                        break                               # tail call, treat as exit
                    todo.append(op.imm)
                if m == 'jmp': break
            if m in ('ret', 'int3', 'hlt'): break
            pc += ins.size
    return [ins_at[a] for a in sorted(ins_at)]

def xfeatures(start):
    calls, strs = [], []
    for ins in xbody(start):
        if ins.mnemonic == 'call':
            op = ins.operands[0]
            if op.type == capstone.x86.X86_OP_IMM and is_xcode(op.imm): calls.append(op.imm)
        for op in ins.operands:
            v = op.imm if op.type == capstone.x86.X86_OP_IMM else (op.mem.disp if op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 else None)
            if v is not None:
                s = xstr(v & 0xffffffff)
                if s: strs.append(s)
    return calls, strs

# ---------------------------------------------------------------- PS2 side
elf = open(ELF, 'rb').read()
def praw(va, n):
    o = 0x80 + va - 0x100000
    if o < 0x80 or o >= 0x80 + 0x266d00: return b''
    return elf[o:o + n]
psym = {}; psize = {}
for line in subprocess.run(['readelf', '-s', '-W', ELF], capture_output=True, text=True).stdout.splitlines():
    p = line.split()
    if len(p) < 8 or not p[0][:-1].isdigit(): continue
    # Weak symbols (the inline methods headers define: CMover::Moving,
    # ::Stationary, ...) print their binding as "<processor specific>: 13",
    # which shifts the columns: the name is always the last field.
    if p[3] == 'FUNC' and int(p[2], 0) > 0 and p[-1] not in ('DEFAULT', 'UND'):
        a = int(p[1], 16); psym.setdefault(a, p[-1]); psize[a] = int(p[2], 0)
pname2addr = {}
for a, n in psym.items(): pname2addr.setdefault(n, a)
mdm = capstone.Cs(capstone.CS_ARCH_MIPS, capstone.CS_MODE_MIPS32 + capstone.CS_MODE_LITTLE_ENDIAN)
def pfeatures(a):
    calls, strs = [], []
    code = praw(a, psize[a]); hi = {}
    for i in range(0, len(code) - 3, 4):
        ins = next(mdm.disasm(code[i:i + 4], a + i), None)
        if ins is None: continue
        m, ops = ins.mnemonic, ins.op_str.replace('$', '').split(', ')
        if m == 'jal':
            t = int(ops[0], 16)
            if t in psym: calls.append(t)
        elif m == 'lui':
            hi[ops[0]] = int(ops[1], 0) << 16
        elif m in ('addiu', 'ori') and len(ops) == 3 and ops[1] in hi:
            lo = int(ops[2], 0)
            v = (hi[ops[1]] + (lo if m == 'addiu' else lo & 0xffff)) & 0xffffffff
            s = cstr_at(praw, v)
            if s: strs.append(s)
    return calls, strs

print('extracting features...', file=sys.stderr)
XF = {f: xfeatures(f) for f in xfuncs}
PF = {a: pfeatures(a) for a in psym}

# ---------------------------------------------------------------- database
db = {}          # xaddr -> dict(name, ps2, evidence:set)
def add(x, pn, kind):
    e = db.setdefault(x, dict(name=pn, ps2=pname2addr.get(pn), evidence=set(), alts=set()))
    if e['name'] != pn:
        e['alts'].add(pn); return False
    e['evidence'].add(kind); return True

vt = json.load(open(f'{ROOT}/vtmatch.json'))
import os, zlib
HOLDOUT = float(os.environ.get('HOLDOUT', '0'))
held = {}
for xa, pn in vt['names'].items():
    if pn == 'DEFAULT': continue
    if HOLDOUT and (zlib.crc32(xa.encode()) % 1000) < HOLDOUT * 1000:
        held[int(xa, 16)] = pn; continue          # hidden ground truth for the self-test
    add(int(xa, 16), pn, 'vtable')
folded = {int(k, 16): v for k, v in vt['conflicts'].items()}

# --- class registry: static initializers do
#       push parent; push flags; push <Create fn>; push "<ClassName>"; call Register
#     so the pushed code pointer is Create__<len><ClassName>F...
create_by_cls = {}
for a, n in psym.items():
    if n.startswith('Create__'):
        r = n[8:]; i = 0
        while i < len(r) and r[i].isdigit(): i += 1
        if i: create_by_cls[r[i:i + int(r[:i])]] = n
registry_hits = 0
_tb = xd[_t['raw']:_t['raw'] + _t['rsize']]
for _o in range(len(_tb) - 15):
    # 68 <code> 68 <str> E8 <rel>
    if _tb[_o] == 0x68 and _tb[_o + 5] == 0x68 and _tb[_o + 10] == 0xE8:
        fn, sp = struct.unpack_from('<II', _tb, _o + 1)[0], struct.unpack_from('<I', _tb, _o + 6)[0]
        cls = xstr(sp)
        if cls and is_xcode(fn) and cls in create_by_cls:
            add(fn, create_by_cls[cls], 'registry'); registry_hits += 1
print('registry: %d Create functions named' % registry_hits, file=sys.stderr)

taken = collections.defaultdict(set)   # ps2 name -> xaddrs
def rebuild_taken():
    taken.clear()
    for x, e in db.items(): taken[e['name']].add(x)

def align_pairs(xc, pc):
    """Pair Xbox callees with PS2 callees, aligned on already-named callees."""
    xt = [db[c]['name'] if c in db else ('?', i) for i, c in enumerate(xc)]
    named = {e['name'] for e in db.values()}
    # PS2 helpers small enough that MSVC likely inlined them, and not yet seen
    # as a real Xbox function, are dropped so the two lists can line up.
    pc = [c for c in pc if psize[c] > INLINE_MAX or psym[c] in named]
    pt = [psym[c] for c in pc]
    sm = difflib.SequenceMatcher(None, xt, pt, autojunk=False)
    out = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'replace' and (i2 - i1) == (j2 - j1):
            for k in range(i2 - i1):
                if isinstance(xt[i1 + k], tuple): out.append((xc[i1 + k], pt[j1 + k]))
    return out

# --- string evidence (unique string sets)
def strkey(ss): return tuple(sorted(set(ss)))
xs_by = collections.defaultdict(list); ps_by = collections.defaultdict(list)
for f, (_, ss) in XF.items():
    if ss: xs_by[strkey(ss)].append(f)
for a, (_, ss) in PF.items():
    if ss: ps_by[strkey(ss)].append(a)
string_pairs = {}
for k, xl in xs_by.items():
    if len(xl) == 1 and len(ps_by.get(k, [])) == 1:
        # an Xbox static initialiser (above the game code) sharing one string
        # with a PS2 method is a coincidence: 0x16C190 registers the class
        # whose name string is "Pause" and was named Input_Pause this way
        if xl[0] >= 0x141000 and not psym[ps_by[k][0]].startswith('__sinit'):
            continue
        string_pairs[xl[0]] = psym[ps_by[k][0]]

# --- iterate call-graph propagation to fixpoint, seeding strings each round
for rnd in range(30):
    votes = collections.defaultdict(collections.Counter)
    for x in list(db):
        e = db[x]
        if e['ps2'] is None or x in folded: continue
        for xc, pn in align_pairs(XF.get(x, ([], []))[0], PF.get(e['ps2'], ([], []))[0]):
            votes[xc][pn] += 1
    rebuild_taken(); added = 0
    for xc, cnt in votes.items():
        if xc in folded: continue
        (pn, n), = cnt.most_common(1)
        if len(cnt) > 1:        # disagreement: record as hypothesis only
            for alt in cnt:
                if alt != pn: db.setdefault(xc, dict(name=pn, ps2=pname2addr.get(pn), evidence=set(), alts=set()))['alts'].add(alt)
            continue
        if taken.get(pn) and xc not in taken[pn]: continue   # one-to-one
        if xc in db and db[xc]['name'] != pn: db[xc]['alts'].add(pn); continue
        if xc not in db or 'callgraph' not in db[xc]['evidence']: added += 1
        add(xc, pn, 'callgraph')
    # --- callee signature: an unnamed function identified by the ordered list
    # of already-named functions it calls (unique on both sides)
    rebuild_taken(); named = {e['name'] for e in db.values()}
    xsig = collections.defaultdict(list); psig = collections.defaultdict(list)
    for f in xfuncs:
        if f in db or f in folded: continue
        k = tuple(db[c]['name'] for c in XF[f][0] if c in db)
        if len(k) >= 2: xsig[('seq',) + k].append(f); xsig[('set',) + tuple(sorted(set(k)))].append(f)
    for a, (pc, _) in PF.items():
        if psym[a] in taken: continue
        k = tuple(psym[c] for c in pc if psym[c] in named)
        if len(k) >= 2: psig[('seq',) + k].append(a); psig[('set',) + tuple(sorted(set(k)))].append(a)
    for k, xl in xsig.items():
        pl = psig.get(k, [])
        if len(xl) == 1 and len(pl) == 1:
            pn = psym[pl[0]]; xc = xl[0]
            if taken.get(pn) and xc not in taken[pn]: continue
            if xc in db and db[xc]['name'] != pn: db[xc]['alts'].add(pn); continue
            if xc not in db: added += 1
            add(xc, pn, 'callees'); taken[pn].add(xc)
    for xc, pn in string_pairs.items():
        rebuild_taken()
        if taken.get(pn) and xc not in taken[pn]: continue
        if xc not in db: added += 1
        add(xc, pn, 'string')
    print(f'round {rnd}: +{added}, total {len(db)}', file=sys.stderr)
    if not added: break

# --- callseq corroboration + tiers
for x, e in db.items():
    if e['ps2'] is None: continue
    xc, pc = XF.get(x, ([], []))[0], PF.get(e['ps2'], ([], []))[0]
    if len(xc) == len(pc) >= 2:
        known = [(db[c]['name'], psym[p]) for c, p in zip(xc, pc) if c in db]
        if known and all(a == b for a, b in known) and len(known) >= 2:
            e['evidence'].add('callseq')
    if x in string_pairs and string_pairs[x] == e['name']: e['evidence'].add('string')
def tier(e):
    ev = e['evidence']
    if ev & {'vtable', 'registry'}: return 'A'
    if len(ev & {'callgraph', 'callees', 'string', 'callseq'}) >= 2: return 'B'
    if ev & {'callgraph', 'callees', 'string'}: return 'C'
    return 'D'
# --- ambiguity pass: a PS2 name may only stay on several Xbox addresses when all
# of them are tier A (the same source function emitted twice). Otherwise every
# non-A claimant is demoted to D, so no ambiguous name is ever applied.
for x in [x for x, e in db.items() if e['name'] == 'DEFAULT' or psym.get(e['ps2'] or 0) == 'DEFAULT']:
    del db[x]
claim = collections.defaultdict(list)
for x, e in db.items():
    if x not in folded: claim[e['name']].append(x)
demoted = set()
for pn, xs in claim.items():
    if len(xs) > 1 and not all(tier(db[x]) == 'A' for x in xs):
        for x in xs:
            if tier(db[x]) != 'A': demoted.add(x)
print('ambiguity pass: %d names demoted to D' % len(demoted), file=sys.stderr)
rows = []
for x in sorted(db):
    e = db[x]; t = 'D' if x in demoted else tier(e)
    if x in folded: t = 'F'
    rows.append(dict(xbox=f'0x{x:08X}', tier=t, name=e['name'],
                     ps2=f'0x{e["ps2"]:08X}' if e['ps2'] else '',
                     evidence='+'.join(sorted(e['evidence'])),
                     alternatives='|'.join(sorted(e['alts']))[:300]))
for x, names in folded.items():
    if x not in db:
        rows.append(dict(xbox=f'0x{x:08X}', tier='F', name='', ps2='', evidence='folded', alternatives='|'.join(names)[:300]))
import csv
with open(f'{ROOT}/symbols/xbox_symbols.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
c = collections.Counter(r['tier'] for r in rows)
if held:
    rec = {x: db[x] for x in held if x in db and x not in folded}
    by = collections.defaultdict(lambda: [0, 0])
    for x, e in rec.items():
        t = tier(e); by[t][0] += 1; by[t][1] += (e['name'] == held[x])
        if e['name'] != held[x]: print('  MISS 0x%08X truth %s got %s via %s' % (x, held[x], e['name'], sorted(e['evidence'])))
    print('HOLDOUT self-test: %d hidden vtable names, %d re-derived without vtable evidence' % (len(held), len(rec)))
    for t in sorted(by): print('  tier %s: %d re-derived, %d correct (%.1f%%)' % (t, by[t][0], by[t][1], 100 * by[t][1] / by[t][0]))
    sys.exit(0)
print('xbox .text functions:', len(xfuncs))
print('tiers:', dict(sorted(c.items())))
print('conflicting candidates recorded (alternatives column non-empty):', sum(1 for r in rows if r['alternatives'] and r['tier'] != 'F'))
