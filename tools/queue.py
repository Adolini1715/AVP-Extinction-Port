#!/usr/bin/env python3
"""What to write next: game functions with no hand-written body yet, ranked.

    queue.py [--all] [--max-bytes N] [-n N]

Lists functions that no src/*.cpp defines (sub_, pending_ or avpe_dec_) and
that symbols/census.json shows being called (>= 3 times in a test config,
so a sweep can verify them), smallest first, with whether they use the x87
and which configs call them most. --all includes never-called functions.
"""
import glob, json, re, sys, capstone
R = '/home/claude/avpe'
DEC = '/home/claude/avpe-decomp'
info = json.load(open(R + '/xbe_info.json')); xbe = open(R + '/disc/default.xbe', 'rb').read()
F = {int(f['start'], 16): f['size'] for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
census = json.load(open(R + '/symbols/census.json'))
import csv
smap = {int(r['xbox'], 16): r for r in csv.DictReader(open(R + '/symbols/source_map.csv'))}
game = {a for a, r in smap.items() if r['how'] not in ('runtime', 'alias')}
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
def rd(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']; return xbe[o:o + n]
    return b''
args = sys.argv[1:]
allf = '--all' in args
maxb = int(args[args.index('--max-bytes') + 1]) if '--max-bytes' in args else 10 ** 9
n = int(args[args.index('-n') + 1]) if '-n' in args else 60
written = set()
for f in glob.glob(DEC + '/src/*.cpp'):
    written |= {int(v, 16) for v in re.findall(r'void (?:sub|pending|avpe_dec)_([0-9A-F]{8})\(void\)', open(f).read())}
written |= {int(k, 16) for k in json.load(open(DEC + '/verified.json'))}
rows = []
for va in sorted(game):
    if va in written or va not in F or F[va] > maxb:
        continue
    c = census.get('0x%08X' % va, {})
    best = max(c.values()) if c else 0
    if best < 3 and not allf:
        continue
    fp = sum(1 for i in md.disasm(rd(va, F[va]), va) if i.mnemonic.startswith('f'))
    top = ' '.join('%s:%d' % kv for kv in sorted(c.items(), key=lambda kv: -kv[1])[:3])
    rows.append((fp > 0, F[va], va, fp, top))
rows.sort()
print('%d candidates' % len(rows))
for fp, size, va, nfp, top in rows[:n]:
    print('%08X %5d %-7s %s' % (va, size, 'x87:%d' % nfp if fp else '', top))
