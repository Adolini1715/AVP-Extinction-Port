#!/usr/bin/env python3
"""Everything known about a function, for decompiling it by hand: annotated
disassembly (callee names and conventions, strings, field names from the
editable tables for the class of `this`, globals named elsewhere), callers,
coverage, verification status, and its file.

    fninfo.py <va> [--class C]   (C: the class `this` points to; default:
                                  from the function's name)
"""
import csv, json, os, re, sys
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
db = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'xbox_symbols.csv')))}
smap = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv')))}
abi = json.load(open(os.path.join(R, 'symbols', 'abi.json')))
classes = json.load(open(os.path.join(R, 'symbols', 'classes.json')))
ed = json.load(open(os.path.join(R, 'symbols', 'editables.json')))
cov = set(json.load(open(os.path.join(R, 'symbols', 'coverage.json')))['ran'])
vj = json.load(open('/home/claude/avpe-decomp/verified.json'))
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


def rd(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]
    return b''


def sec(va):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            return s['name']
    return None


def cstr(va):
    b = rd(va, 80)
    if not b or b'\0' not in b:
        return None
    s = b[:b.index(b'\0')]
    if len(s) < 2 or not all(32 <= c < 127 for c in s):
        return None
    return s.decode()


def name(va):
    r = db.get(va)
    n = r['name'] if r and r['tier'] in ('A', 'B', 'C') else ''
    return n or 'sub_%08X' % va


def cls_of(n):
    m = re.match(r'^\w+?__(\d+)(\w+)$', n)
    return m.group(2)[:int(m.group(1))] if m else None


va = int(sys.argv[1], 16)
cls = sys.argv[sys.argv.index('--class') + 1] if '--class' in sys.argv else cls_of(name(va))
fields = {}
c = cls
while c:
    for f in ed.get(c, {}).get('fields', []):
        fields.setdefault(f['offset'], '%s::%s' % (c, f['name']))
    c = classes.get(c, {}).get('parent')
f = FUNCS.get(va)
r = smap.get(va, {})
st = 'verified' if '0x%08X' % va in vj else 'lifted'
print('%08X %s  [%s; %s; %s; ran: %s]  file %s (%s)' % (
    va, name(va), st, 'abi in: %s pops %s%s' % (','.join(abi.get('0x%08X' % va, {}).get('regs', [])) or '-',
                                                abi.get('0x%08X' % va, {}).get('pops'),
                                                ', ret eax' if abi.get('0x%08X' % va, {}).get('ret_eax') else ''),
    '%d bytes' % f['size'] if f else '?', '0x%08X' % va in cov, r.get('file'), r.get('how')))
if cls:
    print('this: %s  (fields named from the editable tables of %s and its parents)' % (cls, cls))
callers = [int(c, 16) for c in (f or {}).get('called_by', [])]
if callers:
    print('called by: ' + ', '.join('%s' % name(c) for c in callers[:12]) + (' ...' if len(callers) > 12 else ''))
print()
for i in md.disasm(rd(va, f['size'] if f else 0x200), va):
    note = []
    for m in re.finditer(r'0x([0-9a-f]{5,8})', i.op_str):
        v = int(m.group(1), 16)
        if i.mnemonic == 'call' or (i.mnemonic == 'jmp' and v in FUNCS and v != va and not (va <= v < va + f['size'])):
            a = abi.get('0x%08X' % v, {})
            note.append('%s [in: %s; pops %s]' % (name(v), ','.join(a.get('regs', [])) or '-', a.get('pops')))
        elif cstr(v):
            note.append('"%s"' % cstr(v))
        elif v in db and db[v]['tier'] in 'ABC':
            note.append(name(v))
        elif sec(v) in ('.data', '.rdata') and len(m.group(1)) >= 6:
            b = rd(v, 4)
            if b and sec(v) == '.rdata':
                fv = __import__('struct').unpack('<f', b)[0]
                if 1e-6 < abs(fv) < 1e9 or fv == 0:
                    note.append('%g' % fv)
    for m in re.finditer(r'\[(e[a-z]{2}) \+ 0x([0-9a-f]+)\]', i.op_str):
        off = int(m.group(2), 16)
        if m.group(1) != 'esp' and i.mnemonic != 'call' and off in fields:            # esp-relative: locals and arguments
            note.append(fields[off])
    print('  %08X  %-8s %-40s%s' % (i.address, i.mnemonic, i.op_str, ('  ; ' + '; '.join(note)) if note else ''))
    if i.mnemonic == 'ret' and i.address + i.size >= va + (f['size'] if f else 0):
        break
