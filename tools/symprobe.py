#!/usr/bin/env python3
"""Probes that drive a symgen-generated function down each of its paths.

symgen.py runs a function along every path; each path is a set of decisions
on tests (a field non-zero, a compare, an x87 compare). This tool picks, for
each path, values for what those tests read (this, the stack arguments, the
input registers, the memory words they load) and writes verify_multi probes
that set them: `va=X ecx=@S arg=.. poke=@S+2C:3F800000 ...`. Values are
searched from the constants the tests compare with (and their neighbours),
then checked by evaluating the path's tests on them; the run's range traces
(AVPE_PATHS=1) show which paths the probes really took.

`this` is the probe scratch block @S (pointers the function follows point
into it as well), so only functions that call nothing are probed this way:
a callee would run on the scratch.

    symprobe.py VA [VA ...]  > probes      one or more probes per path
    symprobe.py --skip-paths FILE VA ...    leave out paths already covered
                                            (FILE: lines 'VA j j ...')
"""
import os, random, re, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import symgen

PTR = 0x800                       # where pointers the function follows point (an @S offset)
FAKE = 0x10000000                 # @S as a number while searching
KEEP = 0x20000000                 # live mode: a pointer left as the game has it (not poked)

# live mode: a method that calls other code runs on a live object of its
# class (the last one that class's Create made, @C<Create>), at a frame of
# Marine 1; only the words its tests read are poked, pointers stay real
_creates = {}
for _va, _n in symgen._names.items():
    _m = re.match(r'Create__(\d+)(\w+)', _n)
    if _m:
        _creates[_m.group(2)[:int(_m.group(1))]] = _va


def live_objects(cls, limit=12):
    """Create VAs of cls and its descendants (an object of any will do)"""
    out, todo = [], [cls]
    while todo and len(out) < limit:
        c = todo.pop(0)
        if c in _creates:
            out.append(_creates[c])
        todo.extend(symgen._children.get(c, []))
    return out


class Skip(Exception):
    pass


DEBUG = {}                                    # va -> {path: why it got no probe}


class Retry(Exception):                       # these values do not work (a null pointer followed)
    pass


def paths_of(va):
    ab = symgen._abi.get('0x%08X' % va, {})
    entry = 'method' if 'ecx' in ab.get('regs', []) else 'free'
    return entry, symgen.paths(symgen.insns(va), entry, cls=symgen.method_class(va) if entry == 'method' else None)


def ro_word(a):
    b = symgen._rd(a, 4)
    return struct.unpack('<I', b)[0] if len(b) == 4 else None


def f2u(x):
    return struct.unpack('<I', struct.pack('<f', x))[0]


def u2f(u):
    return struct.unpack('<f', struct.pack('<I', u & 0xFFFFFFFF))[0]


