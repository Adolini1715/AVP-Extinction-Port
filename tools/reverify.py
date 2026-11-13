#!/usr/bin/env python3
"""Re-run every recorded verification check (avpe-decomp/verified.json) for
the functions a decompiled source file defines, after a refactor or a change
to a shared header. Prints PASS/DIFF per check; verified.json is not changed
by a failing re-run (verify.py only records passes).

    reverify.py <File.cpp | 0xVA> [...]
"""
import json, os, re, subprocess, sys
T = os.path.dirname(os.path.abspath(__file__))
D = '/home/claude/avpe-decomp'
vj = json.load(open(os.path.join(D, 'verified.json')))
vas = []
for a in sys.argv[1:]:
    if a.startswith('0x'):
        vas.append(int(a, 16))
    else:
        src = open(os.path.join(D, 'src', a)).read()
        vas += [int(m, 16) for m in re.findall(r'^void sub_([0-9A-Fa-f]{8})\(void\)', src, re.M)]


def args_of(check):
    """verify.py arguments that reproduce a recorded check."""
    out = []
    m = re.search(r' until call ([0-9A-F]{8})$', check)
    if m:
        out += ['--stop-at', m.group(1)]; check = check[:m.start()]
    if check.startswith('natural call #'):
        return [check.split('#')[1]] + out
    if check.startswith('probe '):
        spec = check[6:]
        m = re.search(r' at frame (\d+)$', spec)
        if m:
            out += ['--frame', m.group(1)]; spec = spec[:m.start()]
        return ['--probe', spec] + out
    return None                                       # e.g. "covered by ..."


bad = 0
for va in vas:
    for e in vj.get('0x%08X' % va, []):
        a = args_of(e['check'])
        if a is None:
            print('%08X  %-60s  (not re-runnable)' % (va, e['check'][:60])); continue
        r = subprocess.run([sys.executable, os.path.join(T, 'verify.py'), '%X' % va] + a, capture_output=True, text=True)
        res = 'PASS' if r.returncode == 0 else 'FAIL'
        bad += res != 'PASS'
        print('%08X  %-60s  %s' % (va, e['check'][:60], res), flush=True)
        if res != 'PASS':
            print('    ' + '\n    '.join(r.stdout.strip().splitlines()[-6:]))
sys.exit(1 if bad else 0)
