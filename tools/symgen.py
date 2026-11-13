"""Small functions as C++, by symbolic execution of their machine code.

classgen.py writes the class boilerplate from fixed shapes. Many functions
are the same boilerplate with something extra compiled in: a Create whose
constructor the compiler inlined (stores, a call, a test of the class-table
build flag), a constructor that is GObject's inlined constructor plus a few
fields, a static initialiser that copies globals. This module runs such a
function symbolically along every path (forward branches only, at most a few
tests), records what it does (calls with the registers and stack arguments
the callee reads, stores, the value it returns), and writes that as straight
C++ statements, each under the condition of the paths that execute it.

It accepts only what it can model exactly: mov/lea/xor/push/pop/test/cmp,
dword/word/byte stores and read-modify-writes, direct calls (abi.json gives
pops and register inputs), virtual calls (thiscall: they pop what was pushed
for them), x87 loads, arithmetic, stores and compares. Anything else, a loop,
an input it cannot name, or a register a callee reads that it cannot know,
rejects the function.

    create(va)                  a Create: the construction, as a lambda body
    plain(va, entry, returns)   entry 'method' (this in ecx), 'free' or 'sinit'
Both raise Reject with the reason when the function is not one it models.

The long tail (classgen's symgen_tail.txt, plain(..., exact=True)) adds, each
modelled exactly or rejected:
- inputs in any register (in_eax = reg(EAX) ...), byte and word arguments;
- partial registers (al, ax: the rest of the register kept), movzx/movsx,
  register arithmetic (add sub and or xor shl shr sar imul inc dec neg not,
  cdq), indexed addresses;
- every condition after cmp/test/sub/and/or (signed and unsigned), setcc,
  sbb r,r; x87 compares through fnstsw/test ah/je-jne-jp-jnp as one C++
  compare each (truth table over >, <, ==, unordered); x87 register-stack
  operations; a float result in st0;
- results that differ between paths (result = ... under each path's tests),
  bool results in al (ret_al when the rest of eax is what the guest eax holds);
  with exact=True eax is always returned as the original leaves it (callers
  may read it), or the function is rejected;
- tail calls (jmp to a function: its arguments are this one's);
- cdecl calls whose `add esp` comes later (or whose callee reads no stack
  arguments, stack_args()), virtual calls whose argument count the class
  hierarchy of `this` gives, or which one count keeps the stack consistent;
- memory mode: a function that takes the address of its stack keeps its
  stack slots in guest memory at sp + offset (sp = esp at entry), as the
  original does, with GuestFrame placing the callees' frames;
- a check that every instruction the code can reach is on some path.
Not modelled (rejected): loops, indirect jumps, string instructions, x87
values live across a call, the bits of a float used as an integer.
"""
import csv, json, os, re
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_info = json.load(open(os.path.join(R, 'xbe_info.json')))
_xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
_abi = json.load(open(os.path.join(R, 'symbols', 'abi.json')))
_FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
BUILD, OP_NEW = 0x26B410, 0xD6CB0
_names = {int(r['xbox'], 16): r['name'] for r in csv.DictReader(open(os.path.join(R, 'symbols', 'xbox_symbols.csv'))) if r['name'] and r['tier'] in ('A', 'B', 'C')}
_vtables = {}
for _c, _e in json.load(open(os.path.join(R, 'symbols', 'classes.json'))).items():
    for _t in _e['vtables']:
        _vtables[int(_t['va'], 16)] = (_c, _t['offset'])
# What a virtual call pops, by vtable slot: the pops of every function any
# class's primary vtable has in that slot, when they all agree (thiscall
# methods pop their stack arguments, so that is how many the call passes)
_slot_pops = {}
_classes = json.load(open(os.path.join(R, 'symbols', 'classes.json')))
_children = {}
for _c, _e in _classes.items():
    _children.setdefault(_e.get('parent'), []).append(_c)
    for _t in _e['vtables']:
        for _j, _f in enumerate(_t['slots']):
            _a = _abi.get('0x%08X' % int(_f, 16))
            _slot_pops.setdefault(4 * _j, set()).add(_a['pops'] if _a else None)


