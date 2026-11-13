#!/usr/bin/env python3
"""Fault-injection test of the verifier: plant one small fault in each of N hand-written,
verified functions, rebuild, re-run their recorded natural-call checks, and report which
faults the oracle catches. The source is always restored (git checkout of the touched files).

    mutate.py [--n 10] [--seed 1] [--kinds const,cmp,skip] [--out DIR] [--only 0xVA,...]

Fault kinds (one per function, inside its C++ body, never its guest entry):
  const  an integer literal in an expression changed by one (0 -> 1, n -> n+1)
  cmp    a comparison flipped (== / !=, < / <=, > / >=)
  skip   an assignment statement removed (a store the function makes)
Result per mutant: CAUGHT (a check reported DIFF), MISSED (every check passed: either the
verifier is blind there or the check never runs the changed line), or INCONCLUSIVE (the
check could not run: crash, timeout, function not reached).
"""
import json, os, random, re, subprocess, sys, time

T = os.path.dirname(os.path.abspath(__file__))
D = '/home/claude/avpe-decomp'
SRC = os.path.join(D, 'src')

args = sys.argv[1:]
def opt(name, default):
    if name in args:
        i = args.index(name); v = args[i + 1]; del args[i:i + 2]; return v
    return default
N = int(opt('--n', '10'))
SEED = int(opt('--seed', '1'))
KINDS = opt('--kinds', 'const,cmp,skip').split(',')
OUT = opt('--out', os.path.join(T, '..', 'mutate_out'))
ONLY = [int(x, 16) for x in opt('--only', '').split(',') if x]
os.makedirs(OUT, exist_ok=True)

vj = json.load(open(os.path.join(D, 'verified.json')))
natural = {int(k, 16): [c['check'] for c in v if c['check'].startswith('natural call #')]
           for k, v in vj.items()}
natural = {k: v for k, v in natural.items() if v}
# checks run in Marine 1 (pipe.sh msingle): keep functions the census shows called there
_census = json.load(open(os.path.join(T, '..', 'symbols', 'census.json')))
natural = {k: v for k, v in natural.items() if _census.get(f'0x{k:08X}', {}).get('M01', 0) > 0}

HDR = re.compile(r'^/\* 0x([0-9A-F]{8})\b')
TYPES = ('uint8_t', 'uint16_t', 'uint32_t', 'int8_t', 'int16_t', 'int32_t', 'int ', 'unsigned', 'float ',
         'double ', 'bool ', 'char ', 'const ', 'auto ', 'static ', 'CVector ', 'GuestFrame', 'CallFrame',
         'return', 'if ', 'if(', 'for ', 'while ', 'else', '#', '//', '/*', '*', 'case ', 'switch')


def bodies():
    """(va, file, first line, last line) of hand-written C++ bodies with a natural-call check."""
    out = []
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith('.cpp'):
            continue
        lines = open(os.path.join(SRC, fn), errors='ignore').read().split('\n')
        heads = [(i, int(m.group(1), 16)) for i, l in enumerate(lines) for m in [HDR.match(l)] if m]
        for j, (i, va) in enumerate(heads):
            if va not in natural:
                continue
            end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
            block = lines[i:end]
            text = '\n'.join(block[:4])
            if 'Mechanical translation' in text or '(generated' in text or 'AVPE-CLASSGEN' in '\n'.join(block):
                continue
            # the C++ definition: first line ending in ')' or ') const' followed by '{'
            k = next((n for n in range(len(block) - 1) if block[n + 1].strip() == '{'
                      and not block[n].lstrip().startswith(('void sub_', '/*', '*'))
                      and 'extern' not in block[n]), None)
            if k is None:
                continue
            # body ends at the matching closing brace at column 0
            e = next((n for n in range(k + 2, len(block)) if block[n].startswith('}')), None)
            if e is None or e - k < 4:
                continue
            body = '\n'.join(block[k:e])
            if 'void sub_' in body or 'extern "C"' in body:
                continue
            out.append((va, fn, i + k + 2, i + e - 1))
    return out


LIT = re.compile(r'(?<![\w.])(0x[0-9A-Fa-f]+|\d+)(?![\w.])')
CMP = [(' == ', ' != '), (' != ', ' == '), (' <= ', ' < '), (' >= ', ' > '), (' < ', ' <= '), (' > ', ' >= ')]
ASSIGN = re.compile(r'^(\s*)([A-Za-z_][\w]*(?:(?:->|\.)\w+|\[[^\]]*\])*|m(?:8|16|32|f)\([^;]*?\)|\*\([^;]*?\))'
                    r'\s*(=|\+=|-=|\|=|&=|\^=)\s(?!=)[^;]*;\s*(/\*.*\*/)?\s*$')


