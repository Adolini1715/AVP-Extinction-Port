#!/usr/bin/env python3
"""replay_bisect.py [--frames N] [--max-culprits K] [--exclude VA ...]

Finds decompiled functions that make the replay diverge from the all-lifted
run (notes/replay.md), using the toggle build (tools/build_toggle.sh,
avpe_toggle). Prefix bisect over the sorted VA list: the smallest k such that
enabling the first k functions (minus the ones already found) diverges names
function k-1 as a culprit; it is excluded and the search repeats until the full
set is clean. Each probe is one replay run of --frames frames (default 1100,
past the first divergence at 1050). Needs the cached lifted reference
(pipe.sh replay --lifted). Result: replay/bisect.txt, one VA per line.
"""
import subprocess, sys, os
R = '/home/claude/avpe-recomp/replay'; T = '/home/claude/avpe/tools'
B = '/home/claude/avpe-recomp/avpe_toggle'
ref = os.environ.get('REF', R + '/gate_lifted_12000.rph')
frames = 1100; maxc = 20; excl = []
a = sys.argv[1:]
while a:
    x = a.pop(0)
    if x == '--frames': frames = int(a.pop(0))
    elif x == '--max-culprits': maxc = int(a.pop(0))
    elif x == '--exclude': excl.append(a.pop(0).upper())
lst = [l.strip() for l in open('/home/claude/avpe-toggle/list.txt') if l.strip()]
def run(vas):
    f = R + '/bis_on.txt'; open(f, 'w').write('\n'.join(vas) + '\n')
    env = dict(os.environ, AVPE_DEC_SET='on:' + f)
    out = R + '/bis.rph'
    subprocess.run(['sh', T + '/replay_run.sh', out, str(frames), B], env=env, stdout=subprocess.DEVNULL)
    r = subprocess.run(['python3', T + '/replay_cmp.py', out, ref, '--mask', T + '/replay/mask_stack.txt', '--fail'],
                       stdout=subprocess.DEVNULL)
    return r.returncode != 0
found = []
for _ in range(maxc):
    pool = [v for v in lst if v not in excl and v not in found]
    if not run(pool):
        print('clean with %d functions enabled; culprits: %s' % (len(pool), ' '.join(found) or 'none')); break
    lo, hi = 0, len(pool)       # prefix [0,hi) diverges, [0,lo) does not
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if run(pool[:mid]): hi = mid
        else: lo = mid
        print('  prefix %d..%d' % (lo, hi), flush=True)
    found.append(pool[hi - 1]); print('culprit', pool[hi - 1], flush=True)
    open(R + '/bisect.txt', 'w').write('\n'.join(found) + '\n')
else:
    print('stopped at max culprits; found:', ' '.join(found))
