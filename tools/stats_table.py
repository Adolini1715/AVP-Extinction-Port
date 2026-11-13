#!/usr/bin/env python3
"""Export every _G*_DefaultParams record (units, weapons, structures, vehicles)
from one mission file. All 27 mission files carry identical Params blocks.

Writes stats/all_fields.csv (lossless: source, label, field, value) and
stats/summary.md (key columns per unit and per weapon).
"""
import sys, os, io, re, csv, struct, collections
sys.path.insert(0, os.path.dirname(__file__))
import tbd_dump

SRC = 'disc/tbd/M01/FD090663.tbx'
d = open(SRC, 'rb').read()
rows = []
for li, ch in enumerate(tbd_dump.blocks(d)):
    ex = ch.get('EXTA')
    if not ex: continue
    m = re.search(rb'(Params_\w+)\.kfc', d[ex[0]:ex[0] + ex[1]])
    if not m: continue
    src = m.group(1).decode()
    buf = io.StringIO(); tbd_dump.dump(SRC, li, out=buf)
    label = None
    for line in buf.getvalue().splitlines():
        if line.startswith('['):
            mm = re.search(r'(_\w+_DefaultParams)', line)
            label = mm.group(1)[1:-len('_DefaultParams')] if mm else None
        elif label and ' = ' in line:
            f, v = line.strip().split(' = ', 1)
            rows.append((src, label, f.strip(), v.strip()))
os.makedirs('stats', exist_ok=True)
with open('stats/all_fields.csv', 'w', newline='') as fh:
    w = csv.writer(fh); w.writerow(['source', 'class', 'field', 'value']); w.writerows(rows)

by = collections.OrderedDict()
for src, cls, f, v in rows: by.setdefault((src, cls), {})[f] = v
def num(v): return v[:-1] if v.endswith('f') else v
out = ['# AvP: Extinction unit and weapon stats', '',
       'Decoded from the compiled `Params_*.kfc` blocks (identical in all 27 mission files).',
       'Complete field list: `stats/all_fields.csv`. Labels are paired by source order (see m01-level doc); every pairing was checked against the labelled parts that precede it.',
       'A blank cell means the class does not override the value: it inherits it from its parent class (for example `GMarineMedic` from `GMarine`), so blank is not zero.',
       'Units without their own `MaxHealth` (Predator Hydra, Blazer and others) inherit it and are listed in the CSV only.', '']
ucols = ['MaxHealth', 'UnitStats.Defense.Kinetic', 'UnitStats.Defense.Acid', 'UnitStats.Defense.Fire',
         'UnitStats.Defense.Plasma', 'UpgradeCost', 'UpgradeStats.HealthBonus', 'UnitStats.SkullValue', 'AlienCreditReward']
for src in ('Params_Marine', 'Params_Alien', 'Params_Predator', 'Params_NPC', 'Params_Structures'):
    units = [(c, f) for (s, c), f in by.items() if s == src and 'MaxHealth' in f]
    if not units: continue
    out += [f'## {src[7:]}', '', '| Class | ' + ' | '.join(c.split('.')[-1] for c in ucols) + ' |',
            '|---' * (len(ucols) + 1) + '|']
    for c, f in units:
        out.append(f'| {c} | ' + ' | '.join(num(f.get(k, '')) for k in ucols) + ' |')
    out.append('')
wcols = ['Damage.Raw', 'Cooldown', 'MaxSerialCooldown', 'PreferredRange', 'MaximumRange', 'MinimumRange', 'SplashRadius']
weps = [(c, f) for (s, c), f in by.items() if s == 'Params_Weapons' and any(k in f for k in ('Damage.Raw', 'Cooldown'))]
out += ['## Weapons and attacks', '', '| Class | ' + ' | '.join(wcols) + ' |', '|---' * (len(wcols) + 1) + '|']
for c, f in weps:
    out.append(f'| {c} | ' + ' | '.join(num(f.get(k, '')) for k in wcols) + ' |')
open('stats/summary.md', 'w').write('\n'.join(out) + '\n')
print(len(rows), 'field values,', len(by), 'records,', len(weps), 'weapons')