def candidates(line, kind):
    code = line.split('/*')[0].split('//')[0]
    s = code.strip()
    if not s or s.startswith(('/*', '*', '//', '#', 'GuestFrame', 'CallFrame', '{', '}')):
        return []
    if kind == 'const':
        if 'gcall(' in code or 'vcall(' in code or 'case ' in code or 'GuestFrame' in code:
            return []
        if not any(op in code for op in (' = ', ' == ', ' != ', ' < ', ' > ', ' + ', ' - ', ' & ', ' | ', ' >> ', ' << ', ' * ')):
            return []
        res = []
        for m in LIT.finditer(code):
            tok = m.group(1)
            v = int(tok, 16) if tok.lower().startswith('0x') else int(tok)
            nv = v + 1
            rep = (hex(nv).upper().replace('0X', '0x') if tok.lower().startswith('0x') else str(nv))
            res.append((m.start(), m.end(), rep, f'{tok} -> {rep}'))
        return res
    if kind == 'cmp':
        if 'template' in code or 'gptr<' in code or 'static_cast<' in code:
            return []
        res = []
        for a, b in CMP:
            p = code.find(a)
            if p >= 0 and ('if' in code or 'while' in code or '?' in code or 'return' in code or '=' in code):
                res.append((p, p + len(a), b, f'{a.strip()} -> {b.strip()}'))
                break
        return res
    if kind == 'skip':
        if s.startswith(TYPES) or '==' in code:
            return []
        m = ASSIGN.match(line)
        if m and 'gcall' not in line and 'vcall' not in line:
            return [(0, len(line), m.group(1) + '/* MUTATED: skipped */', 'store removed: ' + s[:60])]
    return []


def stmt_start(lines, i):
    """a statement can be inserted before line i (the previous line ends a statement or opens
    a block, and line i does not continue an if/else chain)"""
    prev = lines[i - 1].split('//')[0].rstrip()
    cur = lines[i].strip()
    return prev.endswith((';', '{', '}')) and not cur.startswith(('else', '{', '}', '?', ':', '&&', '||', '+', '-', '.', ','))


def plant(n, seed, kinds):
    rnd = random.Random(seed)
    pool = bodies()
    if ONLY:
        pool = [b for b in pool if b[0] in ONLY]
    rnd.shuffle(pool)
    plan, files = [], {}
    for va, fn, a, b in pool:
        if len(plan) >= n:
            break
        lines = files.setdefault(fn, open(os.path.join(SRC, fn), errors='ignore').read().split('\n'))
        if any(p['file'] == fn and a <= p['i'] <= b for p in plan):
            continue
        kind = kinds[len(plan) % len(kinds)]
        def opts_for(k):
            return [(i, c) for i in range(a, b + 1) if stmt_start(lines, i) for c in candidates(lines[i], k)]
        opts = opts_for(kind)
        if not opts:
            for k2 in kinds:
                opts = opts_for(k2)
                if opts:
                    kind = k2; break
        if not opts:
            continue
        i, (s, e, rep, desc) = rnd.choice(opts)
        before = lines[i]
        after = lines[i][:s] + rep + lines[i][e:]
        plan.append(dict(va=va, file=fn, i=i, kind=kind, desc=desc, before=before.strip(), after=after.strip(), _after=after))
    # write: an include at the top, a hit marker before each mutated line
    for fn, lines in files.items():
        mine = sorted((p for p in plan if p['file'] == fn), key=lambda p: p['i'])
        out = ['#include <cstdio>']
        k = 0
        for i, l in enumerate(lines):
            if k < len(mine) and mine[k]['i'] == i:
                p = mine[k]
                ind = re.match(r'\s*', l).group(0)
                out.append(ind + '{ static int mh_; if (!mh_++) std::fprintf(stderr, "MUTHIT %%08X\\n", 0x%08Xu); }' % p['va'])
                out.append(p['_after'])
                p['line'] = len(out)            # 1-based line of the mutated statement
                k += 1
            else:
                out.append(l)
        open(os.path.join(SRC, fn), 'w').write('\n'.join(out))
    return plan


def restore(files):
    # verify.py records passes: a mutant that passed must not stay in the record
    subprocess.run(['git', '-C', D, 'checkout', '--', 'verified.json', 'verified_paths.json']
                   + [os.path.join('src', f) for f in sorted(set(files))], check=True)


def rebuild():
    r = subprocess.run(['sh', os.path.join(T, 'pipe.sh'), 'rebuild'], capture_output=True, text=True)
    return r.returncode == 0 and 'linked' in r.stdout, r.stdout + r.stderr


VDIR = '/home/claude/avpe-recomp/verify'


def raw_diff(va):
    """words that differ between the mutant's end state and the original's replayed end state,
    leaving out what the verifier leaves out by design (the clock word other threads update,
    dead stack below esp): an independent cross-check of the verifier's memory comparison"""
    import struct
    try:
        a = open(f'{VDIR}/{va:08X}_after.bin', 'rb').read()
        o = open(f'{VDIR}/{va:08X}_before.bin.after', 'rb').read()
    except OSError:
        return None
    h = struct.unpack_from('<12I', a); H = 64 if h[0] == 0x32414E53 else 48
    m = a[H:]; esp = h[5]
    out = []
    for p in range(0, min(len(m), len(o)), 4096):
        if m[p:p + 4096] != o[p:p + 4096]:
            for i in range(p, p + 4096, 4):
                if m[i:i + 4] != o[i:i + 4] and i != 0x740020 and not (esp - 0x10000 <= i < esp):
                    out.append(i)
    return out