class Model:
    """concrete values for the leaves a path's tests read"""

    def __init__(self, entry, obj='@S'):
        self.entry = entry
        self.obj = obj            # what this is: @S, or a live object @C<Create>
        self.vals = {}            # leaf key -> int (float leaves hold the bits)

    def addr(self, a):
        """an address value -> (probe syntax, concrete number) or Skip"""
        t = a[0]
        if t == 'k':
            return '%X' % a[1], a[1]
        if t == 'sym':
            n, off = a[1], a[2]
            if n == 'self':
                if self.obj != '@S':
                    if off < 0:
                        raise Skip('negative field')
                    return '%s+%X' % (self.obj, off), FAKE + off
                if off < 0 or off >= 0x1000:
                    raise Skip('field beyond the scratch')
                return '@S+%X' % off, FAKE + off
            base = self.leaf(('arg', int(n[3:])) if n.startswith('arg') else ('reg', n[3:]) if n.startswith('in_') else None)
            return self.ptr_syntax(base, off)
        if t == 'ld':
            v = self.load(a)
            return self.ptr_syntax(v, 0, '[%s]' % self.addr(a[3])[0])
        if t == 'plus':
            s, v = self.addr(a[1])
            if not s.startswith('[') and not s.startswith('@S'):
                raise Skip('address')
            return self.ptr_syntax(v, a[2], s)
        n = self.v(a)                                # computed (an index): where it lands in @S
        if FAKE <= n < FAKE + 0x1000:
            return '@S+%X' % (n - FAKE), n
        raise Skip('address %s' % (t,))

    def ptr_syntax(self, v, off, inner=None):
        if v is None:
            raise Skip('address from an unknown value')
        if inner is not None and inner.startswith('['):
            if self.obj != '@S' and '@C' in inner:
                # a word behind a pointer of a live object: if the object is
                # not live when the probe runs, the bridge still applies the
                # poke, through whatever word lies at the field's offset
                raise Skip('a poke behind a pointer of a live object')
            off &= 0xFFFFFFFF                        # [X]+FFFFFFFC: the word at X, minus 4
            if not (FAKE <= v < FAKE + 0x1000 or v == KEEP):
                raise Retry()
            return inner + ('+%X' % off if off else ''), (v + off) & 0xFFFFFFFF
        if FAKE <= v < FAKE + 0x1000:
            o = v - FAKE + off
            if not 0 <= o < 0x1000:
                raise Skip('beyond the scratch')
            return '@S+%X' % o, v + off
        raise Retry()                                # a null or a number used as an address

    def leaf(self, key):
        if key is None:
            return None
        return self.vals.get(key, 0)

    def load(self, a):
        """value of ('ld', k, w, addr)"""
        w, ad = a[2], a[3]
        if ad[0] == 'k' and symgen.readonly(ad[1]):
            return ro_word(ad[1]) & ((1 << 8 * w) - 1) if w < 4 else ro_word(ad[1])
        s, n = self.addr(ad)
        return self.vals.get(('mem', s), 0) & ((1 << 8 * w) - 1)

    def v(self, x):
        t = x[0]
        if t == 'k':
            return x[1]
        if t == 'sym':
            n = x[1]
            if n == 'self':
                return FAKE + x[2]
            if n.startswith('arg'):
                return (self.leaf(('arg', int(n[3:]))) + x[2]) & 0xFFFFFFFF
            if n.startswith('in_'):
                return (self.leaf(('reg', n[3:])) + x[2]) & 0xFFFFFFFF
            if n == 'build':
                return self.vals.get(('mem', '%X' % symgen.BUILD), 0)
            raise Skip('a test reads %s' % n)
        if t == 'ld':
            return self.load(x)
        if t == 'plus':
            return (self.v(x[1]) + x[2]) & 0xFFFFFFFF
        if t == 'lo':
            return self.v(x[2]) & ((1 << 8 * x[1]) - 1)
        if t == 'op':
            return symgen.op(x[1], ('k', self.v(x[2])), ('k', self.v(x[3])))[1]
        if t == 'un':
            return symgen.un(x[1], ('k', self.v(x[2])))[1]
        if t == 'sx':
            return symgen.sx(x[1], ('k', self.v(x[2])))[1]
        if t == 'ins':
            return symgen.ins(x[1], ('k', self.v(x[2])), ('k', self.v(x[3])))[1]
        raise Skip('value %s' % (t,))

    def f(self, e):
        t = e[0]
        if t == 'fld':
            ad = e[2]
            if ad[0] == 'k' and symgen.readonly(ad[1]):
                return u2f(ro_word(ad[1]))
            s, n = self.addr(ad)
            return u2f(self.vals.get(('mem', s), 0))
        if t == 'fild':
            return float(symgen.s32(self.load(('ld', e[1], 4, e[2]))))
        if t == 'fk':
            return e[1]
        if t == 'fop':
            a, b = self.f(e[2]), self.f(e[3])
            o = e[1]
            if o in ('fsubr', 'fdivr'):
                a, b = b, a
            try:
                return {'fadd': a + b, 'fsub': a - b, 'fsubr': a - b, 'fmul': a * b,
                        'fdiv': a / b, 'fdivr': a / b}[o]
            except ZeroDivisionError:
                return float('inf')
        if t == 'fun':
            return -self.f(e[2])
        if t == 'r32':
            return u2f(f2u(self.f(e[1]))) if abs(self.f(e[1])) < 3e38 else self.f(e[1])
        if t == 'fint':
            return float(symgen.s32(self.v(e[1])))
        if t == 'farg':
            return u2f(self.leaf(('arg', e[1])))
        raise Skip('x87 %s' % (t,))

    def holds(self, at):
        k = at[0]
        if k == 'nz':
            return self.v(at[1]) != 0
        if k == 'eq':
            return self.v(at[1]) == at[2]
        if k == 'rel':
            return symgen.rel_holds(at[1], self.v(at[2]), self.v(at[3]), at[4])
        if k == 'fgt':
            return self.f(at[1]) > self.f(at[2])
        if k == 'fcmp':
            a, b = self.f(at[2]), self.f(at[3])
            return {'>': a > b, '>=': a >= b, '<': a < b, '<=': a <= b, '==': a == b, '<>': a < b or a > b}[at[1]]
        raise Skip('test %s' % (k,))


