#!/usr/bin/env python3
"""Verify one decompiled function against the original.

Usage: verify.py <hex va> [n] [--probe "spec"] [--frame N] [--stop-at callee]
  --stop-at: compare at the moment the function calls translated code at
             `callee` (memory, pushed arguments, callee-saved registers)
  --probe: call the function on purpose (see AVPE_PROBE in avpe_bridge.c)
Runs the native build with AVPE_VERIFY=<va>:<n> (snapshots at entry and exit
of the n-th call of the decompiled function), replays the ORIGINAL code from
the entry snapshot in the oracle until it returns, and diffs end states.
PASS: registers the convention defines (eax when the function returns a
value, esp) and all live memory identical, ignoring the timer word other
threads update (0x00740020) and dead stack below esp.
"""
import os, struct, subprocess, sys
argv = sys.argv[1:]
probe = frame = None
if '--probe' in argv: i = argv.index('--probe'); probe = argv[i + 1]; del argv[i:i + 2]
if '--frame' in argv: i = argv.index('--frame'); frame = argv[i + 1]; del argv[i:i + 2]
stop_at = None
if '--stop-at' in argv: i = argv.index('--stop-at'); stop_at = int(argv[i + 1], 16); del argv[i:i + 2]
va = int(argv[0], 16); n = int(argv[1]) if len(argv) > 1 else 1
OUT = '/home/claude/avpe-recomp'; T = os.path.dirname(os.path.abspath(__file__))
os.makedirs(OUT + '/verify', exist_ok=True)
before = '%s/verify/%08X_before.bin' % (OUT, va); after = '%s/verify/%08X_after.bin' % (OUT, va)
for f in (before, after):
    if os.path.exists(f): os.remove(f)
env = dict(os.environ, DISPLAY=':99', LIBGL_ALWAYS_SOFTWARE='1', AVPE_NOVSYNC='1', AVPE_FATAL_EXIT='1',
           AVPE_VERIFY='%X:%d' % (va, n))
env.setdefault('AVPE_NO_GPU', '1')
if probe:
    env['AVPE_PROBE'] = 'va=%X ' % va + probe
    env['AVPE_VERIFY'] = '0:0'          # armed by the probe itself
    if frame: env['AVPE_PROBE_FRAME'] = frame
if stop_at: env['AVPE_VERIFY_STOP'] = '%X' % stop_at
# Probes call game code from the frame hook and can deadlock against another
# thread holding the heap lock: bounded time, a few attempts.
for attempt in range(3 if probe else 1):
    try:
        subprocess.run(['./avpe_recomp'], cwd=OUT, env=env, stdout=subprocess.DEVNULL,
                       stderr=open(OUT + '/verify/last_run.log', 'w'),
                       timeout=float(os.environ.get('VERIFY_TIMEOUT', '90' if probe else '600')))
    except subprocess.TimeoutExpired:
        subprocess.run(['pkill', '-x', 'avpe_recomp'])
    if os.path.exists(after):
        break
if not os.path.exists(after):
    sys.exit('no capture: call #%d of %08X never happened (or crashed)' % (n, va))
hb = struct.unpack_from('<12I', open(before, 'rb').read(48))
_hb = 64 if hb[0] == 0x32414E53 else 48
ret_addr = struct.unpack('<I', open(before, 'rb').read()[_hb + hb[5]:_hb + hb[5] + 4])[0]
if probe: ret_addr = 0xDEC0DE00
if stop_at: ret_addr = stop_at
sys.path.insert(0, T); import bblocks, verify_lib
rep = subprocess.run([sys.executable, T + '/snap_replay.py', before, '%X' % ret_addr, '%X' % va,
                      '--trace-range=%X:%X' % (va, bblocks.end(va))], capture_output=True, text=True).stdout
print(next((l for l in rep.splitlines() if l.startswith('stopped:')), 'replay produced no output'))
if 'returned to caller' not in rep:
    sys.exit('FAIL: original did not run to completion in the oracle:\n' + rep[-800:])
O = open(before + '.after', 'rb').read(); L = open(after, 'rb').read()
hl = struct.unpack_from('<12I', L); l = L[64 if hl[0] == 0x32414E53 else 48:]
oe = [int(x.split('=')[1], 16) for x in rep.strip().splitlines()[-1].split()]   # eax, esp from replay
esp = hl[5]
bad = []
if oe[1] != esp: bad.append('esp: original %08X decompiled %08X' % (oe[1], esp))
def returns():
    """What the decompiled guest entry declares it returns: 'eax' (ret),
    'al' (ret_al, a bool) or None (ret_void). The C++ signature is the claim
    being verified; eax left over by a void function is not part of it."""
    import glob, re
    for f in glob.glob('/home/claude/avpe-decomp/src/*.cpp'):
        src = open(f).read()
        m = re.search(r'^void sub_%08X\(void\)\s*\{(.*?)^\}' % va, src, re.M | re.S)
        if m:
            b = m.group(1)
            return 'al' if 'ret_al(' in b else 'eax' if re.search(r'\bret\(', b) else None
    return 'eax'