def class_slot_pops(cls, slot):
    """the pops of the functions in `slot` of the primary vtables of cls and
    every class derived from it (a virtual call on `this` in a method of cls)"""
    out, todo = set(), [cls]
    while todo:
        c = todo.pop()
        for t in _classes.get(c, {}).get('vtables', []):
            if t['offset'] == 0 and 4 * len(t['slots']) > slot:
                a = _abi.get('0x%08X' % int(t['slots'][slot // 4], 16))
                out.add(a['pops'] if a else None)
        todo.extend(_children.get(c, []))
    return out


def method_class(va):
    n = _names.get(va)
    try:
        from cwdemangle import demangle
        d = demangle(n) if n else None
        return d[0] if d and d[0] in _classes else None
    except Exception:
        return None


def fname(va):
    """readable name of a function, or None"""
    n = _names.get(va)
    if not n:
        return None
    try:
        from cwdemangle import demangle
        d = demangle(n)
        if d:
            cls, meth, args, const = d
            return ('%s::' % cls if cls else '') + '%s(%s)' % ('~' + cls if meth == '__dt' else meth, ', '.join(args))
    except Exception:
        pass
    return n


_stack_args = {}


def stack_args(va):
    """how many stack arguments a function reads (its highest [esp+N] /
    [ebp+N] argument slot, along its code), or None when that is not clear
    (the address of a slot taken, a jump elsewhere, an unknown function)"""
    if va in _stack_args:
        return _stack_args[va]
    f = _FUNCS.get(va)
    out = None
    if f:
        depth, fp, mx, at, ok = 0, None, 0, {}, True
        pending = 0                                    # pops right before a ret: undone after it
        for x in _md.disasm(_rd(va, f['size']), va):
            if x.address in at:
                depth = at[x.address]
            m, o = x.mnemonic, x.op_str
            if m == 'lea' and o in ('esp, [esp]', 'ebp, [ebp]'):
                continue                                   # padding
            if m == 'lea' and (re.search(r'\[esp', o) or (fp is not None and re.search(r'\[ebp', o))):
                ok = False; break
            for mm in re.finditer(r'\[esp(?: \+ (0x[0-9a-f]+|\d+))?\]', o):
                rel = (num(mm.group(1)) if mm.group(1) else 0) - depth
                if rel >= 4:
                    mx = max(mx, (rel - 4) // 4 + 1)
            for mm in re.finditer(r'\[ebp \+ (0x[0-9a-f]+|\d+)\]', o):
                if fp is None:
                    continue                               # ebp as an ordinary register
                rel = num(mm.group(1)) - fp
                if rel >= 4:
                    mx = max(mx, (rel - 4) // 4 + 1)
            if m == 'mov' and o == 'ebp, esp':
                fp = depth
            if m == 'push':
                depth += 4; pending = 0
            elif m == 'pop':
                depth -= 4; pending += 4
            elif m == 'sub' and o.startswith('esp, '):
                depth += num(o.split(', ')[1]); pending = 0
            elif m == 'add' and o.startswith('esp, '):
                depth -= num(o.split(', ')[1]); pending = 0
            elif m == 'ret':
                depth += pending; pending = 0
            elif m == 'jmp' or m in JCC:
                if not re.fullmatch(r'0x[0-9a-f]+', o):
                    ok = False; break
                t = num(o)
                if not va <= t < va + f['size']:
                    # a tail jump: the target reads this function's arguments
                    _stack_args[va] = None                 # (recursion guard)
                    sub = stack_args(t) if m == 'jmp' and depth == 0 else None
                    if sub is None:
                        ok = False; break
                    mx = max(mx, sub)
                    depth += pending; pending = 0
                    continue
                at.setdefault(t, depth)
                if m == 'jmp':
                    depth += pending
                pending = 0
            elif re.search(r'\besp\b', o) and m not in ('call',):
                if not re.search(r'\[esp', o):
                    ok = False; break
                pending = 0
            else:
                pending = 0
        out = mx if ok else None
    _stack_args[va] = out
    return out


def vtable_name(c):
    t = _vtables.get(c)
    return ('%s vtable' % t[0]) if t and t[1] == 0 else None
REGS32 = ['eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp']
LOW8 = {'al': 'eax', 'cl': 'ecx', 'dl': 'edx', 'bl': 'ebx'}
LOW16 = {'ax': 'eax', 'cx': 'ecx', 'dx': 'edx', 'bx': 'ebx', 'si': 'esi', 'di': 'edi'}
HIGH8 = {'ah': 'eax', 'ch': 'ecx', 'dh': 'edx', 'bh': 'ebx'}
REGW = dict([(r, 4) for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')] + [(r, 1) for r in LOW8] + [(r, 2) for r in LOW16])
NEW = ('op', 'un', 'ins', 'sx')
JCC = {'je', 'jne', 'jl', 'jle', 'jg', 'jge', 'jb', 'jbe', 'ja', 'jae', 'js', 'jns', 'jp', 'jnp'}
ARITH = {'add', 'sub', 'and', 'or', 'xor', 'shl', 'sal', 'shr', 'sar', 'inc', 'dec', 'neg', 'not', 'imul'}
FOPS = {m: m for m in ('fadd', 'fsub', 'fmul', 'fdiv', 'fsubr', 'fdivr')}
FOPS.update({'fi' + m[1:]: m for m in FOPS})
# x87 compare outcomes (st0 > src, <, ==) -> the C++ compare that is true in
# exactly those (C0 is st0 < src, C3 equal, C0 = C2 = C3 = 1 unordered)
FREL = {(1, 0, 0): '>', (1, 0, 1): '>=', (0, 1, 0): '<', (0, 1, 1): '<=', (0, 0, 1): '==', (1, 1, 0): '<>'}


def opwidth(o):
    m = re.match(r'(byte|word|dword) ptr', o)
    if m:
        return WIDTH[m.group(1)]
    return REGW.get(o, 1 if o in HIGH8 else 4)
WIDTH = {'byte': 1, 'word': 2, 'dword': 4}
ACC = {1: 'm8', 2: 'm16', 4: 'm32'}


def _rd(va, n):
    for s in _info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return _xbe[o:o + n]
    return b''


def readonly(va):
    return any(x['va'] <= va < x['va'] + x['vsize'] and not x['flags'] & 1 for x in _info['sections'])


def insns(va):
    f = _FUNCS.get(va)
    return list(_md.disasm(_rd(va, f['size']), va)) if f else []


class Reject(Exception):
    pass


class Fork(Exception):
    def __init__(self, atom):
        self.atom = atom


class ForkN(Exception):
    """a choice among counts (a virtual call's arguments): each is tried, and
    exactly one may run to completion"""
    def __init__(self, name, opts):
        self.name, self.opts = name, opts


def num(s):
    return int(s, 0)


# ---------------------------------------------------------------- values
# ('k', c)                     constant
# ('sym', name, off)           a named input or result, plus an offset:
#                              mem (Create's new memory), self (ecx at entry),
#                              build (the class-table build flag), rN (call result)
# ('ld', id, w, addr)          a memory load (id = instruction index); the
#                              value as of that point
# ('lo', w, v)                 the low byte/word of v
# ('f', expr)                  an x87 value: ('fld', id, addr) ('fild', id, addr) ('fop', op, a, b)
# ('unk', why)                 a value it cannot name (any use rejects)
def add(v, off):
    if v[0] == 'k':
        return ('k', (v[1] + off) & 0xFFFFFFFF)
    if v[0] == 'sym':
        return ('sym', v[1], v[2] + off)
    if off == 0:
        return v
    if v[0] == 'plus':
        return add(v[1], v[2] + off)
    return ('plus', v, off)


# Values the long-tail extensions add (none of them occur in the shapes above):
# ('op', o, a, b)  32-bit a o b, o in + - * & | ^ << >> >>s (arithmetic shift)
# ('un', o, a)     -a or ~a
# ('ins', w, x, v) x with its low w bytes replaced by v (a write to al, ax...)
# ('sx', w, v)     the low w bytes of v, sign-extended
# x87 expressions: ('fk', c) a constant (fld1, fldz), ('fun', 'chs', e),
# ('r32', e) e rounded to float (stored to a stack slot and reloaded),
# ('fint', v) an integer value loaded with fild, ('farg', i) a float argument
M32 = 0xFFFFFFFF


def s32(c):
    c &= M32
    return c - (1 << 32) if c >> 31 else c


def lo(w, v):
    """the low w bytes of v, zero-extended"""
    if w == 4:
        return v
    if v[0] == 'k':
        return ('k', v[1] & ((1 << 8 * w) - 1))
    if v[0] == 'ins' and w <= v[1]:
        return lo(w, v[3])
    if (v[0] == 'ld' and v[2] <= w) or (v[0] == 'lo' and v[1] <= w):
        return v
    if v[0] == 'lo':
        return ('lo', w, v[2])
    return ('lo', w, v)


def op(o, a, b):
    if a[0] == 'k' and b[0] == 'k':
        x, y = a[1], b[1]
        r = {'+': x + y, '-': x - y, '&': x & y, '|': x | y, '^': x ^ y, '*': x * y,
             '<<': x << (y & 31), '>>': x >> (y & 31), '>>s': s32(x) >> (y & 31)}[o]
        return ('k', r & M32)
    if o == '+' and a[0] == 'k':
        a, b = b, a
    if o in ('+', '-') and b[0] == 'k':
        c = s32(b[1] if o == '+' else -b[1])
        return add(a, c) if a[0] in ('sym', 'plus') or c == 0 else ('plus', a, c)
    if o in ('&', '|') and a == b:
        return a
    if (o == '&' and b == ('k', M32)) or (o in ('|', '^', '<<', '>>', '>>s') and b == ('k', 0)):
        return a
    if o in ('&', '*') and b == ('k', 0):
        return ('k', 0)
    if o in ('^', '-') and a == b:
        return ('k', 0)
    return ('op', o, a, b)


op_ = op


def un(o, a):
    if a[0] == 'k':
        return ('k', (-a[1] if o == '-' else ~a[1]) & M32)
    return ('un', o, a)


def sx(w, v):
    if v[0] == 'k':
        b = 8 * w
        c = v[1] & ((1 << b) - 1)
        return ('k', (c - (1 << b) if c >> (b - 1) else c) & M32)
    return ('sx', w, v)


def rel_holds(rel, x, y, w):
    b = 8 * w
    x &= (1 << b) - 1; y &= (1 << b) - 1
    sx_, sy = (x - (1 << b) if x >> (b - 1) else x), (y - (1 << b) if y >> (b - 1) else y)
    return {'==': x == y, '!=': x != y, '<s': sx_ < sy, '<=s': sx_ <= sy, '>s': sx_ > sy, '>=s': sx_ >= sy,
            '<u': x < y, '<=u': x <= y, '>u': x > y, '>=u': x >= y}[rel]


NEG = {'==': '!=', '!=': '==', '<s': '>=s', '>=s': '<s', '<=s': '>s', '>s': '<=s',
       '<u': '>=u', '>=u': '<u', '<=u': '>u', '>u': '<=u'}


def ins(w, x, v):
    v = lo(w, v)
    if x[0] == 'k' and v[0] == 'k':
        return ('k', (x[1] & ~((1 << 8 * w) - 1) & M32) | v[1])
    return ('ins', w, x, v)


class State:
    def __init__(self):
        self.r = {}
        self.stack = {}            # entry-relative offset -> value
        self.esp = 0
        self.fp = []               # x87 stack, top last
        self.flags = None
        self.effects = []          # (key, kind, data)
        self.fresh = set()         # registers written since the last call
        self.pushes = []           # esp offsets of pushes since the last call
        self.decided = {}
        self.atoms = {}
        self.flags2 = None         # the long-tail flags model (see Sim.cond)
        self.last_res = None       # the last call's result: what the guest eax holds after it
        self.argw = False          # wrote one of its own stack arguments
        self.slotfx = {}           # memory mode: stack offset -> the effect that last stored there
        self.dead = set()          # effects not written out (a slot a gcall writes anyway)


class Sim:
    """One run along the path the decisions select."""

    cls = None                     # the class of `this` (a method's symbol), for virtual calls

    def __init__(self, ins, entry, decided):
        self.ins = ins
        self.at = {i.address: k for k, i in enumerate(ins)}
        self.entry = entry
        self.s = State()
        self.s.decided = decided
        s = self.s
        for r in REGS32:
            s.r[r] = ('sym', 'in_' + r, 0)
        if entry == 'method':
            s.r['ecx'] = ('sym', 'self', 0)
        s.stack[0] = ('sym', 'retaddr', 0)
        self.visited = []          # the instructions this path runs (path coverage)
        # memory mode: the function takes the address of its stack (a local
        # handed to a callee, a frame pointer): its stack slots are then
        # guest memory at sp + offset (sp = esp at entry), written and read
        # there as the original does
        self.mem = any((x.mnemonic == 'lea' and '[esp' in x.op_str and x.op_str != 'esp, [esp]') or
                       (x.mnemonic in ('mov', 'push') and re.search(r'(^|, )esp$', x.op_str)) for x in ins)
        self.loads = {}            # id -> (effect position, addr, width)
        self.uses = []             # (load id, effect position at use)

    # ---- operands
    def val(self, op, k):
        s = self.s
        op = op.strip()
        if op in s.r:
            return s.r[op]
        if op in LOW8 or op in LOW16:
            w, v = (1, s.r[LOW8[op]]) if op in LOW8 else (2, s.r[LOW16[op]])
            return lo(w, v) if v[0] in NEW else ('lo', w, v)
        if op in HIGH8:
            v = s.r[HIGH8[op]]
            if v[0] == 'unk':
                return v
            return lo(1, op_('>>', v, ('k', 8)))
        if op == 'esp' and self.mem:
            return ('sym', 'sp', s.esp)
        if re.fullmatch(r'-?(0x[0-9a-f]+|\d+)', op):
            return ('k', num(op) & 0xFFFFFFFF)
        m = re.fullmatch(r'(byte|word|dword) ptr \[(.*)\]', op)
        if m:
            w = WIDTH[m.group(1)]
            a = self.addr(m.group(2))
            return self.load(a, w, k)
        raise Reject('operand %s' % op)

    def addr(self, inner):
        s = self.s
        m = re.fullmatch(r'(e[a-z]{2})(?: ([+-]) (0x[0-9a-f]+|\d+))?', inner)
        if m:
            off = num(m.group(3)) if m.group(3) else 0
            if m.group(2) == '-':
                off = -off
            if m.group(1) == 'esp':
                return ('stk', s.esp + off)
            if m.group(1) not in s.r:
                raise Reject('base %s' % m.group(1))
            return add(s.r[m.group(1)], off)
        m = re.fullmatch(r'(0x[0-9a-f]+|\d+)', inner)
        if m:
            return ('k', num(m.group(1)))
        # base + index*scale + displacement (the long tail)
        m = re.fullmatch(r'(?:(e[a-z]{2}) \+ )?(e[a-z]{2})(?:\*([1248]))?(?: ([+-]) (0x[0-9a-f]+|\d+))?', inner)
        if m and 'esp' not in (m.group(1), m.group(2)):
            for r in (m.group(1), m.group(2)):
                if r and r not in s.r:
                    raise Reject('base %s' % r)
            ix = s.r[m.group(2)]
            if m.group(3) and m.group(3) != '1':
                ix = op('*', ix, ('k', int(m.group(3))))
            a = op('+', s.r[m.group(1)], ix) if m.group(1) else ix
            if m.group(5):
                a = op('+' if m.group(4) == '+' else '-', a, ('k', num(m.group(5))))
            return a
        raise Reject('address [%s]' % inner)

    def load(self, a, w, k):
        s = self.s
        if a[0] == 'stk' and self.mem:
            a = ('sym', 'sp', a[1])                # memory mode: the slot as memory
        if a[0] == 'stk':
            off = a[1]
            if w != 4:
                # a narrow read of a slot (a bool or char argument, a byte
                # the function stored there): its low bytes
                if off % 4:
                    raise Reject('narrow stack load')
                v = s.stack[off] if off in s.stack else \
                    ('sym', 'arg%d' % ((off - 4) // 4), 0) if off >= 4 else None
                if v is None or v[0] == 'f':
                    raise Reject('narrow stack load')
                return lo(w, v)
            if off in s.stack:
                if s.stack[off][0] == 'f':
                    return ('fbits', s.stack[off][1])   # good only to be stored again whole
                return s.stack[off]
            if off >= 4 and off % 4 == 0 and off >= 4:
                return ('sym', 'arg%d' % ((off - 4) // 4), 0)
            raise Reject('stack load %d' % off)
        if a == ('k', BUILD) and w == 4:
            return ('sym', 'build', 0)
        self.check_addr(a)
        self.loads[k] = (len(s.effects), a, w)
        return ('ld', k, w, a)

    def check_addr(self, a):
        if a[0] == 'unk' or (a[0] == 'sym' and a[1] in ('retaddr',)):
            raise Reject('address from %s' % (a,))

    def use(self, v):
        """a value is used now: loads it reads must still hold"""
        if v[0] == 'ld':
            self.uses.append((v[1], len(self.s.effects)))
            self.use(v[3])
        elif v[0] in ('plus', 'lo'):
            self.use(v[1] if v[0] == 'plus' else v[2])
        elif v[0] in ('op', 'ins'):
            self.use(v[2]); self.use(v[3])
        elif v[0] in ('un', 'sx'):
            self.use(v[2])
        elif v[0] == 'f':
            self.usef(v[1])
        elif v[0] == 'unk':
            raise Reject('uses an unknown value (%s)' % v[1])
        elif v[0] == 'fbits':
            raise Reject('uses the bits of a float it stored')
        elif v[0] == 'sym' and v[1] in ('retaddr',):
            raise Reject('uses the return address')
        return v

    def usef(self, e):
        if e[0] in ('fld', 'fild'):
            self.uses.append((e[1], len(self.s.effects)))
            self.use(e[2])
        elif e[0] == 'fop':
            self.usef(e[2]); self.usef(e[3])
        elif e[0] in ('fun', 'r32'):
            self.usef(e[-1])
        elif e[0] == 'fint':
            self.use(e[1])

    def store(self, a, w, v, op, k, sym_only=False):
        s = self.s
        if v[0] == 'fbits' and w == 4 and op == '=':
            v = ('f', v[1])                            # a float copied through a register
        if a[0] == 'stk' and self.mem and not sym_only:
            off = a[1]
            try:
                self.store(a, w, v, op, k, sym_only=True)
            except Reject:
                s.stack[off] = ('unk', 'stack slot')
            a = ('sym', 'sp', off)
            self.check_addr(a); self.use(v)
            s.effects.append((k, 'store', (a, w, v, op)))
            s.slotfx[off] = len(s.effects) - 1
            return
        if a[0] == 'stk':
            off = a[1]
            if off > 0:
                s.argw = True
            if w != 4 or op != '=':
                # the long tail: a narrow store or a read-modify-write of a slot
                if off % 4 or v[0] == 'f':
                    raise Reject('stack store')
                old = s.stack[off] if off in s.stack else ('sym', 'arg%d' % ((off - 4) // 4), 0) if off >= 4 else None
                if old is None or old[0] == 'f':
                    raise Reject('stack store')
                if op != '=':
                    v = op_({'|=': '|', '&=': '&', '+=': '+', '-=': '-', '^=': '^'}[op], lo(w, old), v)
                v = ins(w, old, v) if w < 4 else v
            s.stack[off] = v
            return
        if v[0] == 'fbits' and w == 4 and op == '=':
            v = ('f', v[1])                            # a float copied through a register
        self.check_addr(a)
        self.use(a)
        self.use(v)
        s.effects.append((k, 'store', (a, w, v, op)))

    def setreg(self, r, v):
        s = self.s
        if r in LOW8 or r in LOW16:
            # al, ax...: the register's other bytes keep their value
            full = LOW8.get(r) or LOW16[r]
            if s.r[full][0] == 'unk' or v[0] == 'unk':
                raise Reject('partial register write %s' % r)
            s.r[full] = ins(1 if r in LOW8 else 2, s.r[full], v)
            s.fresh.add(full)
            return
        if r not in s.r:
            raise Reject('register %s' % r)
        s.r[r] = v
        s.fresh.add(r)

    # ---- conditions
    def atom_of(self, v, k):
        """the boolean 'v != 0' as (atom name, is it already known)"""
        if v[0] == 'k':
            return None, v[1] != 0
        if v[0] == 'sym':
            if v[1] == 'build':
                return 'build', None
            if v[1] in ('mem', 'self') and v[2] == 0:
                return v[1], None
            if v[1] in ('mem', 'self'):
                return None, True
            if re.fullmatch(r'r[0-9A-F]+', v[1]) and v[2] == 0:
                return v[1], None
            raise Reject('tests %s' % (v,))
        if v[0] == 'ld':
            self.use(v)
            self.s.atoms['L%d' % v[1]] = ('nz', v)
            return 'L%d' % v[1], None
        raise Reject('tests %s' % (v,))

    def decide(self, atom):
        if atom not in self.s.decided:
            raise Fork(atom)
        return self.s.decided[atom]

    def zf(self):
        """ZF: True if the last test/cmp found equal / zero"""
        f = self.s.flags
        if f is None:
            raise Reject('flags')
        kind = f[0]
        if kind == 'const':
            return f[1]
        if kind == 'nz':           # ZF = !(atom)
            a, known = f[1], f[2]
            if a is None:
                return not known
            return not self.decide(a)
        if kind == 'eq':           # ZF = atom
            return self.decide(f[1])
        raise Reject('flags %s' % (kind,))

    # ---- conditions, the long-tail model: s.flags2 is what the last flag-
    # setting instruction compared, ('cmp', a, b, w) (cmp a, b or sub: every
    # condition), ('logic', v, w) (test v, v / and / or / xor: CF = OF = 0),
    # ('arith', v, w) (add, inc, dec, neg: ZF and SF only)
    CC = {'e': '==', 'ne': '!=', 'l': '<s', 'le': '<=s', 'g': '>s', 'ge': '>=s',
          'b': '<u', 'be': '<=u', 'a': '>u', 'ae': '>=u', 's': 'S', 'ns': 'NS'}

    def cond(self, cc, k):
        """does condition code cc (e, ne, l, ...) hold? (may fork)"""
        f = self.s.flags2
        if f is None:
            raise Reject('flags')
        rel = self.CC.get(cc)
        if rel is None:
            raise Reject('condition %s' % cc)
        if f[0] == 'cmp':
            a, b, w = f[1], f[2], f[3]
            if rel in ('S', 'NS'):
                raise Reject('sign of a compare')
        else:
            a, b, w = f[1], ('k', 0), f[2]
            if f[0] == 'arith' and rel not in ('==', '!=', 'S', 'NS'):
                raise Reject('condition %s after arithmetic' % cc)
            # after test/and/or (CF = OF = 0) every condition is one on v
            rel = {'S': '<s', 'NS': '>=s', '<u': False, '>=u': True, '<=u': '==', '>u': '!='}.get(rel, rel)
            if rel is True or rel is False:
                return rel
        if a[0] == 'k' and b[0] == 'k':
            return rel_holds(rel, a[1], b[1], w)
        self.use(a); self.use(b)
        n, neg = self.atom('J%d' % k, ('rel', rel, a, b, w))
        return self.decide(n) != neg

    def atom(self, name, at):
        """a test's name: an earlier test of exactly the same values keeps its
        name (the same decision), so no path takes both outcomes"""
        for n, x in self.s.atoms.items():
            if x == at and n[0] in 'JQ':
                return n, False
            if x[0] == 'rel' and at[0] == 'rel' and x[2:] == at[2:] and x[1] == NEG[at[1]] and n[0] == 'J':
                return n, True                      # the opposite test of the same values
        self.s.atoms[name] = at
        return name, False

    # ---- run
    def run(self):
        s = self.s
        k = 0
        n = 0
        while True:
            n += 1
            if n > 4000 or k >= len(self.ins):
                raise Reject('ran off the function')
            i = self.ins[k]
            self.visited.append(i.address)
            m, o = i.mnemonic, i.op_str
            ops = [x.strip() for x in re.split(r',(?![^\[]*\])', o)] if o else []
            nxt = k + 1
            if m == 'mov':
                d, src = ops
                if '[' in d:
                    mm = re.fullmatch(r'(byte|word|dword) ptr \[(.*)\]', d)
                    w = WIDTH[mm.group(1)]
                    v = self.val(src, k)
                    if w < 4 and v[0] == 'lo' and v[1] == w:
                        v = v[2] if v[2][0] not in NEW else lo(w, v[2])
                    self.store(self.addr(mm.group(2)), w, v, '=', k)
                else:
                    self.setreg(d, self.val(src, k))
            elif m == 'movzx' or m == 'movsx':
                d, src = ops
                w = opwidth(src)
                v = self.val(src, k)
                self.setreg(d, lo(w, v) if m == 'movzx' else sx(w, v))
            elif m == 'lea':
                d, src = ops
                mm = re.fullmatch(r'\[(.*)\]', src.replace('dword ptr ', ''))
                a = self.addr(mm.group(1))
                if a[0] == 'stk':
                    if not self.mem:
                        raise Reject('lea of the stack')
                    a = ('sym', 'sp', a[1])
                self.setreg(d, a)
            elif m == 'xor' and ops[0] == ops[1] and ops[0] in s.r:
                self.setreg(ops[0], ('k', 0))
                s.flags = ('const', True)
                s.flags2 = ('logic', ('k', 0), 4)
            elif m in ('or', 'and', 'add', 'sub', 'xor') and '[' in ops[0]:
                mm = re.fullmatch(r'(byte|word|dword) ptr \[(.*)\]', ops[0])
                v = self.val(ops[1], k)
                w = WIDTH[mm.group(1)]
                if v[0] != 'k':
                    if v[0] == 'unk':
                        raise Reject('%s with an unknown value' % m)
                    v = lo(w, v) if w < 4 else v
                self.store(self.addr(mm.group(2)), w, v, {'or': '|=', 'and': '&=', 'add': '+=', 'sub': '-=', 'xor': '^='}[m], k)
                s.flags = s.flags2 = None
            elif m in ('inc', 'dec') and '[' in ops[0]:
                mm = re.fullmatch(r'(byte|word|dword) ptr \[(.*)\]', ops[0])
                self.store(self.addr(mm.group(2)), WIDTH[mm.group(1)], ('k', 1), '+=' if m == 'inc' else '-=', k)
                s.flags = s.flags2 = None
            elif m == 'add' and ops[0] == 'esp':
                s.esp += num(ops[1])
            elif m == 'sub' and ops[0] == 'esp':
                s.esp -= num(ops[1])
            elif m in ARITH and ops and ops[0] in REGW:
                self.arith(m, ops, k)
            elif m == 'cdq':
                self.setreg('edx', op('>>s', s.r['eax'], ('k', 31)))
            elif m == 'push':
                v = self.val(ops[0], k)
                s.esp -= 4
                s.stack[s.esp] = v
                s.pushes.append(s.esp)
                if self.mem:                       # the pushed word, in memory
                    if v[0] == 'unk':
                        raise Reject('pushes an unknown value')
                    self.use(v)
                    s.effects.append((k, 'store', (('sym', 'sp', s.esp), 4, v, '=')))
                    s.slotfx[s.esp] = len(s.effects) - 1
            elif m == 'pop':
                if s.esp not in s.stack:
                    raise Reject('pop of nothing')
                self.setreg(ops[0], s.stack[s.esp])
                if ops[0] not in ('eax', 'ecx', 'edx'):
                    s.fresh.discard(ops[0])      # a callee-saved register restored (eax/ecx/edx: a value to pass on)
                if s.pushes and s.pushes[-1] == s.esp:
                    s.pushes.pop()
                s.esp += 4
            elif m == 'test':
                if ops[0] == 'ah' and s.flags and s.flags[0] == 'fcom':
                    mask = num(ops[1])
                    s.flags2 = ('fah', mask & 0x45, s.flags[1], s.flags[2])
                    if mask == 0x41:
                        s.flags = ('fgt', s.flags[1], s.flags[2], k)
                    else:
                        s.flags = None
                elif ops[0] == ops[1]:
                    v = self.val(ops[0], k)
                    try:
                        a, known = self.atom_of(v, k)
                        s.flags = ('nz', a, known)
                    except Reject:
                        s.flags = None
                    s.flags2 = ('logic', v, opwidth(ops[0]))
                else:
                    w = opwidth(ops[0])
                    a, b = self.val(ops[0], k), self.val(ops[1], k)
                    s.flags = None
                    s.flags2 = ('logic', lo(w, op('&', a, b)), w)
            elif m == 'cmp':
                a, b = self.val(ops[0], k), self.val(ops[1], k)
                w = min(opwidth(ops[0]), opwidth(ops[1]))
                s.flags2 = ('cmp', a, b, w)
                if b[0] != 'k' or w != 4 or a[0] in NEW:
                    s.flags = None
                elif b[1] == 0:
                    try:
                        at, known = self.atom_of(a, k)
                        s.flags = ('nz', at, known)
                    except Reject:
                        s.flags = None
                elif a[0] == 'k':
                    s.flags = ('const', a[1] == b[1])
                elif a[0] in ('unk',) or (a[0] == 'sym' and (a[1].startswith('in_') or a[1].startswith('arg'))):
                    s.flags = None
                else:
                    self.use(a)
                    name = 'C%d' % k
                    s.atoms[name] = ('eq', a, b[1])
                    s.flags = ('eq', name)
            elif m in JCC:
                tgt = self.at.get(num(o))
                if tgt is None or tgt <= k:
                    raise Reject('branch out or back')
                if self.holds(m[1:], k):
                    nxt = tgt
            elif m.startswith('set') and m[3:] in self.CC:
                v = ('k', 1 if self.holds(m[3:], k) else 0)
                if '[' in ops[0]:
                    mm = re.fullmatch(r'byte ptr \[(.*)\]', ops[0])
                    if not mm:
                        raise Reject(m + ' ' + o)
                    self.store(self.addr(mm.group(1)), 1, v, '=', k)
                else:
                    self.setreg(ops[0], v)
            elif m == 'sbb' and ops[0] == ops[1] and ops[0] in s.r:
                # sbb r, r: all ones when CF is set (after cmp a, b: a < b
                # unsigned; after neg v: v != 0)
                f = s.flags2
                if f and f[0] == 'neg':
                    s.flags2 = ('logic', f[1], f[2])
                    cf = self.cond('ne', k)
                elif f and f[0] == 'cmp':
                    cf = self.cond('b', k)
                else:
                    raise Reject('sbb')
                self.setreg(ops[0], ('k', M32 if cf else 0))
                s.flags = s.flags2 = None
            elif m == 'jmp':
                if not re.fullmatch(r'0x[0-9a-f]+', o):
                    raise Reject('indirect jump')
                tgt = self.at.get(num(o))
                if tgt is None and '0x%08X' % num(o) in _abi and num(o) not in [x.address for x in self.ins]:
                    return self.tail(i, num(o), k)
                if tgt is None or tgt <= k:
                    raise Reject('jump out or back')
                nxt = tgt
            elif m == 'call':
                self.call(i, ops[0], k)
                s.flags = s.flags2 = None
            elif m == 'ret':
                return self.ret(num(o) if o else 0)
            elif m in ('fld', 'fild') and re.fullmatch(r'st\(\d\)', o) is None:
                mm = re.fullmatch(r'dword ptr \[(.*)\]', o)
                if not mm:
                    raise Reject(m + ' ' + o)
                a = self.addr(mm.group(1))
                if a[0] == 'stk' and self.mem:
                    a = ('sym', 'sp', a[1])
                if a[0] == 'stk':
                    s.fp.append(self.stack_float(a[1], m))
                else:
                    self.check_addr(a)
                    self.loads[k] = (len(s.effects), a, 4)
                    s.fp.append((m, k, a))
            elif m in FOPS and '[' in o:
                mm = re.fullmatch(r'dword ptr \[(.*)\]', o)
                if not mm or not s.fp:
                    raise Reject(m + ' ' + o)
                a = self.addr(mm.group(1))
                if a[0] == 'stk' and self.mem:
                    a = ('sym', 'sp', a[1])
                if a[0] == 'stk':
                    raise Reject('x87 operand on the stack')
                self.check_addr(a)
                self.loads[k] = (len(s.effects), a, 4)
                s.fp[-1] = ('fop', FOPS[m], s.fp[-1], ('fild' if m.startswith('fi') else 'fld', k, a))
            elif m in ('fstp', 'fst') and '[' in o:
                mm = re.fullmatch(r'dword ptr \[(.*)\]', o)
                if not mm or not s.fp:
                    raise Reject(m + ' ' + o)
                e = s.fp.pop() if m == 'fstp' else s.fp[-1]
                a = self.addr(mm.group(1))
                if a[0] == 'stk' and self.mem:
                    s.stack[a[1]] = ('f', ('r32', e))
                    a = ('sym', 'sp', a[1])
                    self.store(a, 4, ('f', e), '=', k)
                    s.slotfx[a[2]] = len(s.effects) - 1
                elif a[0] == 'stk':
                    if a[1] % 4:
                        raise Reject('x87 store to the stack')
                    if a[1] > 0:
                        s.argw = True
                    s.stack[a[1]] = ('f', ('r32', e))
                else:
                    self.store(a, 4, ('f', e), '=', k)
            elif m == 'fcomp' and '[' in o:
                mm = re.fullmatch(r'dword ptr \[(.*)\]', o)
                if not mm or not s.fp:
                    raise Reject(m + ' ' + o)
                a = self.addr(mm.group(1))
                if a[0] == 'stk' and self.mem:
                    a = ('sym', 'sp', a[1])
                if a[0] == 'stk':
                    raise Reject('x87 compare with the stack')
                self.check_addr(a)
                self.loads[k] = (len(s.effects), a, 4)
                s.flags = ('fcom', s.fp.pop(), ('fld', k, a))
                s.flags2 = None
            elif m == 'fcom' and '[' in o:
                mm = re.fullmatch(r'dword ptr \[(.*)\]', o)
                if not mm or not s.fp:
                    raise Reject(m + ' ' + o)
                a = self.addr(mm.group(1))
                if a[0] == 'stk' and self.mem:
                    a = ('sym', 'sp', a[1])
                if a[0] == 'stk':
                    raise Reject('x87 compare with the stack')
                self.check_addr(a)
                self.loads[k] = (len(s.effects), a, 4)
                s.flags = ('fcom', s.fp[-1], ('fld', k, a))
                s.flags2 = None
            elif m == 'fnstsw' and o == 'ax':
                if not s.flags or s.flags[0] != 'fcom':
                    raise Reject('fnstsw')
                s.r['eax'] = ('unk', 'fnstsw')
            elif m[0] == 'f' and m != 'fnstsw':
                self.x87reg(i, k)
            elif m == 'nop':
                pass
            else:
                raise Reject('instruction %s %s' % (m, o))
            k = nxt

    def holds(self, cc, k):
        """condition code cc at instruction k: the shapes' flag model for
        ZF where it applies (so their output stays the same), else cond()"""
        s = self.s
        f = s.flags
        if cc in ('e', 'ne') and f and f[0] == 'fgt':
            # fcomp; fnstsw ax; test ah, 0x41: ZF set when st0 > src
            name = 'F%d' % f[3]
            s.atoms[name] = ('fgt', f[1], f[2])
            self.usef(f[1]); self.usef(f[2])
            z = self.decide(name)
        elif s.flags2 and s.flags2[0] == 'fah':
            return self.fcond(cc, k)
        elif cc in ('e', 'ne') and f is not None:
            z = self.zf()
        else:
            return self.cond(cc, k)
        return z if cc == 'e' else not z

    def fcond(self, cc, k):
        """fcom; fnstsw ax; test ah, mask; then je/jne (ZF) or jp/jnp (PF):
        the outcome in each of the four compare results, as one C++ compare"""
        mask, a, b = self.s.flags2[1:]
        out = []
        for ah in (0x00, 0x01, 0x40, 0x45):            # st0 > src, <, ==, unordered
            x = ah & mask
            z, p = x == 0, bin(x).count('1') % 2 == 0
            out.append({'e': z, 'ne': not z, 'p': p, 'np': not p}[cc])
        out = tuple(out)
        if all(out) or not any(out):
            return out[0]
        neg = out[3]                                    # true when unordered: the negation of an ordered compare
        if neg:
            out = tuple(not x for x in out)
        rel = FREL.get(out[:3])
        if rel is None:
            raise Reject('x87 condition %s' % (out,))
        self.usef(a); self.usef(b)
        n, _ = self.atom('Q%d' % k, ('fcmp', rel, a, b))
        r = self.decide(n)
        return not r if neg else r

    def arith(self, m, ops, k):
        """a register operation of the long tail: add/sub/and/or/xor/adc..,
        shifts, inc/dec/neg/not, imul"""
        s = self.s
        d = ops[0]
        w = REGW[d]
        if d in ('esp',):
            raise Reject('%s esp' % m)
        cur = self.val(d, k)
        if cur[0] == 'unk':
            raise Reject('%s of an unknown value' % m)
        flags2 = None
        if m == 'not':
            res, flags2 = un('~', cur), s.flags2
        elif m == 'neg':
            res = lo(w, un('-', cur))
            flags2 = ('neg', lo(w, cur), w)
        elif m in ('inc', 'dec'):
            res = lo(w, op('+' if m == 'inc' else '-', cur, ('k', 1)))
            flags2 = ('arith', res, w)
        elif m in ('shl', 'sal', 'shr', 'sar'):
            cnt = self.val(ops[1], k) if len(ops) > 1 else ('k', 1)
            if cnt[0] != 'k' or w != 4:
                raise Reject('%s %s' % (m, ', '.join(ops)))
            res = op({'shl': '<<', 'sal': '<<', 'shr': '>>', 'sar': '>>s'}[m], cur, cnt)
            flags2 = ('arith', res, w) if cnt[1] & 31 else s.flags2
        elif m == 'imul':
            if w != 4 or len(ops) not in (2, 3):
                raise Reject('imul')
            a, b = (cur, self.val(ops[1], k)) if len(ops) == 2 else (self.val(ops[1], k), self.val(ops[2], k))
            res = op('*', a, b)
        else:
            src = self.val(ops[1], k)
            if src[0] == 'unk':
                raise Reject('%s with an unknown value' % m)
            res = lo(w, op({'add': '+', 'sub': '-', 'and': '&', 'or': '|', 'xor': '^'}[m], cur, src))
            flags2 = ('cmp', cur, src, w) if m == 'sub' else ('arith', res, w) if m == 'add' else ('logic', res, w)
        self.setreg(d, res)
        s.flags, s.flags2 = (s.flags if m == 'not' else None), flags2

    def stack_float(self, off, m):
        """fld/fild of a stack slot: a float the function stored there, a
        float argument, or (fild) an integer"""
        s = self.s
        if off % 4:
            raise Reject('x87 load of the stack')
        v = s.stack.get(off)
        if v is None:
            if off < 4:
                raise Reject('x87 load of the stack')
            v = ('sym', 'arg%d' % ((off - 4) // 4), 0)
            return ('farg', (off - 4) // 4) if m == 'fld' else ('fint', v)
        if m == 'fld':
            if v[0] != 'f':
                raise Reject('x87 load of an integer slot')
            return v[1]
        if v[0] == 'f':
            raise Reject('fild of a float slot')
        return ('fint', v)

    def x87reg(self, i, k):
        """x87 register-stack instructions, decoded from their bytes"""
        s = self.s
        b = bytes(i.bytes)
        fp = s.fp

        def st(j):
            if j >= len(fp):
                raise Reject('x87 stack underflow (st(%d))' % j)
            return fp[-1 - j]

        def put(j, v):
            st(j)
            fp[-1 - j] = v
        if len(b) != 2 or b[1] < 0xC0:
            raise Reject('instruction %s %s' % (i.mnemonic, i.op_str))
        hi, r, j = b[0], (b[1] >> 3) & 7, b[1] & 7
        OPS = {0: 'fadd', 1: 'fmul', 4: 'fsub', 5: 'fsubr', 6: 'fdiv', 7: 'fdivr'}
        if hi == 0xD8 and r in OPS:                      # st0 = st0 op st(j)
            put(0, ('fop', OPS[r], st(0), st(j)))
        elif hi in (0xDC, 0xDE) and r in OPS:           # st(j) = st(j) op st0 (the sub/div pairs swap)
            o = {4: 'fsubr', 5: 'fsub', 6: 'fdivr', 7: 'fdiv'}.get(r, OPS[r])
            put(j, ('fop', o, st(j), st(0)))
            if hi == 0xDE:
                fp.pop()
        elif hi == 0xD9 and r == 0:                     # fld st(j)
            fp.append(st(j))
        elif hi == 0xD9 and r == 1:                     # fxch st(j)
            a, c = st(0), st(j)
            put(0, c); put(j, a)
        elif hi == 0xDD and r in (2, 3):                # fst / fstp st(j)
            put(j, st(0))
            if r == 3:
                fp.pop()
        elif b == b'\xd9\xe0':                          # fchs
            put(0, ('fun', 'chs', st(0)))
        elif b in (b'\xd9\xe8', b'\xd9\xee'):            # fld1, fldz
            fp.append(('fk', 1.0 if b[1] == 0xE8 else 0.0))
        elif (hi == 0xD8 and r in (2, 3)) or (hi == 0xDD and r in (4, 5)):   # fcom(p) / fucom(p) st(j)
            s.flags, s.flags2 = ('fcom', st(0), st(j)), None
            if r in (3, 5):
                fp.pop()
        elif b in (b'\xde\xd9', b'\xda\xe9'):            # fcompp, fucompp
            s.flags, s.flags2 = ('fcom', st(0), st(1)), None
            fp.pop(); fp.pop()
        elif b == b'\xd9\xe4':                          # ftst
            s.flags, s.flags2 = ('fcom', st(0), ('fk', 0.0)), None
        else:
            raise Reject('instruction %s %s' % (i.mnemonic, i.op_str))

    def ret(self, pops):
        s = self.s
        if s.esp != 0:
            raise Reject('stack not balanced at ret')
        st0 = None
        if s.fp:
            if len(s.fp) > 1 or self.entry == 'create':
                raise Reject('x87 values left')
            st0 = s.fp[0]
            self.usef(st0)
        if pops == 0 and s.argw and not self.mem:
            raise Reject('writes its stack arguments (the caller\'s memory)')
        e = s.r['eax']
        if self.entry in ('method', 'free') and e[0] in ('ld', 'plus'):
            self.use(e)                                  # the result: a load still valid here
        elif e[0] in NEW:
            try:
                self.use(e)
            except Reject:
                e = ('unk', 'result')
        out = dict(eax=e, pops=pops, last=s.last_res)
        if st0 is not None:
            out['st0'] = st0
        return out

    def tail(self, i, va, k):
        """jmp to another function: it returns to this one's caller, popping
        what its own ret pops (this function's arguments)"""
        s = self.s
        if s.esp != 0 or s.fp:
            raise Reject('tail call with a frame')
        ab = _abi['0x%08X' % va]
        n = ab['pops'] // 4
        if not n:
            f = _FUNCS.get(va)
            if not f:
                raise Reject('tail call to an unknown function')
            for x in _md.disasm(_rd(va, f['size']), va):
                if re.search(r'\[e[sb]p', x.op_str) or (x.mnemonic == 'jmp' and re.fullmatch(r'0x[0-9a-f]+', x.op_str)
                                                         and not va <= num(x.op_str) < va + f['size']):
                    raise Reject('tail call to a function that may read the stack')
        args = [self.load(('stk', 4 + 4 * j), 4, k) for j in range(n)]
        for v in reversed(args):
            s.esp -= 4
            s.stack[s.esp] = v
            s.pushes.append(s.esp)
        self.call(i, '0x%x' % va, k, tail=True)
        s.esp = 0
        return self.ret(ab['pops'])

    def saves(self):
        """the pushes since the last call that saved a register: its entry
        value pushed, the register written since"""
        s = self.s
        out = []
        for a in s.pushes:
            v = s.stack[a]
            if v[0] == 'sym' and v[1].startswith('in_') and v[2] == 0 and s.r.get(v[1][3:]) != v:
                out.append(a)
        return out

    def cleanup(self, j, va):
        """a cdecl call whose caller does not remove the arguments at once:
        the `add esp, N` that does, later on the straight line after the call;
        else none when the callee reads no stack arguments; else every value
        pushed since the last call (passed as they lie on the stack, so the
        callee sees the original's stack whatever it reads)"""
        s = self.s
        if stack_args(va) == 0:
            return 0
        if stack_args(va) is None and all(s.stack[a] in (('sym', 'in_' + r, 0) for r in ('ebx', 'esi', 'edi', 'ebp'))
                                          for a in s.pushes):
            return 0                       # only callee-saved registers pushed (saved): no arguments
        while j < len(self.ins):
            x = self.ins[j]
            if x.mnemonic == 'add' and x.op_str.startswith('esp, '):
                n = num(x.op_str.split(', ')[1]) // 4
                if n > len(s.pushes):
                    raise Reject('cdecl cleanup of more than was pushed')
                return n
            if x.mnemonic in ('push', 'pop', 'call', 'ret', 'jmp', 'leave') or x.mnemonic in JCC or 'esp' in x.op_str:
                break
            j += 1
        if [s.esp + 4 * n for n in range(len(s.pushes))] != sorted(s.pushes):
            raise Reject('cdecl call: the values pushed are not contiguous')
        return len(s.pushes)

    def call(self, i, target, k, tail=False):
        s = self.s
        if s.fp:
            raise Reject('a call with x87 values on the stack (the callee may use st(0))')
        if re.fullmatch(r'0x[0-9a-f]+', target):
            va = num(target)
            ab = _abi.get('0x%08X' % va)
            if ab is None:
                raise Reject('no abi for %X' % va)
            pops, need = ab['pops'], list(ab['regs'])
            nargs = pops // 4
            j = self.at[i.address] + 1
            nxt = self.ins[j] if j < len(self.ins) else None
            if pops == 0 and nxt is not None and nxt.mnemonic == 'add' and nxt.op_str.startswith('esp, '):
                nargs = num(nxt.op_str.split(', ')[1]) // 4         # cdecl: the caller removes them
            elif pops == 0 and not tail:
                while j + nargs < len(self.ins) and self.ins[j + nargs].mnemonic == 'pop' and \
                        self.ins[j + nargs].op_str in ('ecx', 'edx'):
                    nargs += 1                                      # cdecl, removed by pop ecx
                if not nargs and s.pushes:
                    nargs = self.cleanup(j, va)
            tv = ('k', va)
        else:
            mm = re.fullmatch(r'dword ptr \[(.*)\]', target)
            if not mm:
                raise Reject('call %s' % target)
            a = self.addr(mm.group(1))
            if a[0] == 'stk':
                raise Reject('call through the stack')
            self.check_addr(a)
            self.loads[k] = (len(s.effects), a, 4)
            tv = ('ld', k, 4, a)
            self.use(tv)
            va = None
            nargs = len(s.pushes)            # thiscall: it pops what was pushed for it
            slot = a[2] if a[0] == 'plus' and a[1][0] == 'ld' else 0 if a[0] == 'ld' else None
            sp = _slot_pops.get(slot, set())
            if slot is not None and self.cls and a[0] == 'plus' and a[1][3] == ('sym', 'self', 0):
                sp = class_slot_pops(self.cls, slot)     # a virtual call on this: the class's own hierarchy
            if len(sp) == 1 and None not in sp and len(s.pushes) >= list(sp)[0] // 4:
                nargs = list(sp)[0] // 4     # every class's method in that slot pops this much
            else:
                sv = self.saves()
                if sv:
                    # some pushes may have saved registers: try every count
                    # the slot's methods pop; only the one that keeps the
                    # stack consistent to the end (the saves popped back) runs
                    # to completion
                    opts = sorted({x // 4 for x in sp if x is not None and x // 4 <= len(s.pushes)} or {len(s.pushes) - len(sv)})
                    name = 'N%d' % k
                    if name not in s.decided:
                        raise ForkN(name, opts)
                    nargs = s.decided[name]
            pops, need = 4 * nargs, ['ecx']
        if len(s.pushes) < nargs:
            raise Reject('call with arguments not pushed after the last call')
        args = []
        for j in range(nargs):
            v = s.stack.get(s.esp + 4 * j)
            if self.mem and (v is None or v[0] in ('unk', 'f', 'fbits')):
                v = ('mrd', ('sym', 'sp', s.esp + 4 * j))   # the slot as memory holds it at the call
            if v is None:
                raise Reject('call argument never pushed')
            args.append(v)
        for a in args:
            if a[0] == 'f':
                raise Reject('a float pushed as an argument')
            self.use(a)
        if self.mem:                                 # gcall writes the argument slots itself
            for j in range(nargs):
                fx = s.slotfx.pop(s.esp + 4 * j, None)
                if fx is not None:
                    s.dead.add(fx)
        regs = {}
        for r in REGS32:
            v = s.r[r]
            holds_obj = v[0] == 'sym' and v[1] in ('mem', 'self') and v[2] == 0
            if not (r in need or holds_obj):
                continue
            if v == ('sym', 'in_' + r, 0):
                continue                     # untouched: the gcall leaves it as it was
            if r in ('eax', 'ecx', 'edx') and r not in s.fresh and not holds_obj:
                continue                     # what the last call left: the same in both
            if v[0] == 'unk':
                if r in need:
                    raise Reject('callee %s reads %s, unknown here' % (target, r))
                continue
            regs[r] = self.use(v)
        frame = -(s.esp + 4 * nargs)         # bytes pushed below the return address before the arguments
        res = 'r%X' % i.address
        s.effects.append((k, 'call', (tv, pops, args, regs, frame, res)))
        for j in range(nargs):
            if pops:
                s.stack.pop(s.esp + 4 * j, None)
        s.esp += pops
        s.pushes = []
        s.r['eax'] = ('sym', 'mem', 0) if va == OP_NEW else ('sym', res, 0)
        s.last_res = s.r['eax']
        s.fresh = {'eax'}


# ---------------------------------------------------------------- paths
def paths(ins, entry, limit=64, cls=None):
    out = []
    todo = [{}]
    forks = []                                 # (decisions so far, count name): a ForkN that must keep one run
    failed = []                                # runs a count choice may explain
    while todo:
        d = todo.pop()
        sim = Sim(ins, entry, dict(d))
        sim.cls = cls
        try:
            res = sim.run()
        except Fork as f:
            if len(out) + len(todo) > limit:
                raise Reject('too many paths')
            for b in (True, False):
                nd = dict(d); nd[f.atom] = b
                todo.append(nd)
            continue
        except ForkN as f:
            if len(out) + len(todo) > limit:
                raise Reject('too many paths')
            forks.append((dict(d), f.name))
            for n in f.opts:
                nd = dict(d); nd[f.name] = n
                todo.append(nd)
            continue
        except Reject as e:
            if any(a[0] == 'N' for a in d):
                failed.append((d, e))           # a wrong count, unless it is the count that works
                continue
            raise
        out.append((d, sim, res))
    # every instruction the code can reach is on some path (a path lost
    # anywhere would leave its instructions out)
    at = {x.address: j for j, x in enumerate(ins)}
    seen, todo2 = set(), [0]
    while todo2:
        j = todo2.pop()
        if j in seen or j >= len(ins):
            continue
        seen.add(j)
        x = ins[j]
        m = x.mnemonic
        if m == 'ret':
            continue
        if (m == 'jmp' or m in JCC) and re.fullmatch(r'0x[0-9a-f]+', x.op_str):
            t = at.get(num(x.op_str))
            if t is not None:
                todo2.append(t)
            if m == 'jmp':
                continue
        elif m == 'jmp':
            continue
        todo2.append(j + 1)
    ran = set()
    for d, sim, res in out:
        ran |= set(sim.visited)
    lost = {ins[j].address for j in seen} - ran
    if lost:
        raise Reject('no path runs %X' % min(lost))
    if forks:
        right = {}
        for d, name in forks:
            vals = {o[0][name] for o in out if all(o[0].get(a) == v for a, v in d.items())}
            if len(vals) != 1:
                raise Reject('virtual call: %s argument counts keep the stack consistent' % ('no' if not vals else 'several'))
            right[(tuple(sorted(d.items(), key=str)), name)] = vals.pop()
        for d, e in failed:
            # a run with every count right failed for another reason: the function is rejected
            ok = True
            for (pre, name), v in right.items():
                if name in d and all(d.get(a) == b for a, b in pre) and d[name] != v:
                    ok = False
            if ok:
                raise e
        out = [({a: v for a, v in d.items() if a[0] != 'N'}, sim, res) for d, sim, res in out]
    return out


# ---------------------------------------------------------------- rendering
def hexs(c):
    return '%d' % c if c < 10 else '0x%X' % c


class Render:
    """C++ spelling of values; decides which loads become variables."""

    def __init__(self, ins, ps, obj):
        self.ins, self.ps, self.obj = ins, ps, obj
        self.mat = set()
        self.atoms = {}
        self.callres = set()
        self.args = set()
        self.inregs = set()
        self.fargs = set()
        self.uses_sp = False
        for d, sim, res in ps:
            self.atoms.update(sim.s.atoms)
            for lid, pos in sim.uses:
                lpos, a, w = sim.loads[lid]
                if self.clobbered(sim, lpos, pos, a, w):
                    self.mat.add(lid)
        def keep(lid, a):                      # an atom reads it: a variable unless it is read-only data
            if not (a[0] == 'k' and readonly(a[1])):
                self.mat.add(lid)
        def keep_all(v):                       # every load a long-tail condition reads
            t = v[0]
            if t == 'ld':
                keep(v[1], v[3]); keep_all(v[3])
            elif t in ('fld', 'fild'):
                keep(v[1], v[2]); keep_all(v[2])
            elif t in ('plus', 'lo', 'un', 'sx', 'fint'):
                keep_all(v[1] if t in ('plus', 'fint') else v[2])
            elif t in ('op', 'ins', 'fop'):
                keep_all(v[2]); keep_all(v[3])
            elif t in ('f', 'fun', 'r32'):
                keep_all(v[-1])
        for name, at in self.atoms.items():
            if at[0] == 'rel':
                keep_all(at[2]); keep_all(at[3])
            elif at[0] == 'fcmp':
                keep_all(at[2]); keep_all(at[3])
            elif at[0] in ('nz', 'eq') and at[1][0] == 'ld':
                keep(at[1][1], at[1][3])
            elif at[0] == 'fgt':
                for e in (at[1], at[2]):
                    if e[0] in ('fld', 'fild'):
                        keep(e[1], e[2])
                    else:
                        raise Reject('x87 compare of an expression')

    @staticmethod
    def clobbered(sim, lpos, pos, a, w):
        for key, kind, data in sim.s.effects[lpos:pos]:
            if kind == 'call':
                return True
            if may_alias(a, w, data[0], data[1]):
                return True
        return False

    def name(self, lid):
        i = self.ins[lid]
        return ('f%X' if i.mnemonic.startswith('f') else 'v%X') % i.address

    def v(self, x, fixed):
        t = x[0]
        if t == 'k':
            return hexs(x[1])
        if t == 'sym':
            n = x[1]
            if n == 'build':
                base = '0' if fixed.get('build') is False else 'g_ClassTableBuild()'
            elif n in ('mem', 'self'):
                base = self.obj
            elif re.fullmatch(r'r[0-9A-F]+', n):
                self.callres.add(n)
                base = '0' if fixed.get(n) is False else n
            elif n.startswith('arg'):
                self.args.add(int(n[3:]))
                base = 'a%s' % n[3:]
            elif n.startswith('in_') and n[3:] in REGS32:
                self.inregs.add(n[3:])
                base = n
            elif n == 'sp':
                self.uses_sp = True
                base = 'sp'
            else:
                raise Reject('reads %s' % n)
            if x[2]:
                return '%s %s 0x%X' % (base, '+' if x[2] > 0 else '-', abs(x[2]))
            return base
        if t == 'ld':
            if fixed.get('L%d' % x[1]) is False:
                return '0'
            if x[1] in self.mat:
                return self.name(x[1])
            return '%s(%s)' % (ACC[x[2]], self.v(x[3], fixed))
        if t == 'mrd':
            return 'm32(%s)' % self.v(x[1], fixed)
        if t == 'plus':
            return '%s %s 0x%X' % (self.v(x[1], fixed), '+' if x[2] > 0 else '-', abs(x[2]))
        if t == 'op':
            a, b = paren(self.v(x[2], fixed)), paren(self.v(x[3], fixed))
            if x[1] == '>>s':
                return '(uint32_t)((int32_t)%s >> %s)' % (a, b)
            return '((uint32_t)%s %s %s)' % (a, x[1], b)
        if t == 'un':
            return ('(0u - (uint32_t)%s)' if x[1] == '-' else '~(uint32_t)%s') % paren(self.v(x[2], fixed))
        if t == 'sx':
            return '(uint32_t)(int32_t)(int%d_t)%s' % (8 * x[1], paren(self.v(x[2], fixed)))
        if t == 'ins':
            hi = '(uint32_t)%s & 0x%X' % (paren(self.v(x[2], fixed)), ~((1 << 8 * x[1]) - 1) & M32)
            if x[3][0] == 'k':
                return '(%s)' % hi if not x[3][1] else '((%s) | %s)' % (hi, hexs(x[3][1]))
            return '((%s) | (uint%d_t)%s)' % (hi, 8 * x[1], paren(self.v(x[3], fixed)))
        if t == 'lo':
            inner = x[2]
            if inner[0] == 'k':
                return hexs(inner[1] & ((1 << (8 * x[1])) - 1))
            return '(uint%d_t)%s' % (8 * x[1], paren(self.v(inner, fixed)))
        if t == 'f':
            return self.f(x[1], fixed)
        raise Reject('renders %s' % (t,))

    def f(self, e, fixed):
        if e[0] in ('fld', 'fild') and e[1] in self.mat:
            return self.name(e[1])
        if e[0] == 'fld':
            return '(double)mf(%s)' % self.v(e[2], fixed)
        if e[0] == 'fild':
            return '(double)(int32_t)m32(%s)' % self.v(e[2], fixed)
        if e[0] == 'fk':
            return repr(e[1])
        if e[0] == 'fun':
            return '(-%s)' % paren(self.f(e[2], fixed))
        if e[0] == 'r32':
            return '(double)(float)%s' % paren(self.f(e[1], fixed))
        if e[0] == 'fint':
            return '(double)(int32_t)%s' % paren(self.v(e[1], fixed))
        if e[0] == 'farg':
            self.fargs.add(e[1])
            return '(double)fa%d' % e[1]
        if e[0] == 'fop':
            a, b = self.f(e[2], fixed), self.f(e[3], fixed)
            op = {'fadd': '+', 'fsub': '-', 'fmul': '*', 'fdiv': '/', 'fsubr': '-', 'fdivr': '/'}[e[1]]
            if e[1] in ('fsubr', 'fdivr'):
                a, b = b, a
            return '(%s %s %s)' % (a, op, b)
        raise Reject('x87 %s' % (e[0],))

    def lit(self, name, val):
        if name == 'build':
            return '!g_ClassTableBuild()' if not val else 'g_ClassTableBuild()'
        if name in ('mem', 'self') or re.fullmatch(r'r[0-9A-F]+', name):
            e = self.v(('sym', name, 0), {})
            return e if val else '!' + e
        at = self.atoms[name]
        if at[0] == 'nz':
            e = self.v(at[1], {})
            return e if val else '!' + e
        if at[0] == 'eq':
            return '%s %s %s' % (self.v(at[1], {}), '==' if val else '!=', hexs(at[2]))
        if at[0] == 'fgt':
            e = '%s > %s' % (self.f(at[1], {}), self.f(at[2], {}))
            return e if val else '!(%s)' % e
        if at[0] == 'rel':
            rel, a, b, w = at[1:]
            if not val:
                rel = NEG[rel]
            signed = rel.endswith('s')
            sym = rel.rstrip('su')

            def opnd(x):
                t = self.v(x, {})
                if not signed and (x[0] == 'k' or (w == 4 and x[0] not in ('lo', 'ins', 'sx')) or
                                   (x[0] == 'ld' and x[2] == w) or (x[0] == 'lo' and x[1] == w)):
                    return t                       # already an unsigned value of that width
                return '(%sint%d_t)%s' % ('' if signed else 'u', 8 * w, paren(t))
            if b == ('k', 0) and rel in ('==', '!='):
                return '%s %s 0' % (opnd(a), sym)
            return '%s %s %s' % (opnd(a), sym, opnd(b) if b[0] != 'k' or signed else hexs(b[1] & ((1 << 8 * w) - 1)))
        if at[0] == 'fcmp':
            rel, a, b = at[1:]
            fa, fb = self.f(a, {}), self.f(b, {})
            e = '(%s < %s || %s > %s)' % (fa, fb, fa, fb) if rel == '<>' else '%s %s %s' % (fa, rel, fb)
            return e if val else '!(%s)' % e
        raise Reject('atom')


def paren(t):
    """t as an operand: in parentheses unless it is a name, a number, a call
    or already parenthesised"""
    if re.fullmatch(r'[\w.]+', t):
        return t
    m = re.match(r'[\w:]*\(', t)
    if m:
        d = 0
        for j in range(m.end() - 1, len(t)):
            d += {'(': 1, ')': -1}.get(t[j], 0)
            if d == 0:
                if j == len(t) - 1:
                    return t
                break
    return '(%s)' % t


def may_alias(a, w, b, bw):
    def split(x):
        if x[0] == 'k':
            return ('k',), x[1]
        if x[0] == 'sym':
            return ('sym', x[1]), x[2]
        if x[0] == 'plus':
            return ('expr', x[1]), x[2]
        return ('expr', x), 0
    (ba, oa), (bb, ob) = split(a), split(b)
    if ba == bb:
        return oa < ob + bw and ob < oa + w
    if {ba[0], bb[0]} == {'k', 'sym'} and (ba[-1] in ('mem', 'self') or bb[-1] in ('mem', 'self')):
        return False                    # an object and a static
    return True


def guard_of(inside, ps, order):
    """the conjunction of tests (atom -> value) that holds on the paths
    `inside` and fails, on a test it made, on every other path"""
    sel = [ps[j][0] for j in inside]
    cube = dict(sel[0])
    for d in sel[1:]:
        cube = {a: v for a, v in cube.items() if d.get(a) == v}
    others = [ps[j][0] for j in range(len(ps)) if j not in inside]

    def separates(c):
        return all(any(a in d and d[a] != v for a, v in c.items()) for d in others)
    if not separates(cube):
        raise Reject('a statement runs under a condition that is not one conjunction of tests')
    for a in sorted(cube, key=lambda a: -order.get(a, 0)):
        trial = {x: y for x, y in cube.items() if x != a}
        if separates(trial):
            cube = trial
    return cube


def body(ins, ps, obj, sel=None, skip=lambda j, n: False, indent='    ', frame_base=0, also=()):
    """C++ statements for the effects of paths `sel`, each under the tests
    of the paths that run it (nested if blocks). -> (lines, render)"""
    sel = list(range(len(ps))) if sel is None else sel
    sub = [ps[j] for j in sel]
    rd = Render(ins, sub, obj)
    order = {}
    occurs = {}
    for jj, (d, sim, res) in enumerate(sub):
        for a in d:
            mm = re.fullmatch(r'[LCFJQ](\d+)', a)
            order[a] = int(mm.group(1)) if mm else -1
        for n, (key, kind, data) in enumerate(sim.s.effects):
            if not skip(sel[jj], n) and n not in sim.s.dead:
                occurs.setdefault((key, 1, kind), []).append((jj, data))
        for lid in rd.mat:
            if lid in sim.loads:
                occurs.setdefault((lid, 0, 'load'), []).append((jj, sim.loads[lid]))
    stmts = []
    for (key, _, kind), lst in sorted(occurs.items()):
        cube = guard_of(sorted({j for j, _ in lst}), sub, order)
        texts = {effect(rd, ins, key, kind, data, dict(cube)) for j, data in lst}
        if len(texts) != 1:
            raise Reject('the statement at %X differs between paths' % (ins[key].address if key < len(ins) else key))
        stmts.append((cube, texts.pop()))
    also_r = [rd.v(x, {}) for x in also]             # rendered first: call results they read become variables
    # nested blocks
    lines, pre, stack = [], [], [{}]
    closed = None                                     # (depth, tests) of the block just closed
    elses = set()                                     # depths whose open block is an else
    for cube, text in stmts:                          # tests first: call results they read become variables
        for a in cube:
            rd.lit(a, cube[a])
    for cube, text in stmts:
        while not all(stack[-1].get(a) == v for a, v in cube.items()) or \
                not all(cube.get(a) == v for a, v in stack[-1].items()):
            if all(cube.get(a) == v for a, v in stack[-1].items()):
                new = {a: v for a, v in cube.items() if a not in stack[-1]}
                lits = sorted(new, key=lambda a: order.get(a, 0))
                if closed and closed[0] == len(stack) and len(new) == 1 and closed[1] == {lits[0]: not new[lits[0]]} and \
                        lines[-1] == indent * len(stack) + '}':
                    lines[-1] += ' else {'            # the block just closed tested the opposite
                    elses.add(len(stack))
                else:
                    lines.append(indent * len(stack) + 'if (%s) {' % ' && '.join(rd.lit(a, new[a]) for a in lits))
                    elses.discard(len(stack))
                stack.append(dict(cube))
                closed = None
            else:
                top = stack.pop()
                closed = None if len(stack) in elses else (len(stack), {a: v for a, v in top.items() if a not in stack[-1]})
                elses.discard(len(stack))
                lines.append(indent * len(stack) + '}')
        ind = indent * len(stack)
        nested = len(stack) > 1
        if text.startswith('@'):                       # a variable: '@type name = expr;' or a call
            kind, rest = text[1:].split(' ', 1)
            if kind == 'CALL':
                res, frame, call = rest.split(' ', 2)
                call, _, cname = call.partition('//')
                frame = int(frame) - frame_base
                used = res in rd.callres
                if used and (nested or frame):
                    pre.append('uint32_t %s = 0;' % res)
                    stmt = '%s = %s;' % (res, call)
                elif used:
                    stmt = 'uint32_t %s = %s;' % (res, call)
                else:
                    stmt = '%s;' % call
                if frame:
                    stmt = '{ GuestFrame pushed(%d); %s }' % (frame, stmt)
                lines.append(ind + stmt + ('   /* %s */' % cname if cname else ''))
            else:
                typ, decl = kind, rest
                if nested:
                    pre.append('%s %s = 0;' % (typ, decl.split(' = ')[0]))
                    lines.append(ind + decl)
                else:
                    lines.append(ind + typ + ' ' + decl)
        else:
            lines.append(ind + text)
    while len(stack) > 1:
        stack.pop()
        lines.append(indent * len(stack) + '}')
    return [indent + p for p in pre] + lines, rd, also_r


def effect(rd, ins, key, kind, data, fixed):
    if kind == 'load':
        lpos, a, w = data
        if ins[key].mnemonic.startswith('f'):
            if ins[key].mnemonic == 'fild':
                return '@double %s = (double)(int32_t)m32(%s);' % (rd.name(key), rd.v(a, fixed))
            return '@double %s = (double)mf(%s);' % (rd.name(key), rd.v(a, fixed))
        return '@uint32_t %s = %s(%s);' % (rd.name(key), ACC[w], rd.v(a, fixed))
    if kind == 'store':
        a, w, v, op = data
        if v[0] == 'f':
            if w != 4 or op != '=':
                raise Reject('x87 store')
            return 'mf(%s) = (float)%s;' % (rd.v(a, fixed), rd.v(v, fixed))
        val = rd.v(v, fixed)
        if w < 4 and v[0] not in ('k', 'lo'):
            val = '(uint%d_t)%s' % (8 * w, val)
        if op in ('+=', '-=') and v == ('k', 1):
            return '%s(%s)%s;' % (ACC[w], rd.v(a, fixed), '++' if op == '+=' else '--')
        text = '%s(%s) %s %s;' % (ACC[w], rd.v(a, fixed), op, val)
        if w == 4 and op == '=' and v[0] == 'k' and a[0] == 'sym' and a[1] in ('mem', 'self') and a[2] == 0 and vtable_name(v[1]):
            text += '   /* %s */' % vtable_name(v[1])
        return text
    if kind == 'result':
        return 'result = %s;' % rd.v(data, fixed)
    if kind == 'call':
        tv, pops, args, regs, frame, res = data
        t = ('0x%08X' % tv[1]) if tv[0] == 'k' else rd.v(tv, fixed)
        rs = ''.join('.set(%s, %s)' % (r.upper(), rd.v(v, fixed)) for r, v in sorted(regs.items(), key=lambda kv: REGS32.index(kv[0])))
        call = 'gcall(%s, %d, {%s}%s)' % (t, pops, ', '.join(rd.v(a, fixed) for a in args), (', regs()' + rs) if rs else '')
        n = fname(tv[1]) if tv[0] == 'k' else None
        return '@CALL %s %d %s%s' % (res, frame, call, ('//' + n) if n else '')
    raise Reject(kind)


# ---------------------------------------------------------------- entries
def is_post(eff, obj):
    """the call of PostConstruction: vtable slot 1 of obj, this in ecx"""
    key, kind, data = eff
    if kind != 'call':
        return False
    tv, pops, args, regs, frame, res = data
    return (tv[0] == 'ld' and tv[3][0] == 'plus' and tv[3][2] == 4 and tv[3][1][0] == 'ld' and tv[3][1][3] == obj
            and not args and regs.get('ecx') == obj)


def create(va):
    """A Create whose construction is not one of classgen's shapes.
    -> dict(size, saves, lines (the construction, as the body of a lambda
    taking mem), ret (what it returns))"""
    ins = insns(va)
    if not ins:
        raise Reject('no function')
    ps = paths(ins, 'create')
    size = saves = None
    nonnull = []
    obj = None
    for j, (d, sim, res) in enumerate(ps):
        eff = sim.s.effects
        if not eff or eff[0][1] != 'call' or eff[0][2][0] != ('k', OP_NEW):
            raise Reject('does not start with operator new')
        tv, pops, args, regs, frame, r_ = eff[0][2]
        if args != [('sym', 'arg0', 0), ('sym', 'in_edx', 0), ('sym', 'arg1', 0), ('sym', 'arg2', 0)] or \
                regs.get('eax') != ('sym', 'in_ecx', 0) or regs.get('ecx', ('',))[0] != 'k' or set(regs) != {'eax', 'ecx'}:
            raise Reject('operator new arguments')
        if size not in (None, regs['ecx'][1]) or saves not in (None, frame):
            raise Reject('inconsistent')
        size, saves = regs['ecx'][1], frame
        if res['pops'] != 12:
            raise Reject('pops')
        if 'mem' not in d or 'build' not in d:
            raise Reject('no null or build test')
        if d['mem'] is False:
            # allocation failed: return 0, PostConstruction on 0 unless building
            rest = eff[1:]
            want_post = not d['build']
            if res['eax'] != ('k', 0) or len(rest) != (1 if want_post else 0) or (want_post and not is_post(rest[0], ('k', 0))):
                raise Reject('the null path is not the standard one')
            continue
        if obj not in (None, res['eax']):
            raise Reject('returns different objects on different paths')
        obj = res['eax']
        want_post = not d['build']
        if want_post != (len(eff) > 1 and is_post(eff[-1], obj)):
            raise Reject('PostConstruction is not the last call exactly when not building')
        if any(is_post(e, obj) for e in eff[1:-1]):
            raise Reject('PostConstruction twice')
        nonnull.append(j)
    if saves not in (4, 8):
        raise Reject('saves %s' % saves)

    def skip(j, n):
        eff = ps[j][1].s.effects
        return n == 0 or (n == len(eff) - 1 and is_post(eff[n], obj))
    # frames inside the construction are relative to Create's own saves
    lines, rd, (ret,) = body(ins, ps, 'mem', sel=nonnull, skip=skip, indent='    ', frame_base=saves, also=[obj])
    out = []
    for l in lines:
        if re.search(r'\breg\(|\ba\d\b', l):
            raise Reject('the construction reads an input of Create')
        out.append(l)
    if not out:
        raise Reject('nothing to construct')
    return dict(size=size, saves=saves, lines=out, ret=ret, paths=len(ps))


RESULT = 1 << 20                     # effect keys of the result assignments (after every instruction)


def plain(va, entry, returns_eax, exact=False):
    """a method (this in ecx: entry 'method'), a function without this
    ('free') or a static initialiser ('sinit'), as statements.
    -> dict(lines, ret (expression or None), ret_kind ('eax' or 'al'), st0
    (the x87 result or None), pops, args, inregs, fargs)"""
    ins = insns(va)
    if not ins:
        raise Reject('no function')
    ps = paths(ins, entry, cls=method_class(va) if entry == 'method' else None)
    pops = {res['pops'] for d, sim, res in ps}
    if len(pops) != 1:
        raise Reject('pops differ between paths')
    st0s = [res.get('st0') for d, sim, res in ps]
    if any(x is not None for x in st0s) and not all(x is not None for x in st0s):
        raise Reject('an x87 result on some paths only')
    vals = [res['eax'] for d, sim, res in ps]
    last_call = all(sim.s.effects and sim.s.effects[-1][1] == 'call' and res['eax'] == ('sym', sim.s.effects[-1][2][5], 0)
                    for d, sim, res in ps)
    def simple(v):                                   # a value eax holds that costs nothing to return
        return v[0] == 'k' or (v[0] == 'sym' and (v[1] in ('self',) or re.fullmatch(r'r[0-9A-F]+|arg\d+', v[1])))
    plain_eax = all(simple(v) for v in vals)
    loaded = entry in ('method', 'free') and all(v[0] in ('ld', 'plus') for v in vals)
    computed = any(v[0] in NEW for v in vals) or exact   # the long tail: eax exactly as the original leaves it
    want = returns_eax or last_call or plain_eax or loaded or computed or exact
    if want and any(v[0] == 'unk' for v in vals):
        if returns_eax or exact:
            raise Reject('returns an unknown value')
        want = False

    def alform(res):                                 # al set, the rest of eax what the guest eax holds anyway
        v = res['eax']
        if v[0] == 'ins' and v[1] == 1 and v[2] == (res['last'] or ('sym', 'in_eax', 0)):
            return v[3]
        return None
    al = want and computed and all(alform(res) is not None for d, sim, res in ps)
    rv = [alform(res) if al else res['eax'] for d, sim, res in ps]
    also = [v for v in rv if v[0] == 'sym' and re.fullmatch(r'r[0-9A-F]+', v[1])] if not computed else (rv if want else [])
    lines, rd, _ = body(ins, ps, 'self', indent='    ', also=also)
    ret = None
    if want:
        texts = [rd.v(v, dict(d)) for (d, sim, res), v in zip(ps, rv)]
        if len(set(texts)) == 1:
            ret = texts[0]
        else:
            # different results on different paths: result = ... under the
            # tests of the paths that return it
            groups = {}
            for j, t in enumerate(texts):
                groups.setdefault(t, []).append(j)
            for g, (t, js) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0])):
                for j in js:
                    ps[j][1].s.effects.append((RESULT + g, 'result', rv[j]))
            lines, rd, _ = body(ins, ps, 'self', indent='    ', also=rv)
            lines = ['    uint32_t result = 0;'] + lines
            ret = 'result'
        if re.search(r'\breg\(', ret):
            raise Reject('returns an input register')
    st0 = None
    if st0s[0] is not None:
        t = {rd.f(x, dict(d)) for (d, sim, res), x in zip(ps, st0s)}
        if len(t) != 1:
            raise Reject('returns different floats on different paths')
        st0 = t.pop()
    for l in lines:
        if re.search(r'\breg\(', l):
            raise Reject('reads an input register other than ecx')
    return dict(lines=lines, ret=ret, ret_kind='al' if al else 'eax', st0=st0, pops=pops.pop(), args=sorted(rd.args),
                inregs=sorted(rd.inregs, key=REGS32.index), fargs=sorted(rd.fargs), uses_sp=rd.uses_sp,
                uses_self='self' in '\n'.join(lines) + (ret or '') + (st0 or ''))
