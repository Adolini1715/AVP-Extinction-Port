#!/usr/bin/env python3
"""Which game functions have run, over every coverage run so far.

Input: coverage dumps from the instrumented build (recomp/build_coverage.sh,
AVPE_COVERAGE=<prefix>): each dump is the cumulative set of translated host
functions that had run at a checkpoint (before each level load, N frames into
a mission, on request). Give dump files or run prefixes (all dumps of a run).
For a run with a log (run_<tag>.log next to cov_<tag>.*), each checkpoint is
labelled with the level that was running before it, so the table shows what
each level added.

Output: symbols/coverage.json ('ran': every game function seen, 'first_seen':
function -> checkpoint label) and a per-file table. Game code only: runtime
libraries and in-body aliases (symbols/source_map.csv) are excluded.

    coverage.py <prefix or dump> [...]
"""
import csv, glob, json, os, re, sys, collections
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
FUNCS = '/home/claude/xboxrecomp/tools/disasm/output/functions.json'
OUT = '/home/claude/avpe-recomp'
by_name = {}
size = {}
for f in json.load(open(FUNCS)):
    by_name[f['name']] = int(f['start'], 16)
    size[int(f['start'], 16)] = f['size']
smap = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv')))}
game = {a for a, r in smap.items() if r['how'] not in ('runtime', 'alias')}


def load(path):
    out = set()
    for line in open(path):
        n = line.strip()
        if n.endswith('_gen'):
            n = n[:-4]
        a = by_name.get(n)
        if a is None and n.startswith('sub_'):
            try:
                a = int(n[4:12], 16)
            except ValueError:
                a = None
        if a is not None and a in game:
            out.add(a)
    return out


def dumps_of(spec):
    if os.path.isfile(spec):
        return [spec]
    fs = glob.glob(spec + '.*')
    def key(p):
        m = re.search(r'before_load(\d+)$', p)
        return (0, int(m.group(1))) if m else (1, os.path.getmtime(p))
    return sorted(fs, key=key)


def labels_for(prefix):
    """level number -> level file, from the run's log when there is one."""
    base = os.path.basename(prefix)
    tag = base[4:] if base.startswith('cov_') else base
    log = os.path.join(os.path.dirname(prefix) or OUT, 'run_%s.log' % tag)
    loads = {}
    if os.path.exists(log):
        for m in re.finditer(r"\[LEVEL\] load #(\d+) '([^']+)'", open(log, errors='ignore').read()):
            loads[int(m.group(1))] = m.group(2)
    return loads


ran = set(); first = {}
rows = []
for spec in sys.argv[1:]:
    loads = labels_for(spec)
    prev = set()
    for d in dumps_of(spec):
        s = load(d)
        m = re.search(r'before_load(\d+)$', d)
        k = int(m.group(1)) if m else None
        if k == 1:
            label = 'boot'
        elif k and k - 1 in loads:
            label = loads[k - 1]
        else:
            label = d.rsplit('.', 1)[-1]
        label = '%s: %s' % (os.path.basename(spec), label)
        new = s - ran
        for a in new:
            first['0x%08X' % a] = label
        rows.append((label, len(s - prev), len(new)))
        ran |= s; prev = s

tot = len(game); tb = sum(size.get(a, 0) for a in game)
print('game functions: %d (%d KB)' % (tot, tb // 1024))
print('ran at least once: %d (%.1f%%), %.1f%% of game code bytes' % (
    len(ran), 100 * len(ran) / tot, 100 * sum(size.get(a, 0) for a in ran) / tb))
print('\ncheckpoint (level running before it)            new in run  new overall')
for label, n_run, n_all in rows:
    if n_all or n_run:
        print('  %-46s %6d %10d' % (label[:46], n_run, n_all))
per = collections.defaultdict(lambda: [0, 0])
for a in game:
    p = per[smap[a]['file']]; p[0] += 1; p[1] += a in ran
json.dump({'ran': sorted('0x%08X' % a for a in ran), 'first_seen': first},
          open(os.path.join(R, 'symbols', 'coverage.json'), 'w'), indent=0)
known = {r['file'] for r in smap.values() if r['how'] in ('local', 'class', 'sinit')}
fr = sorted(((p[1] / p[0], f, p) for f, p in per.items() if f in known and p[0] >= 10), reverse=True)
print('\nZono-named files (>= 10 functions) by share that ran:')
for frac, f, p in fr[:20]:
    print('  %-26s %4d/%-4d (%3.0f%%)' % (f, p[1], p[0], 100 * frac))
print('  ...')
for frac, f, p in fr[-10:]:
    print('  %-26s %4d/%-4d (%3.0f%%)' % (f, p[1], p[0], 100 * frac))