kind = returns()
notes = []
if not stop_at and oe[0] != hl[1]:
    msg = 'eax: original %08X decompiled %08X' % (oe[0], hl[1])
    if kind == 'eax' or (kind == 'al' and (oe[0] & 0xFF) != (hl[1] & 0xFF)):
        bad.append(msg)
    else:
        notes.append(msg + ' (not compared: the function returns %s)' % ('al' if kind == 'al' else 'nothing'))
if stop_at:
    import json
    abi = json.load(open(os.path.join(T, '..', 'symbols', 'abi.json')))
    callee_in = set(abi.get('0x%08X' % stop_at, {}).get('regs', []))
    print('callee %08X reads registers: %s' % (stop_at, ', '.join(sorted(callee_in)) or 'none'))
    names = 'eax ecx edx ebx esp ebp esi edi'.split()
    ob = open(before + '.after', 'rb').read()   # replay end state has no header; registers from replay output
    for line in rep.splitlines():
        if line.startswith('regs '):
            for kv in line[5:].split():
                k, v = kv.split('='); v = int(v, 16); j = names.index(k)
                if k in callee_in and v != hl[1 + j]:
                    bad.append('%s: original %08X decompiled %08X' % (k, v, hl[1 + j]))
entry_esp = hb[5]
nargs = hl[11] if stop_at else 0
def own_frame(off):
    """The function's own stack frame at a call boundary: below its entry esp,
    above the callee's arguments. Scratch the C++ keeps in host locals."""
    return stop_at and esp + 4 + 4 * nargs <= off < entry_esp
# Memory other threads change while the main thread runs the function: their
# stacks (512 KB below each worker's stack top, from the run log) and the
# background loader's countdown and flag (0x2F6850, 0x2F6854; sub_000DE9B0). The oracle replays
# the main thread alone.
import re as _re
volatile = [(0x740020, 0x740024), (0x2F6850, 0x2F6858)]   # loader: countdown, busy flag
for m in _re.finditer(r'stack top 0x([0-9A-Fa-f]{8})', open(OUT + '/verify/last_run.log', errors='ignore').read()):
    top = int(m.group(1), 16) + 16
    volatile.append((top - 512 * 1024, top))
def is_volatile(off):
    return any(a <= off < b for a, b in volatile)
for off in range(0, len(O), 4):
    if stop_at and (off == esp or own_frame(off)):
        continue                                  # return address slot / own scratch
    if O[off:off+4] != l[off:off+4] and not is_volatile(off) and not (esp - 0x10000 <= off < esp and not (os.environ.get('VERIFY_DEADSTACK') and esp - int(os.environ['VERIFY_DEADSTACK'], 0) <= off)):
        bad.append('mem %08X: original %08X decompiled %08X' % (off, struct.unpack_from('<I', O, off)[0], struct.unpack_from('<I', l, off)[0]))
        if len(bad) > 40: break
for n_ in notes: print('note: ' + n_)
print('PASS' if not bad else 'DIFF (%d):\n  ' % len(bad) + '\n  '.join(bad[:40]))
verify_lib.record_paths(va, ('probe ' + probe if probe else 'natural call #%d' % n) +
                        (' at frame %s' % frame if probe and frame else '') +
                        (' until call %08X' % stop_at if stop_at else ''), 'PASS' if not bad else 'DIFF', rep, bad)
if not bad:
    # record it: avpe-decomp/verified.json, one entry per (function, check)
    import json, time
    VJ = '/home/claude/avpe-decomp/verified.json'
    vj = json.load(open(VJ)) if os.path.exists(VJ) else {}
    how = ('probe ' + probe if probe else 'natural call #%d' % n) + (' at frame %s' % frame if probe and frame else '') + \
          (' until call %08X' % stop_at if stop_at else '')
    stopped = next((l for l in rep.splitlines() if l.startswith('stopped:')), '')
    insns = stopped.split(' after ')[1].split(' instructions')[0] if ' after ' in stopped else '?'
    vj.setdefault('0x%08X' % va, [])
    entry = {'check': how, 'original_instructions': insns, 'date': time.strftime('%Y-%m-%d')}
    if not any(e['check'] == how for e in vj['0x%08X' % va]):
        vj['0x%08X' % va].append(entry)
    json.dump(vj, open(VJ, 'w'), indent=1)
sys.exit(0 if not bad else 1)
