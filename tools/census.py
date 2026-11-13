#!/usr/bin/env python3
"""Mission census: which game functions each test configuration calls, and how often.

    census.py run [CONFIG ...]     run the coverage build (avpe_recomp_cov,
                                   recomp/build_coverage.sh) in each config and
                                   keep its call counts in avpe-recomp/census/
    census.py merge                write symbols/census.json from those runs
    census.py who VA [VA ...]      the configs that call each function, by count

Configs are the replay gates (pipe.sh replayall: same mission, pad and frame
count) plus 'attract' (front end left idle, idle.pad). Counts are entries of
the translated function, so a function already replaced by decompiled C++
reads 0 (its body is not instrumented): the census is for pending and
unwritten functions. sweep.py uses census.json to pick, per function, the
missions where it is called at least 3 times (the natural call it checks).

census.json: {"0x000E0D00": {"M05": 812, "attract": 40, ...}, ...}
"""
import glob, json, os, subprocess, sys

AVPE = '/home/claude/avpe'
OUT = '/home/claude/avpe-recomp/census'
BIN = '/home/claude/avpe-recomp/avpe_recomp_cov'
FUNCS = '/home/claude/xboxrecomp/tools/disasm/output/functions.json'
P = AVPE + '/tools/replay'
# name: (mission or None for the front end, pad, frames)
CONFIGS = {
    'attract': (None, 'idle', 9000),
    'T01': ('T01', 't01', 6000), 'T02': ('T02', 't02', 6000), 'T03': ('T03', 't03', 9000),
    'T04': ('T04', 't04', 6000), 'T05': ('T05', 't05', 6000),
    'PAUSE': ('M01', 'pause', 4200), 'M01': ('M01', 'm01', 12000), 'M02': ('M02', 'm01', 2500),
    'M02P': ('M02', 'm02', 7300), 'M03': ('M03', 'm01', 3000), 'M05': ('M05', 'm01', 3000),
    'M06': ('M06', 'm01', 3000), 'M09': ('M09', 'm01', 3000), 'M10': ('M10', 'm01', 2600),
    'A01': ('A01', 'm01', 6000), 'P01': ('P01', 'm01', 6000),
    # the other campaign missions (2026-10-27: the names AVPE_LEVEL accepts; M04/M07/M08 and the
    # rest of P and A do not exist), each with the generic m01 pad
    **{m: (m, 'm01', 6000) for m in ('P02', 'P03', 'P04', 'P06', 'P08', 'P11',
                                     'A02', 'A03', 'A04', 'A06', 'A07', 'A08')},
    # monkey runs: 20,000 frames of seeded random play with cheats (tools/monkeypad.py), K_<mission>
    **{'K_' + m: (m, 'monkey_' + m, 20000) for m in ('M01', 'M02', 'M03', 'M05', 'M06', 'M09', 'M10',
                                                     'P01', 'P02', 'P03', 'P04', 'P06', 'P08', 'P11',
                                                     'A01', 'A02', 'A03', 'A04', 'A06', 'A07', 'A08',
                                                     'T01', 'T02', 'T03', 'T04', 'T05')},
}
# monkey variants written by tools/monkeyloop.py: tools/replay/monkey_<M>_<tag>.pad -> config K_<M>_<tag>
import glob as _glob
for _p in sorted(_glob.glob(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'replay', 'monkey_*_*.pad'))):
    _tag = os.path.basename(_p)[len('monkey_'):-len('.pad')]
    _first = open(_p).readline()
    _frames = int(_first.split(' frames')[0].split(', ')[-1]) if ' frames' in _first else 20000
    CONFIGS['K_' + _tag] = (_tag.split('_')[0], 'monkey_' + _tag, _frames)


def run(names):
    os.makedirs(OUT, exist_ok=True)
    for n in names:
        mission, pad, frames = CONFIGS[n]
        prefix = os.path.join(OUT, n)
        for suf in ('.exit', '.mission'):
            if os.path.exists(prefix + suf):
                os.remove(prefix + suf)
        env = dict(os.environ, AVPE_COVERAGE=prefix, AVPE_COVERAGE_COUNTS='1',
                   AVPE_REPLAY_PAD='%s/%s.pad' % (P, pad), REPLAY_TIMEOUT='1800')
        if n.startswith('K_'):
            env['AVPE_NOAPU'] = '1'     # long monkey runs: the bench's DirectSound fault (no APU) would end them
        if mission:
            env['MISSION'] = mission
        else:
            env['AVPE_NO_LEVEL'] = '1'
        r = subprocess.run(['sh', AVPE + '/tools/replay_run.sh', prefix + '.rph', str(frames), BIN],
                           env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        ok = os.path.exists(prefix + '.exit')
        print('%-7s %s %s' % (n, 'ok' if ok else 'NO EXIT DUMP', r.stdout.strip()[-80:]), flush=True)
        if os.path.exists(prefix + '.rph'):
            os.remove(prefix + '.rph')     # page hashes not needed (they are large)
    merge()


def merge():
    by_name = {f['name']: int(f['start'], 16) for f in json.load(open(FUNCS))}
    # configs with no dump here (a rebuilt workspace) keep their counts from census.json
    census = {}
    old = json.load(open(AVPE + '/symbols/census.json')) if os.path.exists(AVPE + '/symbols/census.json') else {}
    for n in CONFIGS:
        path = os.path.join(OUT, n + '.exit')
        if not os.path.exists(path):            # a run that crashed: its last checkpoint
            dumps = [d for d in glob.glob(os.path.join(OUT, n + '.*')) if not d.endswith(('.log', '.rph'))]
            if not dumps:
                for va, c in old.items():
                    if n in c:
                        census.setdefault(va, {})[n] = c[n]
                continue
            path = max(dumps, key=os.path.getmtime)
            print('%s: no exit dump, using %s' % (n, os.path.basename(path)))
        for line in open(path):
            parts = line.split()
            if len(parts) != 2 or parts[0] not in by_name:
                continue
            census.setdefault('0x%08X' % by_name[parts[0]], {})[n] = int(parts[1])
    json.dump(census, open(AVPE + '/symbols/census.json', 'w'), indent=0, sort_keys=True)
    print('census.json: %d functions over %d configs' %
          (len(census), sum(os.path.exists(os.path.join(OUT, n + '.exit')) for n in CONFIGS)))


def who(vas):
    census = json.load(open(AVPE + '/symbols/census.json'))
    for v in vas:
        c = census.get('0x%08X' % int(v, 16), {})
        print('%08X  %s' % (int(v, 16), ' '.join('%s:%d' % kv for kv in sorted(c.items(), key=lambda kv: -kv[1]))
                             or 'not called in any config'))


if __name__ == '__main__':
    a = sys.argv[1:]
    if not a:
        print(__doc__); sys.exit(2)
    if a[0] == 'run':
        run(a[1:] or list(CONFIGS))
    elif a[0] == 'merge':
        merge()
    elif a[0] == 'who':
        who(a[1:])
