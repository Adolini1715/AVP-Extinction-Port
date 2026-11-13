#!/usr/bin/env python3
"""Verify many decompiled functions in one game run (natural calls only).

    verify_multi.py <File.cpp | va[:n]> ... [--timeout S] [--pad SCRIPT]
    verify_multi.py --probes FILE [--frame N] [--timeout S]

Runs the native build once with AVPE_VERIFY_MULTI listing the functions (the
n-th call of each, default the first): the bridge streams a memory image and,
for every entry and exit, the registers and the pages that changed
(verify/multi.stream). Each function is then checked exactly as verify.py
does it (verify_lib): the original replayed in the oracle from the entry
state, compared with the exit state. Passing checks are recorded in
verified.json as "natural call #n".

--probes: FILE has one probe per line, "va=XXXX <spec>" (spec as for
verify.py --probe); all run in one game run at frame N (default 40) and are
recorded as "probe <spec> at frame N".

A File.cpp argument takes every function the file defines a guest entry for
that has no natural-call check yet. Functions that were not called before the
timeout are listed. --pad feeds a virtual-pad script (AVPE_PAD) for runs that
need input.
"""
import os, re, struct, subprocess, sys, time
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import json
import verify_lib as V

OUT = V.OUT
D = '/home/claude/avpe-decomp'
argv = sys.argv[1:]
timeout = 600
pad = None
probes = None
frame = '40'
if '--probes' in argv:
    i = argv.index('--probes'); probes = argv[i + 1]; del argv[i:i + 2]
if '--frame' in argv:
    i = argv.index('--frame'); frame = argv[i + 1]; del argv[i:i + 2]
if '--timeout' in argv:
    i = argv.index('--timeout'); timeout = float(argv[i + 1]); del argv[i:i + 2]
keep = '--keep' in argv
if keep:
    argv.remove('--keep')
if '--pad' in argv:
    i = argv.index('--pad'); pad = argv[i + 1]; del argv[i:i + 2]
want = {}
vj = json.load(open(V.VJ)) if os.path.exists(V.VJ) else {}
for a in argv:
    if a.endswith('.cpp'):
        src = open(os.path.join(D, 'src', a)).read()
        for m in re.findall(r'^void sub_([0-9A-Fa-f]{8})\(void\)', src, re.M):
            va = int(m, 16)
            if not any(e['check'].startswith('natural call') for e in vj.get('0x%08X' % va, [])):
                want[va] = 1
    else:
        va, _, n = a.partition(':')
        want[int(va, 16)] = int(n or 1)
probe_lines = {}
if probes:
    for k, line in enumerate(open(probes), 1):
        line = line.strip()
        if line:
            probe_lines[k] = line
            want[int(re.search(r'va=([0-9A-Fa-f]+)', line).group(1), 16)] = 1
if not want:
    sys.exit('nothing to verify')
os.makedirs(OUT + '/verify', exist_ok=True)
lst = OUT + '/verify/multi.list'
open(lst, 'w').write(''.join('%X:%d\n' % kv for kv in want.items()))
stream = OUT + '/verify/multi.stream'
if os.path.exists(stream):
    os.remove(stream)
env = dict(os.environ, DISPLAY=':99', AVPE_NOVSYNC='1', AVPE_FATAL_EXIT='1')
if probes:
    env['AVPE_PROBE_MULTI'] = os.path.abspath(probes); env['AVPE_PROBE_FRAME'] = frame
else:
    env['AVPE_VERIFY_MULTI'] = lst
env.setdefault('AVPE_NO_GPU', '1')
env['AVPE_VERIFY_EXTERN'] = ','.join('%X' % v for v in V.EXTERNS)
if pad:
    env['AVPE_PAD'] = pad
run_log = OUT + '/verify/last_multi_run.log'
given = os.environ.get('AVPE_STREAM_GIVEN')     # "<stream> <log>": a fork server already ran the probes (tools/forkprobe.py)
if given:
    stream, run_log = given.split()
print('running the game for %d %s (timeout %ds)...' % (len(probe_lines) or len(want), 'probes' if probes else 'functions', timeout), flush=True)
t0 = time.time()
try:
    if not given:
        subprocess.run(['./avpe_recomp'], cwd=OUT, env=env, stdout=subprocess.DEVNULL,
                       stderr=open(run_log, 'w'), timeout=timeout)
except subprocess.TimeoutExpired:
    subprocess.run(['pkill', '-x', 'avpe_recomp'])
print('game run: %.0fs' % (time.time() - t0), flush=True)

# ---- replay the stream
RAM = 0x04000000
img = bytearray(RAM)
before_hdr = {}
done = set()
results = {}
open_caps = {}                                         # captures in progress: their external calls
ext_esp = 0
ext_fp = 0
f = open(stream, 'rb')
tmpd = OUT + '/verify/multi'
os.makedirs(tmpd, exist_ok=True)


def snapshot(path, hdr):
    with open(path, 'wb') as o:
        o.write(struct.pack('<16I', *hdr)); o.write(img)


