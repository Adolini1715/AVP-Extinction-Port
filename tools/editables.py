#!/usr/bin/env python3
"""Field names, offsets and types of every class, from its SpecifyEditable.

Zono's editor describes each class's editable members to the level editor in
SpecifyEditable: for every field, the CRC of its name ("MoverData.MaxRunSpeed"),
its offset in the object, its size and a type name ("float"), one 12-byte
EditableInfo each:
    +0 name CRC   +4 size (word)   +6 type index (set by 0xD6B20)
    +7 flags      +8 offset
The names are Zono's own member names (dotted for members of a struct
member), so they name the decompiled classes' fields.

Reads each SpecifyEditable linearly: a field is the last string whose CRC was
computed, the last offset and size stored, and the type string pushed for
the type registration call (0xD6B20). Output: symbols/editables.json
{class: [{name, offset, size, type, flags}]} and a summary.

    editables.py [Class ...]     (print those classes' fields)
"""
import csv, json, os, re, sys
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
db = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'xbox_symbols.csv')))}
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
TYPE_REG = 0xD6B20


def rd(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]
    return b''


def cstr(va):
    b = rd(va, 128)
    return b[:b.index(b'\0')].decode('latin1') if b and b'\0' in b else None


def imm(op):
    m = re.search(r'0x([0-9a-f]+)|\b(\d+)$', op)
    return int(m.group(1), 16) if m and m.group(1) else (int(m.group(2)) if m else None)


def fields(va):
    f = FUNCS.get(va)
    if not f:
        return []
    ins = list(md.disasm(rd(va, f['size']), va))
    out = []
    name = off = size = flags = None
    pushed = None
    for k, i in enumerate(ins):
        op = i.op_str
        if i.mnemonic == 'mov' and re.fullmatch(r'edx, 0x[0-9a-f]+', op) and k + 1 < len(ins) \
                and re.fullmatch(r'cl, 0x[0-9a-f]+', ins[k + 1].op_str):
            s = cstr(imm(op))
            if s:
                name = s; off = size = flags = None
        elif i.mnemonic == 'mov' and re.fullmatch(r'dword ptr \[e[a-z]{2} \+ 8\], (0x[0-9a-f]+|\d+)', op):
            off = imm(op)
        elif i.mnemonic == 'mov' and re.fullmatch(r'word ptr \[e[a-z]{2} \+ 4\], (0x[0-9a-f]+|\d+)', op):
            size = imm(op)
        elif i.mnemonic == 'mov' and re.fullmatch(r'byte ptr \[e[a-z]{2} \+ 7\], (0x[0-9a-f]+|\d+)', op):
            flags = imm(op)
        elif i.mnemonic == 'push' and re.fullmatch(r'0x[0-9a-f]+', op):
            pushed = imm(op)
        elif i.mnemonic == 'call' and imm(op) == TYPE_REG:
            if name is not None and off is not None:
                out.append({'name': name, 'offset': off, 'size': size, 'type': cstr(pushed) if pushed else None,
                            'flags': flags})
            name = off = size = flags = None
    return out


def cls_of(n):
    m = re.match(r'^\w+?__(\d+)(\w+)$', n)
    return m.group(2)[:int(m.group(1))] if m else None


res = {}
for va, r in sorted(db.items()):
    if r['tier'] in ('A', 'B', 'C') and r['name'].startswith('SpecifyEditable__'):
        c = cls_of(r['name'])
        fs = fields(va)
        if c and fs:
            res[c] = {'function': '0x%08X' % va, 'fields': fs}
json.dump(res, open(os.path.join(R, 'symbols', 'editables.json'), 'w'), indent=1)
print('%d classes, %d fields' % (len(res), sum(len(v['fields']) for v in res.values())))
for c in sys.argv[1:]:
    print('%s (%s):' % (c, res.get(c, {}).get('function')))
    for f in res.get(c, {}).get('fields', []):
        print('  +0x%03X  %-6s %-2s  %s' % (f['offset'], f['type'], f['size'], f['name']))
