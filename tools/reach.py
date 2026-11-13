#!/usr/bin/env python3
"""reach.py classify [VA ...]: why a function is never reached in our runs.

For each target (default: every pending_ / unverified function in src/), walks
the reference graph upward until it meets code that our runs DO execute:

  edges   direct call / jmp rel32 into the target (the containing function is
          the parent); an absolute reference in a vtable (the class is the
          parent, and the class's parents are its Create and constructor);
          any other absolute reference (a function-pointer table, push
          offset: the containing function, or 'data' outside .text)
  reached a function with a natural-call record in verified.json (any run
          executed it), or a census entry (some test config called it)

Output per target: the shortest chain to a reached ancestor (what the run
must do differently: take a branch, make an object), or why there is none:
'dead' (nothing references it), 'class never made' (only vtables of
classes nothing creates), 'only from unreached' (every chain dies out).
"""
import collections, csv, glob, json, re, struct, sys

R = '/home/claude/avpe'
D = '/home/claude/avpe-decomp'
info = json.load(open(R + '/xbe_info.json'))
xbe = open(R + '/disc/default.xbe', 'rb').read()
funcs = sorted((int(f['start'], 16), int(f['size'])) for f in
               json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json')))
starts = [a for a, _ in funcs]
import bisect


def owner(va):
    i = bisect.bisect_right(starts, va) - 1
    if i >= 0 and funcs[i][0] <= va < funcs[i][0] + funcs[i][1]:
        return funcs[i][0]
    return None


secs = [(s['va'], s['raw'], s['rsize'], s['name'].strip()) for s in info['sections']]


def va_of_file(off):
    for va, raw, n, name in secs:
        if raw <= off < raw + n:
            return va + off - raw, name
    return None, None


text = [s for s in secs if s[3] == '.text'][0]

# class vtables: address, slots, class name; Create / ctor per class
vt = []
cls_create = {}
cur = None
for line in open(D + '/include/avpe/class_index.h'):
    m = re.match(r'// RTTI vtable 0x([0-9A-F]+) \((\d+) slots\)', line)
    if m:
        cur = [int(m.group(1), 16), int(m.group(2)), None]
        vt.append(cur)
        continue
    m = re.match(r'/\* (\w+)', line)
    if m and cur and cur[2] is None:
        cur[2] = m.group(1)
    m = re.match(r' \*   0x([0-9A-F]+)  Create\(', line)
    if m and cur:
        cls_create[cur[2]] = int(m.group(1), 16)


def in_vtable(va):
    for a, n, c in vt:
        if a <= va < a + 4 * n:
            return c
    return None


db = {int(r['xbox'], 16): r['name'] for r in csv.DictReader(open(R + '/symbols/xbox_symbols.csv'))}
ctors = collections.defaultdict(set)                     # class -> constructor VAs (by mangled name __ct__<n>Class)
for va, n in db.items():
    m = re.match(r'^__ct__(\d+)(\w+)', n)
    if m:
        ctors[m.group(2)[:int(m.group(1))]].add(va)

# reached: natural-call records or census
vj = json.load(open(D + '/verified.json'))
reached = {int(k, 16) for k, v in vj.items() if any('natural' in r.get('check', '') for r in v)}
census = json.load(open(R + '/symbols/census.json'))
reached |= {int(k, 16) for k, v in census.items() if v}

# reverse edges, built once over the whole image
refs = collections.defaultdict(set)      # target -> {('fn', va) | ('class', name) | ('data', va)}
tva, traw, tn, _ = text
code = xbe[traw:traw + tn]
for i in range(len(code) - 5):
    if code[i] in (0xE8, 0xE9):
        tgt = tva + i + 5 + struct.unpack_from('<i', code, i + 1)[0]
        o = owner(tva + i)
        if o is not None and o != tgt:
            refs[tgt].add(('fn', o))
fs = set(starts)
for off in range(0, len(xbe) - 4):
    w = struct.unpack_from('<I', xbe, off)[0]
    if w in fs and 0x11000 <= w < 0x170D60:
        va, sec = va_of_file(off)
        if va is None:
            continue
        c = in_vtable(va)
        if c:
            refs[w].add(('class', c))
        elif sec == '.text':
            o = owner(va)
            if o is not None and o != w:
                refs[w].add(('fn', o))
        else:
            refs[w].add(('data', va))


def class_parents(c):
    out = set()
    for v in [cls_create.get(c)] + sorted(ctors.get(c, ())):
        if v:
            out.add(('fn', v))
    return out


def name(node):
    k, v = node
    if k == 'fn':
        return '%08X %s' % (v, db.get(v, '')[:50])
    if k == 'class':
        return 'class ' + v
    return 'data@%08X' % v


def classify(t):
    start = ('fn', t)
    prev = {start: None}
    q = collections.deque([start])
    while q:
        n = q.popleft()
        if n != start and n[0] == 'fn' and n[1] in reached:
            chain = []
            while n:
                chain.append(name(n)); n = prev[n]
            return 'reachable', chain
        if n[0] == 'fn':
            ps = refs.get(n[1], set())
        elif n[0] == 'class':
            ps = class_parents(n[1])
        else:
            ps = set()
        for p in ps:
            if p not in prev:
                prev[p] = n; q.append(p)
    direct = refs.get(t, set())
    if not direct:
        return 'dead', []
    kinds = sorted({name(p) for p in direct})
    if any(p[0] == 'data' for p in direct):
        return 'data-referenced', kinds
    if all(p[0] == 'class' for p in direct):
        return 'class never made', kinds
    return 'only from unreached', kinds


def targets():
    out = []
    for f in glob.glob(D + '/src/*.cpp'):
        out += [int(m, 16) for m in re.findall(r'void pending_([0-9A-F]{8})\(void\)', open(f).read())]
    sys.path.insert(0, D + '/tools')
    return sorted(set(out) | {0x44ED0, 0x7C890, 0x7D150, 0xF09C0, 0x67D40, 0x133B50, 0x133B60})


if __name__ == '__main__':
    if len(sys.argv) < 2 or sys.argv[1] != 'classify':
        sys.exit(__doc__)
    ts = [int(a, 16) for a in sys.argv[2:]] or targets()
    summary = collections.Counter()
    for t in ts:
        kind, info_ = classify(t)
        summary[kind] += 1
        print('%08X %-40s %s' % (t, db.get(t, '')[:40], kind))
        for line in info_[:8]:
            print('          ' + line)
    print(dict(summary))
