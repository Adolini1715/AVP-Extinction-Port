#!/usr/bin/env python3
"""monkeyloop.py ROUND [--missions M01,P11,...] [--frames N] [--jobs 2]

One round of coverage-guided monkey runs (tools/monkeypad.py v2).

For each mission: a new variant (seed = hash of round and mission; options
drawn from the seed: invulnerable or not, cheat win or not) is written as
tools/replay/monkey_<M>_r<ROUND>.pad, which census.py picks up as config
K_<M>_r<ROUND>. The runs are split over --jobs parallel workers (each its
own census process, AVPE_LOCKED=1: coverage runs do not touch the build),
merged into symbols/census.json, and the round is scored against the target:
unverified game functions (minus symbols/not_functions.json) that no config
called before the round. Variants that reached nothing new have their pad
and dumps removed, so the census keeps only productive configs; the score
goes to sweep/monkeyloop.json.

Missions default to those whose earlier monkey runs reached the most target
functions, all 26 when there is no history.
"""
import json, os, random, subprocess, sys, zlib

AVPE = '/home/claude/avpe'
DEC = '/home/claude/avpe-decomp'
CEN = '/home/claude/avpe-recomp/census'
ALL = ['M01', 'M02', 'M03', 'M05', 'M06', 'M09', 'M10', 'P01', 'P02', 'P03', 'P04', 'P06', 'P08', 'P11',
       'A01', 'A02', 'A03', 'A04', 'A06', 'A07', 'A08', 'T01', 'T02', 'T03', 'T04', 'T05']
a = sys.argv[1:]
rnd = int(a[0])
opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
missions = opt('--missions', ','.join(ALL)).split(',')
frames = int(opt('--frames', 20000))
jobs = int(opt('--jobs', 2))


def census():
    return json.load(open(AVPE + '/symbols/census.json'))


def target(c):
    import csv
    sm = {int(r['xbox'], 16): r for r in csv.DictReader(open(AVPE + '/symbols/source_map.csv'))}
    nf = {int(k, 16) for k in json.load(open(AVPE + '/symbols/not_functions.json'))}
    ver = {int(k, 16) for k in json.load(open(DEC + '/verified.json'))}
    game = {x for x, r in sm.items() if r['file'] != 'runtime'} - nf - ver
    return {x for x in game if not any(n > 0 for n in c.get('0x%08X' % x, {}).values())}


SCORE_ONLY = '--score-only' in a          # score a round cut off by the time limit (its finished runs)
before = census()
if SCORE_ONLY:
    import glob as _g
    names = ['K_' + os.path.basename(p)[len('monkey_'):-4] for p in sorted(_g.glob('%s/tools/replay/monkey_*_r%d.pad' % (AVPE, rnd)))]
    names = [n for n in names if os.path.exists('%s/%s.exit' % (CEN, n))]
    # the target is what no other config called
    tgt = {x for x in target({k: {c: n for c, n in v.items() if c not in names} for k, v in before.items()})}
    missions = []
names = [] if not SCORE_ONLY else names
if not SCORE_ONLY:
    tgt = target(before)
for m in missions:
    tag = '%s_r%d' % (m, rnd)
    seed = zlib.crc32(tag.encode())
    rr = random.Random(seed)
    flags = []
    if rr.random() < 0.35: flags.append('--no-invuln')
    if rr.random() < 0.25: flags.append('--no-win')
    pad = open('%s/tools/replay/monkey_%s.pad' % (AVPE, tag), 'w')
    subprocess.run(['python3', AVPE + '/tools/monkeypad.py', m, str(frames), '--seed', str(seed)] + flags,
                   stdout=pad, check=True)
    pad.close()
    names.append('K_' + tag)
print('round %d: %d variants, target %d functions never called' % (rnd, len(names), len(tgt)), flush=True)

procs = []
for j in range(jobs if not SCORE_ONLY else 0):
    part = names[j::jobs]
    if part:
        procs.append(subprocess.Popen(['python3', AVPE + '/tools/census.py', 'run'] + part,
                                      env=dict(os.environ, AVPE_LOCKED='1'),
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True))
for p in procs:
    o, _ = p.communicate()
    print('\n'.join(l for l in o.splitlines() if l.startswith('K_')), flush=True)
subprocess.run(['python3', AVPE + '/tools/census.py', 'merge'], capture_output=True)

after = census()
hist_path = DEC + '/sweep/monkeyloop.json'
hist = json.load(open(hist_path)) if os.path.exists(hist_path) else {}
total = set()
for n in names:
    hit = {x for x in tgt if after.get('0x%08X' % x, {}).get(n, 0) > 0}
    total |= hit
    hist[n] = sorted('%08X' % x for x in hit)
    if not hit:                              # nothing new: drop the variant
        os.remove('%s/tools/replay/monkey_%s.pad' % (AVPE, n[2:]))
        for f in os.listdir(CEN):
            if f.startswith(n + '.'):
                os.remove(os.path.join(CEN, f))
    print('%-14s new %d' % (n, len(hit)), flush=True)
subprocess.run(['python3', AVPE + '/tools/census.py', 'merge'], capture_output=True)
json.dump(hist, open(hist_path, 'w'), indent=1, sort_keys=True)
print('round %d: %d target functions newly reached; %d left' % (rnd, len(total), len(tgt) - len(total)))
