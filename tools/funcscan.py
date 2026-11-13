#!/usr/bin/env python3
"""Rough function inventory for an XBE: recursive descent + pointer seeds.
Also resolves MSVC RTTI (vtable -> CompleteObjectLocator -> TypeDescriptor)
to attach class names to virtual methods. Outputs counts and JSON."""
import json, struct, sys, collections
import capstone

info = json.load(open(sys.argv[2]))
d = open(sys.argv[1], 'rb').read()
secs = info['sections']
entry = info['hdr']['entry_enc'] ^ 0xA8FC57AB

def sec_of(va):
    for s in secs:
        if s['va'] <= va < s['va'] + s['vsize']:
            return s
    return None
def raw(va, n):
    s = sec_of(va)
    if not s: return b''
    o = va - s['va']
    if o >= s['rsize']: return b'\0' * n
    return d[s['raw'] + o: s['raw'] + min(o + n, s['rsize'])]
def u32(va):
    b = raw(va, 4)
    return struct.unpack('<I', b)[0] if len(b) == 4 else None
code = [s for s in secs if s['flags'] & 4 and s['name'] not in ('$$XTIMAGE', '$$XSIMAGE', '.rdata', '.data')]
def is_code(va):
    s = sec_of(va)
    return s is not None and s in code

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True

funcs = set(); work = [entry]; visited_insn = set(); bad = set()
def explore(start):
    """Disassemble one function by following intra-function branches."""
    blocks = [start]; seen = set(); size = 0
    while blocks:
        pc = blocks.pop()
        while pc not in seen and is_code(pc):
            seen.add(pc)
            buf = raw(pc, 16)
            ins = next(md.disasm(buf, pc), None)
            if ins is None:
                return None
            size += ins.size
            m = ins.mnemonic
            if m == 'call':
                op = ins.operands[0]
                if op.type == capstone.x86.X86_OP_IMM and is_code(op.imm):
                    work.append(op.imm)
            elif m.startswith('j'):
                op = ins.operands[0]
                if op.type == capstone.x86.X86_OP_IMM:
                    blocks.append(op.imm)
                if m == 'jmp':
                    break
            elif m in ('ret', 'retf', 'int3', 'hlt', 'ud2'):
                break
            pc += ins.size
    return size

# seed: pointers into code from data sections (vtables, callbacks, jump tables)
ptr_seeds = set()
for s in secs:
    if s['name'] in ('.rdata', '.data', 'D3D', 'XGRPH', 'DSOUND'):
        base = s['raw']
        for o in range(0, s['rsize'] - 3, 4):
            v = struct.unpack_from('<I', d, base + o)[0]
            if is_code(v):
                ptr_seeds.add((v, s['va'] + o))

# RTTI: find vtables. vtable[-1] = COL; COL+12 = TypeDescriptor; TD+8 = name
rtti_vtables = {}
for v, where in list(ptr_seeds):
    pass
for s in secs:
    if s['name'] != '.rdata': continue
    for o in range(4, s['rsize'] - 3, 4):
        va = s['va'] + o
        col = u32(va - 4)
        if col is None or not sec_of(col) or sec_of(col)['name'] != '.rdata': continue
        sig = u32(col)
        td = u32(col + 12)
        if sig != 0 or td is None or not sec_of(td): continue
        name = raw(td + 8, 128).split(b'\0')[0]
        if not name.startswith(b'.?A'): continue
        first = u32(va)
        if first is None or not is_code(first): continue
        meths = []
        p = va
        while True:
            f = u32(p)
            if f is None or not is_code(f): break
            if p != va and u32(p - 4) is not None and p - 4 != va - 4:
                # stop if another vtable's COL sits here
                c2 = u32(p - 4)
            meths.append(f); p += 4
            if len(meths) > 400: break
        rtti_vtables[va] = (name.decode('latin1'), meths)

seed_count = 0
for v, _ in ptr_seeds:
    work.append(v); seed_count += 1

sizes = {}
while work:
    f = work.pop()
    if f in funcs or f in bad: continue
    sz = explore(f)
    if sz is None or sz == 0:
        bad.add(f); continue
    funcs.add(f); sizes[f] = sz

by_sec = collections.Counter(sec_of(f)['name'] for f in funcs)
bytes_sec = collections.Counter()
for f, sz in sizes.items(): bytes_sec[sec_of(f)['name']] += sz
virt = set(); named_methods = {}
for va, (name, meths) in rtti_vtables.items():
    cls = name[4:-2] if name.endswith('@@') else name
    for i, m in enumerate(meths):
        virt.add(m)
        named_methods.setdefault(m, '%s::vf%d' % (cls, i))
print('entry 0x%08X' % entry)
print('functions found: %d' % len(funcs))
for k, v in sorted(by_sec.items(), key=lambda x: -x[1]):
    print('  %-8s %6d functions  %8d bytes reached' % (k, v, bytes_sec[k]))
print('RTTI vtables: %d, distinct virtual functions: %d' % (len(rtti_vtables), len(virt)))
cls_g = sum(1 for n, _ in rtti_vtables.values() if n.startswith('.?AVG'))
cls_c = sum(1 for n, _ in rtti_vtables.values() if n.startswith('.?AVC'))
print('  vtables for G* game classes: %d, C* engine classes: %d' % (cls_g, cls_c))
json.dump(dict(funcs=sorted(funcs), sizes={hex(k): v for k, v in sizes.items()},
               vtables={hex(k): [n, [hex(m) for m in ms]] for k, (n, ms) in rtti_vtables.items()}),
          open(sys.argv[3], 'w'))