def clean(va):
    for suf in ('_before.bin', '_after.bin', '_before.bin.after'):
        try:
            os.remove(f'{VDIR}/{va:08X}{suf}')
        except OSError:
            pass


def check(va, chk):
    a = ['sh', os.path.join(T, 'pipe.sh'), 'msingle', f'0x{va:08X}', chk.split('#')[1]]
    try:
        r = subprocess.run(a, capture_output=True, text=True, timeout=600)
        out = r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return 'TIMEOUT', ''
    try:
        log = open('/home/claude/avpe-recomp/verify/last_run.log', errors='ignore').read()
    except OSError:
        log = ''
    hit = ('MUTHIT %08X' % va) in log
    if re.search(r'\bPASS\b', out) and not re.search(r'\bDIFF\b', out):
        if not hit:
            return 'PASS-NOTRUN', out[-600:]
        d = raw_diff(va)
        if d == []:
            return 'PASS-EQUIV', out[-600:]          # the fault changed nothing on this input
        return 'PASS-RAN', 'raw diff at ' + ' '.join(hex(x) for x in (d or [])[:12]) + '\n' + out[-600:]
    if re.search(r'\bDIFF\b', out):
        return 'DIFF', out[-1500:]
    return 'ERROR', out[-1500:]


def main():
    """one mutant per build: plant, rebuild, check, restore; a mutant that does not compile is
    skipped, one whose game run crashes inside it counts as caught (the fault is visible)"""
    plan = plant(N, SEED, KINDS)                       # choose them all, then undo and do one at a time
    restore([p['file'] for p in plan])
    print(f'chose {len(plan)} faults', flush=True)
    results = []
    try:
        for p in plan:
            path = os.path.join(SRC, p['file'])
            lines = open(path, errors='ignore').read().split('\n')
            ind = re.match(r'\s*', lines[p['i']]).group(0)
            lines[p['i']:p['i'] + 1] = [ind + '{ static int mh_; if (!mh_++) std::fprintf(stderr, "MUTHIT %%08X\\n", 0x%08Xu); }' % p['va'],
                                        p['_after']]
            open(path, 'w').write('#include <cstdio>\n' + '\n'.join(lines))
            ok, log = rebuild()
            if not ok:
                p['result'] = 'NO-COMPILE'; p['verdicts'] = []
            else:
                verdicts = []
                for chk in natural[p['va']][:2]:
                    v, tail = check(p['va'], chk)
                    if v == 'ERROR' and ('MUTHIT %08X' % p['va']) in open(VDIR + '/last_run.log', errors='ignore').read() \
                            and '[CRASH]' in open(VDIR + '/last_run.log', errors='ignore').read():
                        v = 'CRASH'                    # the planted fault crashed the game: visible
                    clean(p['va'])
                    verdicts.append(v)
                    if v == 'PASS-RAN':
                        p['blind'] = tail
                    if v in ('ERROR', 'TIMEOUT'):
                        p['error'] = tail[-400:]
                    if v in ('DIFF', 'CRASH'):
                        p['diff'] = tail[-1500:]
                        break
                p['verdicts'] = verdicts
                p['result'] = ('CAUGHT' if 'DIFF' in verdicts or 'CRASH' in verdicts
                               else 'BLIND' if 'PASS-RAN' in verdicts
                               else 'EQUIVALENT' if 'PASS-EQUIV' in verdicts
                               else 'NOT-REACHED' if verdicts and all(v == 'PASS-NOTRUN' for v in verdicts)
                               else 'INCONCLUSIVE')
            restore([p['file']])
            print(f"  0x{p['va']:08X} {p['file']}:{p['i'] + 1} {p['kind']:5s} {p['desc'][:50]:50s} {p['result']} {p.get('verdicts')}", flush=True)
            results.append({k: v for k, v in p.items() if k != '_after'})
            stamp = time.strftime('%Y%m%d')
            json.dump(results, open(os.path.join(OUT, f'mutate-{SEED}-{stamp}.json'), 'w'), indent=1)
    finally:
        restore([p['file'] for p in plan])
        rebuild()
    from collections import Counter
    print('result:', dict(Counter(p.get('result') for p in results)))


if __name__ == '__main__':
    if '--dry' in args:
        b = bodies(); print(len(b), 'candidate bodies')
        for p in plant(N, SEED, KINDS):
            print(f"0x{p['va']:08X} {p['file']}:{p['line']} {p['kind']}: {p['before'][:70]}  =>  {p['after'][:70]}")
        if '--keep' not in args:
            restore([f for f in os.listdir(SRC) if f.endswith('.cpp')])
    else:
        main()