while True:
    rec = f.read(32)
    if len(rec) < 32:
        break
    magic, kind, va, callno, np_ = struct.unpack_from('<5I', rec)
    if magic != 0x43455256:
        print('stream corrupt'); break
    hdr = struct.unpack('<16I', f.read(64))
    ok = True
    writes = []
    for _ in range(np_):
        b = f.read(4 + 4096)
        if len(b) < 4100:
            ok = False; break
        pg = struct.unpack_from('<I', b)[0]
        if kind == 4:                                   # bytes the external call changed
            old, new = img[pg * 4096:(pg + 1) * 4096], b[4:]
            i = 0
            while i < 4096:
                if old[i] != new[i]:
                    j = i
                    while j < 4096 and old[j] != new[j]:
                        j += 1
                    writes.append([pg * 4096 + i, new[i:j].hex()])
                    i = j
                else:
                    i += 1
        img[pg * 4096:(pg + 1) * 4096] = b[4:]
    if not ok:
        break
    key = (va, callno)
    if kind == 5:                                       # rdtsc value read inside the captures
        for k in open_caps:
            open_caps[k]['tsc'].append(hdr[1] | hdr[2] << 32)
        continue
    if kind == 3:
        ext_esp = hdr[5]
        ext_fp = hdr[13]                                # x87 top at the call (0x100 | top)
        ext_in = V.extern_inputs(va, hdr, img)
        continue
    if kind == 4:
        # the callee's own frame, return slot and popped arguments are dead
        # stack once it returns; in the oracle the same addresses can be live
        # (the original's frame need not match the decompiled code's), so
        # those bytes are not replayed
        lo, hi = ext_esp - 0x10000, hdr[5]
        kept = []
        if va >= 0xFE000000:                            # kernel call: its outputs in full, changed or not
            writes = writes + V.kernel_outputs(va, (ext_in or {}).get('args', []), img)
        for addr, hx in writes:
            end = addr + len(hx) // 2
            if end <= lo or addr >= hi:
                kept.append([addr, hx])
                continue
            if addr < lo:
                kept.append([addr, hx[:2 * (lo - addr)]])
            if end > hi:
                kept.append([hi, hx[2 * (hi - addr):]])
        e = {'va': va, 'eax': hdr[1], 'ecx': hdr[2], 'edx': hdr[3], 'pop': hdr[5] - ext_esp, 'writes': kept,
             'in': ext_in, 'esp': ext_esp}
        # a call that returns in st(0) (one value pushed): the oracle pushes the same
        if ext_fp & 0x100 and hdr[13] & 0x100 and ((ext_fp - hdr[13]) & 7) == 1:
            e['fp_push'] = [hdr[14], hdr[15]]
        for k in open_caps:
            open_caps[k]['calls'].append(e)
        continue
    if kind == 1:
        open_caps[key] = {'calls': [], 'tsc': []}
        snapshot('%s/%08X_%d_before.bin' % (tmpd, va, callno), hdr)
        # st(0) and st(1) for two-argument x87 functions (snap_replay.py --fp-in2)
        open('%s/%08X_%d_before.bin.fp' % (tmpd, va, callno), 'wb').write(
            struct.pack('<II', hdr[14], hdr[15]) + rec[20:28])
    elif kind == 2:
        before = '%s/%08X_%d_before.bin' % (tmpd, va, callno)
        after = '%s/%08X_%d_after.bin' % (tmpd, va, callno)
        snapshot(after, hdr)
        ext = open_caps.pop(key, None)
        if ext and (ext['calls'] or ext['tsc']):
            json.dump(ext, open(before + '.ext.json', 'w'))
        # a probe that sets st(0) (st0=): the original takes it from the x87 stack too
        _n_st0 = probe_lines.get(callno, '').count('st0=')     # two st0= (st(1) then st(0)): _CIpow probes
        rep = V.replay(va, before, probe=bool(probes), fp_in=2 if _n_st0 >= 2 else _n_st0 == 1)
        if probes:
            spec = re.sub(r'\s*va=[0-9A-Fa-f]+\s*', ' ', probe_lines.get(callno, '')).strip()
            how = 'probe %s at frame %s' % (spec, frame)
            label = 'probe %s' % spec
        else:
            how = label = 'natural call #%d' % callno
        if 'returned to caller' not in rep:
            why = next((l for l in rep.splitlines() if l.startswith('stopped:')), 'stopped: ?')
            results[key] = ('FAIL', ['original did not run to completion in the oracle (%s)' % why[9:]], [])
        else:
            bad, notes = V.compare(va, before, after, rep, run_log=run_log)
            bad = [l[9:] for l in rep.splitlines() if l.startswith('[EXTERN] ')] + bad   # inputs of extern calls
            results[key] = ('PASS' if not bad else 'DIFF', bad, notes)
            if not bad and not os.environ.get('AVPE_NO_RECORD'):
                V.record(va, how, rep)
        if not os.environ.get('AVPE_NO_RECORD'):      # set by drivers that only explore (fedrive.py with no --vas)
            V.record_paths(va, how, results[key][0], rep, results[key][1])
        print('%08X %-40s %s  (%s instructions)' % (va, label[:40], results[key][0], V.instructions(rep)), flush=True)
        for b in results[key][1][:8]:
            print('    ' + b)
        if os.environ.get('AVPE_PATHS'):            # which instructions of the function ran (path coverage)
            print('    ' + next((l for l in rep.splitlines() if l.startswith('range-trace: ')), 'range-trace: ?')[:2000])
        for p in (before, after, before + '.after', before + '.ext.json'):
            if os.path.exists(p) and not keep:
                os.remove(p)
        done.add(va)
missing = sorted(set(want) - done)
print('\n%d checked: %d pass, %d differ; %d not called in this run%s' % (
    len(results), sum(r[0] == 'PASS' for r in results.values()), sum(r[0] != 'PASS' for r in results.values()),
    len(missing), (': ' + ' '.join('%08X' % m for m in missing)) if missing else ''))
sys.exit(0 if all(r[0] == 'PASS' for r in results.values()) else 1)
