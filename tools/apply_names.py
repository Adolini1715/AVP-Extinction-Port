#!/usr/bin/env python3
"""Write recovered names (tiers A-C of symbols/xbox_symbols.csv) into
xboxrecomp's tools/disasm/output/functions.json so the lifted C uses them.

Metrowerks names are shortened to C identifiers:
  DoFaceHug__10GAvPObjectFP18CFaceHuggedMessageb -> GAvPObject__DoFaceHug
  __ct__5GUnitFv -> GUnit__ctor, __dt__... -> __dtor
Overloads that would collide get a short signature suffix. The full mangled
name is kept in a 'ps2_name' field and the tier in 'name_tier'.
"""
import csv, json, re, sys, shutil, collections, zlib

SYM, FUNCS = sys.argv[1], sys.argv[2]
SPECIAL = {'__ct': 'ctor', '__dt': 'dtor', '__as': 'op_assign', '__eq': 'op_eq', '__ne': 'op_ne',
           '__nw': 'op_new', '__dl': 'op_delete', '__vc': 'op_index', '__cl': 'op_call',
           '__pl': 'op_add', '__mi': 'op_sub', '__ml': 'op_mul', '__dv': 'op_div', '__lt': 'op_lt',
           '__gt': 'op_gt', '__le': 'op_le', '__ge': 'op_ge', '__apl': 'op_add_assign',
           '__ami': 'op_sub_assign', '__amu': 'op_mul_assign', '__adv': 'op_div_assign', '__nt': 'op_not'}

def short(m):
    mm = re.match(r'^(__\w+?|\w+?)__(\d+)(\w+)$', m)
    if mm:
        meth, ln, rest = mm.group(1), int(mm.group(2)), mm.group(3)
        cls, sig = rest[:ln], rest[ln:]
        if meth in SPECIAL: meth = SPECIAL[meth]
        if re.match(r'^[A-Za-z_]\w*$', cls) and re.match(r'^[A-Za-z_]\w*$', meth):
            return f'{cls}__{meth}', sig
    mm = re.match(r'^(\w+?)__F(\w*)$', m)              # free function with signature
    # free functions get a guest_ prefix so CRT names (strcasecmp, rand...) never
    # collide with the host C library the recompiled code links against
    if mm: return 'guest_' + mm.group(1), mm.group(2)
    return 'guest_' + re.sub(r'\W', '_', m), ''

rows = [r for r in csv.DictReader(open(SYM)) if r['tier'] in ('A', 'B', 'C') and r['name']]
cand = {}
for r in rows:
    base, sig = short(r['name']); cand[int(r['xbox'], 16)] = (base, sig, r)
count = collections.Counter(b for b, _, _ in cand.values())
import os
if not os.path.exists(FUNCS + '.orig'): shutil.copy(FUNCS, FUNCS + '.orig')
funcs = json.load(open(FUNCS + '.orig'))   # always start from the toolkit's own names
RESERVED = {'xbe_entry_point'}   # names the lifter and host rely on
by_addr = {int(f['start'], 16): f for f in funcs if f['name'] not in RESERVED}
applied = missing = 0; used = set(f['name'] for f in funcs)
for a, (base, sig, r) in sorted(cand.items()):
    f = by_addr.get(a)
    if not f: missing += 1; continue
    name = base if count[base] == 1 else f'{base}_{zlib.crc32(sig.encode()) & 0xffff:04x}'
    if name in used and f['name'] != name: name = f'{name}_{a:08x}'
    used.discard(f['name']); used.add(name)
    f['name'] = name; f['ps2_name'] = r['name']; f['name_tier'] = r['tier']; applied += 1
# Xbox SDK functions identified by XbSymbolDatabase signatures (tools: xbsymbols.json)
xsym = os.path.join(os.path.dirname(os.path.abspath(SYM)), '..', 'xbsymbols.json')
xdk = 0
if os.path.exists(xsym):
    for r in json.load(open(xsym)):
        if r['kind'] != 'FUN': continue
        f = by_addr.get(r['addr'])
        if not f: continue
        name = 'xdk_' + r['name']
        if name in used and f['name'] != name: name = f"{name}_{r['addr']:08x}"
        used.discard(f['name']); used.add(name)
        f['name'] = name; f['name_tier'] = 'X'; f['xdk_lib'] = r['lib']; xdk += 1
print(f'applied {xdk} Xbox SDK names from XbSymbolDatabase')
json.dump(funcs, open(FUNCS, 'w'), indent=1)
print(f'applied {applied} names to functions.json; {missing} addresses not in the toolkit function list')
