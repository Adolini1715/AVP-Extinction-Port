#!/usr/bin/env python3
"""Class hierarchy from the Xbox build's MSVC RTTI.

For every vtable funcscan found (funcs.json 'vtables'), vtable[-1] is the
CompleteObjectLocator: {sig, offset, cdOffset, TypeDescriptor*,
ClassHierarchyDescriptor*}. The hierarchy descriptor lists the base classes
(BaseClassDescriptor: TypeDescriptor*, numContainedBases, mdisp, pdisp,
vdisp, attributes), the class itself first, then bases depth-first.
Output: symbols/classes.json  {class: {vtable, slots, bases:[...], offset}}
"""
import json, os, struct

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
vt = json.load(open(os.path.join(R, 'funcs.json')))['vtables']


def rd(va, n=4):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['vsize']:
            o = s['raw'] + va - s['va']
            if va + n > s['va'] + s['rsize']:
                return None
            return xbe[o:o + n]
    return None


def u32(va):
    b = rd(va)
    return struct.unpack('<I', b)[0] if b else None


def td_name(td):
    b = rd(td + 8, 256)
    if not b:
        return None
    n = b.split(b'\0')[0].decode('latin1')
    if n.startswith('.?AV') or n.startswith('.?AU'):
        n = n[4:]
    return n[:-2] if n.endswith('@@') else n


out = {}
for vaddr, (rname, slots) in vt.items():
    va = int(vaddr, 16)
    col = u32(va - 4)
    if not col:
        continue
    sig, off, cdoff, td, chd = struct.unpack('<5I', rd(col, 20))
    name = td_name(td)
    attrs, nbase, bca = struct.unpack('<3I', rd(chd + 4, 12))
    bases = []
    for i in range(nbase):
        bcd = u32(bca + 4 * i)
        btd, ncont, mdisp, pdisp, vdisp, battr = struct.unpack('<6I', rd(bcd, 24))
        bases.append({'name': td_name(btd), 'contained': ncont, 'mdisp': mdisp})
    entry = out.setdefault(name, {'vtables': [], 'bases': bases, 'multiple': bool(attrs & 1),
                                   'virtual_inheritance': bool(attrs & 2)})
    entry['vtables'].append({'va': vaddr, 'offset': off, 'slots': slots})

# direct parent: the first base after the class itself (bases[0] is the class)
for name, e in out.items():
    b = e['bases']
    e['parent'] = b[1]['name'] if len(b) > 1 else None
json.dump(out, open(os.path.join(R, 'symbols', 'classes.json'), 'w'), indent=1)
roots = sorted(n for n, e in out.items() if not e['parent'])
print(len(out), 'classes with RTTI;', sum(1 for e in out.values() if e['multiple']), 'with multiple inheritance;',
      len(roots), 'roots')
print('example chain:', ' -> '.join(b['name'] for b in out.get('GAvPPointer', {}).get('bases', [])))