def leaves(x, out, fl=False):
    """the leaves value x reads: ('mem', addr value, float?) ('arg', i) ('reg', r)"""
    t = x[0]
    if t == 'ld':
        out.append(('mem', x[3], fl or False, x[2]))
        leaves(x[3], out)
    elif t in ('fld', 'fild'):
        out.append(('mem', x[2], t == 'fld', 4))
        leaves(x[2], out)
    elif t == 'sym':
        if x[1].startswith('arg'):
            out.append(('arg', int(x[1][3:])))
        elif x[1].startswith('in_'):
            out.append(('reg', x[1][3:]))
        elif x[1] == 'build':
            out.append(('mem', ('k', symgen.BUILD), False, 4))
    elif t in ('plus', 'fint'):
        leaves(x[1], out)
    elif t in ('lo', 'un', 'sx', 'fun'):
        leaves(x[2], out, fl)
    elif t in ('op', 'ins', 'fop'):
        leaves(x[2], out); leaves(x[3], out)
    elif t == 'r32':
        leaves(x[1], out)
    elif t == 'farg':
        out.append(('farg', x[1]))


def atom_values(at):
    k = at[0]
    if k in ('nz',):
        return [at[1]]
    if k == 'eq':
        return [at[1], ('k', at[2])]
    if k == 'rel':
        return [at[2], at[3]]
    if k in ('fgt',):
        return [at[1], at[2]]
    if k == 'fcmp':
        return [at[2], at[3]]
    return []


def pointer_leaves(sim):
    """memory words and inputs the function uses as addresses (they must
    point into @S), and the largest offset it adds to them"""
    out = set()
    big = [0]

    def walk(a, off=0):
        if a[0] == 'ld':
            out.add(('ldaddr', a[3]))
            big[0] = max(big[0], off)
            walk(a[3])
        elif a[0] == 'plus':
            walk(a[1], off + a[2])
        elif a[0] == 'op':
            walk(a[2]); walk(a[3])
        elif a[0] == 'sym' and (a[1].startswith('arg') or a[1].startswith('in_')):
            out.add(a[1])
            big[0] = max(big[0], a[2])
    for lid, (pos, a, w) in sim.loads.items():
        walk(a)
    for key, kind, data in sim.s.effects:
        if kind == 'store':
            walk(data[0])
    return out, big[0]


