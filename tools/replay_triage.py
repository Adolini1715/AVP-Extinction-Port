#!/usr/bin/env python3
"""replay_triage.py FRAME PAGE [--words N] [--lifted BIN] [--hybrid BIN]

Triage a replay divergence (notes/replay.md; the first line of `pipe.sh replay`
failure output gives FRAME and PAGE, hex guest address of the 4 KB page).
1. Run hybrid and all-lifted to FRAME with AVPE_REPLAY_SNAP, list the words of
   PAGE that differ.
2. Take the first differing word(s) (--words, default 1, max 4 hardware
   watchpoints) and rerun both builds under gdb with a hardware write
   watchpoint on host address = guest VA + g_xbox_mem_offset. Every change of
   the word is logged with the game frame and the call chain; gdb names the
   code (sub_XXXXXXXX for lifted, C++ names for decompiled functions).
3. Print both write histories and the first write where they differ, with the
   writing function. Holds the pipeline lock (through replay_run.sh).
Logs and snapshots: /home/claude/avpe-recomp/replay/triage_*.
"""
import os, struct, subprocess, sys
T = os.path.dirname(os.path.abspath(__file__))
R = '/home/claude/avpe-recomp/replay'
BIN = {'hybrid': '/home/claude/avpe-recomp/avpe_recomp', 'lifted': '/home/claude/avpe-recomp/avpe_lifted'}

def run(kind, frame, env):
    e = dict(os.environ); e.update(env)
    e['AVPE_LOCKED'] = '1'
    out = '%s/triage_%s.rph' % (R, kind)
    r = subprocess.run(['sh', T + '/replay_run.sh', out, str(frame), BIN[kind]], env=e, capture_output=True, text=True)
    return r.stdout.strip()

def main():
    a = sys.argv[1:]
    if len(a) < 2: sys.exit(__doc__)
    frame = int(a[0]); page = int(a[1], 16) & ~0xFFF; nw = 1
    i = 2
    while i < len(a):
        if a[i] == '--words': nw = min(4, int(a[i + 1])); i += 2
        elif a[i] in ('--lifted', '--hybrid'): BIN[a[i][2:]] = a[i + 1]; i += 2
        else: sys.exit('unknown option ' + a[i])
    # take the lock for the whole triage so no other run interleaves
    import fcntl
    lk = open('/tmp/avpe-pipeline.lock', 'w'); fcntl.flock(lk, fcntl.LOCK_EX)
    os.environ['AVPE_LOCKED'] = '1'
    snaps = {}
    for k in BIN:
        snaps[k] = '%s/triage_%s.snap' % (R, k)
        print('snapshot %s at frame %d: %s' % (k, frame, run(k, frame, {'AVPE_REPLAY_EVERY': '0', 'AVPE_REPLAY_SNAP': '%d:%s' % (frame, snaps[k])})))
    data = {}
    for k in BIN:
        with open(snaps[k], 'rb') as f:
            f.seek(64 + page); data[k] = f.read(4096)
    diffs = [o for o in range(0, 4096, 4) if data['hybrid'][o:o + 4] != data['lifted'][o:o + 4]]
    print('page %08X: %d differing words (hybrid vs lifted)' % (page, len(diffs)))
    for o in diffs[:48]:
        h = struct.unpack_from('<I', data['hybrid'], o)[0]; l = struct.unpack_from('<I', data['lifted'], o)[0]
        print('  %08X  hybrid %08X  lifted %08X' % (page + o, h, l))
    if len(diffs) > 48: print('  ... %d more' % (len(diffs) - 48))
    if not diffs: sys.exit('no difference in this page at this frame (masked or already converged)')
    words = ','.join('%08X' % (page + o) for o in diffs[:nw])
    hist = {}
    for k in BIN:
        log = '%s/triage_%s.watch' % (R, k)
        gdb = 'gdb -batch -nx -x %s/replay_triage_gdb.py --args' % T
        print('watching %s in %s ...' % (words, k))
        run(k, frame, {'AVPE_REPLAY_EVERY': '0', 'AVPE_RUNNER': gdb, 'TRIAGE_WORDS': words, 'TRIAGE_OUT': log, 'AVPE_REPLAY_SNAP': ''})
        hist[k] = [l.rstrip('\n') for l in open(log) if l.startswith('W ')] if os.path.exists(log) else []
    def parse(l):
        head, _, chain = l.partition(' | ')
        p = head.split()
        return (p[1], int(p[3]), p[4], p[6]), chain.split(' < ')
    for k in ('lifted', 'hybrid'):
        print('\n%s write history (%d writes):' % (k, len(hist[k])))
        for l in hist[k][:20]:
            (w, fr, old, new), ch = parse(l)
            print('  [%s] frame %d %s -> %s by %s' % (w, fr, old, new, ' < '.join(ch[:5])))
    L = [parse(l) for l in hist['lifted']]; H = [parse(l) for l in hist['hybrid']]
    print()
    for n in range(max(len(L), len(H))):
        l = L[n] if n < len(L) else None; h = H[n] if n < len(H) else None
        if l and h and l[0] == h[0]: continue
        print('first differing write (#%d):' % n)
        for k, x in (('lifted', l), ('hybrid', h)):
            if x:
                (w, fr, old, new), ch = x
                fn = next((c for c in ch if c.startswith('sub_') or c.startswith('avpe')), ch[0])
                print('  %s: [%s] frame %d value %s by %s\n    chain: %s' % (k, w, fr, new, fn, ' < '.join(ch[:12])))
            else:
                print('  %s: no such write' % k)
        break
    else:
        print('write histories identical for the watched word(s): the difference arises elsewhere (a copy via another word, or a write before the watch started)')
main()