def probes_for(va, want=None, per_path=3, tries=3000, seed=1, live=False, calls=False):
    """-> [(path index, probe line)]; live: on live objects of the method's
    class (functions that call other code)"""
    if live is True:
        cls = symgen.method_class(va)
        objs = live_objects(cls) if cls else []
        out = []
        for o in objs:
            out += probes_for(va, want, max(1, per_path // len(objs)) if len(objs) > 1 else per_path, tries, seed,
                              live='@C%X' % o)
        return out
    entry, ps = paths_of(va)
    if live and entry != 'method':
        return []
    rnd = random.Random(seed + va)
    out = []
    why = DEBUG.setdefault(va, {})
    for j, (d, sim, res) in enumerate(ps):
        if want is not None and j not in want:
            continue
        if not live and not calls and any(e[1] == 'call' for e in sim.s.effects):
            continue                              # (calls=True: callees run on the scratch too)
        atoms = sim.s.atoms
        dec = [(a, b) for a, b in d.items()]
        if any(a not in atoms and a != 'build' and a not in ('self', 'mem') for a, b in dec):
            continue                              # a test of a call result
        lv = []
        ptrs, big = pointer_leaves(sim)
        if big > 0xFF8:
            continue                              # a pointer's field would lie beyond the scratch
        # each pointer gets its own place in @S where it can (the code may
        # write through one and read through another)
        bases = [b for b in (0x200, 0x400, 0x600, 0x800, 0xA00, 0xC00) if b + big <= 0xFF8] or [(0xFF8 - big) & ~3]
        ptr = bases[0]
        null_self = ('self', False) in d.items()

        # leaves and the constants they meet
        consts, fconsts = {0, 1, 2, 0xFFFFFFFF, 0x7FFFFFFF, 0x80000000, 0x100}, {0.0, 1.0, -1.0, 0.5, 100.0, -100.0}
        for a, b in dec:
            if a == 'build':
                lv.append(('mem', ('k', symgen.BUILD), False, 4)); continue
            if a in ('self', 'mem'):
                continue
            for x in atom_values(atoms[a]):
                leaves(x, lv)
                for c in consts_of(x):
                    consts |= {c, (c + 1) & 0xFFFFFFFF, (c - 1) & 0xFFFFFFFF}
            if atoms[a][0] in ('fgt', 'fcmp'):
                for x in atom_values(atoms[a]):
                    for c in fconsts_of(x):
                        fconsts |= {c, c + 1.0, c - 1.0, c * 2.0, c / 2.0}
        for x in ptrs:                            # every pointer it follows gets a value (into @S)
            if x[0] == 'ldaddr':
                lv.append(('mem', x[1], False, 4))
            else:
                lv.append(('arg', int(x[3:])) if x.startswith('arg') else ('reg', x[3:]))
        for lid, (pos, a, w) in sim.loads.items():  # floats it reads: ordinary numbers, not NaN
            if not live and sim.ins[lid].mnemonic.startswith('f') and not sim.ins[lid].mnemonic.startswith('fi'):
                lv.append(('mem', a, 'benign', 4))
        # dedupe leaves by key (the tests' own leaves first)
        seen, uniq = set(), []
        for l in lv:
            key = (l[0], l[1]) if l[0] == 'mem' else l
            if key not in seen:
                seen.add(key); uniq.append(l)
        found = 0
        cond_keys = set()
        for a, b in dec:
            if a in atoms:
                tmp = []
                for x in atom_values(atoms[a]):
                    leaves(x, tmp)
                cond_keys |= {(l[0], l[1]) if l[0] == 'mem' else l for l in tmp}
        for t in range(tries):
            m = Model(entry, live or '@S')
            try:
                choice = []
                for l in sorted(uniq, key=depth):
                    if l[0] == 'mem':
                        s, n = m.addr(l[1])
                        if l[1][0] == 'k' and symgen.readonly(l[1][1]):
                            continue
                        isptr = ('ldaddr', l[1]) in ptrs
                        if isptr and live:
                            v = rnd.choice([0, KEEP, KEEP]) if ('mem', l[1]) in cond_keys else KEEP
                        elif isptr:
                            v = rnd.choice([0] + [FAKE + x for x in bases])
                        elif l[2] == 'benign':
                            if ('mem', s) in m.vals:
                                continue
                            v = f2u(rnd.choice([1.5, -2.25, 0.75, 3.0]))
                        elif l[2]:
                            v = f2u(rnd.choice(sorted(fconsts)))
                        else:
                            v = rnd.choice(sorted(consts))
                        if l[3] < 4:
                            v &= (1 << 8 * l[3]) - 1
                        m.vals[('mem', s)] = v
                        choice.append(('poke', s, v, l[3]))
                    elif l[0] in ('arg', 'reg', 'farg'):
                        key = ('arg', l[1]) if l[0] == 'farg' else l
                        if key in m.vals:
                            continue
                        name = ('arg%d' % l[1]) if l[0] != 'reg' else 'in_' + l[1]
                        if l[0] == 'farg':
                            v = f2u(rnd.choice(sorted(fconsts)))
                        elif name in ptrs and live:
                            raise Skip('a pointer argument on a live object')
                        elif name in ptrs:
                            v = rnd.choice([0] + [FAKE + x for x in bases])
                        else:
                            v = rnd.choice(sorted(consts))
                        m.vals[key] = v
                ok = True
                for a, b in dec:
                    if a == 'self':
                        continue                       # ecx=0 for the path where this is null
                    if a == 'build':
                        ok = ok and (m.vals.get(('mem', '%X' % symgen.BUILD), 0) != 0) == b
                        continue
                    if m.holds(atoms[a]) != b:
                        ok = False; break
                if not ok:
                    continue
            except Retry:
                why[j] = 'retry'
                continue
            except Skip as e:
                why[j] = 'skip: %s' % e
                break
            try:
                out.append((j, render(va, entry, m, choice, '0' if null_self else (live or '@S'))))
            except Skip as e:
                why[j] = 'render: %s' % e
                break
            found += 1
            if found >= per_path:
                break
    return out


def depth(l):
    """order of assignment: inputs first, then words by how many loads
    their address takes"""
    def dd(a):
        return 1 + dd(a[3]) if a[0] == 'ld' else dd(a[1]) if a[0] == 'plus' else 0
    return dd(l[1]) if l[0] == 'mem' else -1


def consts_of(x):
    out = []
    if x[0] == 'k':
        out.append(x[1])
    elif x[0] in ('op', 'ins'):
        out += consts_of(x[2]) + consts_of(x[3])
    elif x[0] in ('lo', 'un', 'sx'):
        out += consts_of(x[2])
    elif x[0] == 'plus':
        out.append((-x[2]) & 0xFFFFFFFF)
    return out


def fconsts_of(e):
    if e[0] == 'fld' and e[2][0] == 'k' and symgen.readonly(e[2][1]):
        v = u2f(ro_word(e[2][1]))
        return [v] if abs(v) < 1e30 else []
    if e[0] == 'fk':
        return [e[1]]
    if e[0] == 'fop':
        return fconsts_of(e[2]) + fconsts_of(e[3])
    return []


def val_syntax(v):
    if FAKE <= v < FAKE + 0x1000:
        return '@S+%X' % (v - FAKE)
    return '%X' % v


def render(va, entry, m, choice, obj='@S'):
    parts = ['va=%X' % va]
    if entry == 'method':
        parts.append('ecx=' + obj)
    for r in ('eax', 'edx', 'ebx', 'esi', 'edi'):
        if ('reg', r) in m.vals:
            parts.append('%s=%s' % (r, val_syntax(m.vals[('reg', r)])))
    if ('reg', 'ebp') in m.vals or ('reg', 'ecx') in m.vals:
        raise Skip('register')
    n = 1 + max([k[1] for k in m.vals if k[0] == 'arg'] + [-1] + list(symgen_args(va)))
    for i in range(n):
        parts.append('arg=%s' % val_syntax(m.vals.get(('arg', i), 0)))
    # pokes: dword at a time, narrower leaves merged into their word
    words = {}
    for kind, s, v, w in choice:
        if v == KEEP:
            continue                              # a live pointer: as the game has it
        if w == 4:
            words[s] = v
        else:
            mm = re.fullmatch(r'(.*?)\+?([0-9A-F]*)', s)
            base, off = (s, 0)
            m2 = re.fullmatch(r'(@S|\[.*\])\+([0-9A-F]+)', s)
            if m2:
                base, off = m2.group(1), int(m2.group(2), 16)
            elif re.fullmatch(r'[0-9A-F]+', s):
                base, off = '', int(s, 16)
            al = off & ~3
            key = (base + '+%X' % al) if base else '%X' % al
            if base == '@S' or base.startswith('['):
                key = base + ('+%X' % al if al else '') if base.startswith('[') else '@S+%X' % al
            words[key] = (words.get(key, 0) & ~(((1 << 8 * w) - 1) << 8 * (off & 3))) | (v << 8 * (off & 3))
    for s in sorted(words, key=lambda s: s.count('[')):
        parts.append('poke=%s:%s' % (s, val_syntax(words[s])))
    return ' '.join(parts)


def symgen_args(va):
    ab = symgen._abi.get('0x%08X' % va, {})
    return range(ab.get('pops', 0) // 4)


if __name__ == '__main__':
    argv = sys.argv[1:]
    skip = {}
    if '--skip-paths' in argv:
        i = argv.index('--skip-paths')
        for line in open(argv[i + 1]):
            p = line.split()
            if p:
                skip[int(p[0], 16)] = {int(x) for x in p[1:]}
        del argv[i:i + 2]
    for a in argv:
        va = int(a, 16)
        try:
            _, ps = paths_of(va)
            want = set(range(len(ps))) - skip.get(va, set())
            for j, line in probes_for(va, want):
                print(line)
        except (symgen.Reject, Skip) as e:
            print('# %X: %s' % (va, e), file=sys.stderr)


def infeasible(d, atoms):
    """True when the tests a path decides cannot all hold, proven: the tests
    are grouped by the value they compare with a constant; groups that share
    nothing are independent, and a group of compares of one value with
    constants holds somewhere only if it holds at one of the constants, their
    neighbours or the ends of the range (each compare splits the range at its
    constant), so trying those decides it. Anything else: not proven."""
    groups = {}                                       # value -> [(kind, rel, constant, width, holds?)]
    for a, b in d.items():
        at = atoms.get(a)
        if at is None:
            return False
        k = at[0]
        if k == 'nz':
            groups.setdefault(('i', at[1]), []).append(('!=', 0, 4, b))
        elif k == 'eq':
            groups.setdefault(('i', at[1]), []).append(('==', at[2], 4, b))
        elif k == 'rel' and at[3][0] == 'k':
            groups.setdefault(('i', at[2]), []).append((at[1], at[3][1], at[4], b))
        elif k == 'rel' and at[2][0] == 'k':
            flip = {'<s': '>s', '<=s': '>=s', '>s': '<s', '>=s': '<=s', '<u': '>u', '<=u': '>=u', '>u': '<u', '>=u': '<=u'}
            groups.setdefault(('i', at[3]), []).append((flip.get(at[1], at[1]), at[2][1], at[4], b))
        elif k in ('fgt', 'fcmp'):
            x, y = (at[1], at[2]) if k == 'fgt' else (at[2], at[3])
            rel = '>' if k == 'fgt' else at[1]
            c = fconst(y)
            if c is None:
                return False
            groups.setdefault(('f', x), []).append((rel, c, 0, b))
        else:
            return False
    # independence: the groups' values must not share a load
    seen = set()
    for key in groups:
        ls = set()
        loads_in(key[1], ls)
        if ls & seen:
            return False
        seen |= ls
    for key, cs in groups.items():
        if key[0] == 'i':
            pts = {0, 1, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF, 0x7F, 0x80, 0xFF, 0x7FFF, 0x8000, 0xFFFF}
            for rel, c, w, b in cs:
                pts |= {c & 0xFFFFFFFF, (c + 1) & 0xFFFFFFFF, (c - 1) & 0xFFFFFFFF}
            if not any(all(symgen.rel_holds(rel, p, c, w) == b for rel, c, w, b in cs) for p in pts):
                return True
        else:
            import math
            pts = {0.0, -0.0, float('inf'), -float('inf'), float('nan')}
            for rel, c, w, b in cs:
                pts |= {c, math.nextafter(c, math.inf), math.nextafter(c, -math.inf)}

            def fh(rel, p, c):
                return {'>': p > c, '>=': p >= c, '<': p < c, '<=': p <= c, '==': p == c, '<>': p < c or p > c}[rel]
            if not any(all(fh(rel, p, c) == b for rel, c, w, b in cs) for p in pts):
                return True
    return False


def fconst(e):
    if e[0] == 'fld' and e[2][0] == 'k' and symgen.readonly(e[2][1]):
        return u2f(ro_word(e[2][1]))
    if e[0] == 'fk':
        return e[1]
    return None


def loads_in(x, out):
    if not isinstance(x, tuple) or not x:
        return
    if x[0] in ('ld', 'fld', 'fild'):
        out.add(x[1])
    for y in x[1:]:
        if isinstance(y, tuple):
            loads_in(y, out)
