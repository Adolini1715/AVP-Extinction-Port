#!/usr/bin/env python3
"""Is a function's eax (or x87 state) result read by anyone? For eax waivers.

    eaxlive.py [--x87] <va> [...]
    eaxlive.py --from-paths [--md FILE]   every function whose recorded DIFFs
                                          (verified_paths.json) are all eax / x87
    eaxlive.py --md FILE <va> ...         also write the provably dead list to FILE

For each call site of the function in the original (call/jmp/jcc rel32 from
.text; indirect calls through a vtable slot when every data reference to the
function is a vtable slot in symbols/classes.json), walk forward from the
return address over the caller's code (capstone) until eax is written in
full on every path. A read of eax/ax/al/ah first (push eax, test al,
cmp, [eax+..], movzx .., al, a callee whose abi.json inputs include eax)
is a read. Reaching the caller's ret (or a tail jump) means the caller
returns that value: its own call sites are checked the same way (tail-call
chains, to depth 8).
--x87: the same for the x87 stack/status word: any x87 instruction (fstp,
fnstsw, fld ..) or a call before fninit is a read (a stack depth or TOP
difference changes everything after it).

Results: "dead" (no site reads it), "read at <site>, ...", or "unknown"
(a data reference that is not a vtable slot: a function pointer table, a
callback; no callers; recursion depth). Sound but conservative: "read" can
include a vtable site that never dispatches to this function.
"""
import bisect, json, os, re, struct, sys
import capstone
from capstone import x86

T = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(T, '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
abi = json.load(open(os.path.join(R, 'symbols', 'abi.json')))
starts = sorted(int(k, 16) for k in abi)
TEXT = [s for s in info['sections'] if s['name'].strip() == '.text'][0]
code = xbe[TEXT['raw']:TEXT['raw'] + TEXT['vsize']]
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True

# every rel32 call / jmp / jcc in .text: target -> [(site, kind)]
SITES = {}
for o in range(len(code) - 6):
    b = code[o]
    if b in (0xE8, 0xE9):
        t = (TEXT['va'] + o + 5 + struct.unpack_from('<i', code, o + 1)[0]) & 0xFFFFFFFF
        SITES.setdefault(t, []).append((TEXT['va'] + o, 'call' if b == 0xE8 else 'jmp'))
    elif b == 0x0F and 0x80 <= code[o + 1] <= 0x8F:
        t = (TEXT['va'] + o + 6 + struct.unpack_from('<i', code, o + 2)[0]) & 0xFFFFFFFF
        SITES.setdefault(t, []).append((TEXT['va'] + o, 'jcc'))
VSLOT = {}                                         # function -> {slot offset}, vtable slot address set
SLOTADDR = set()
for c in json.load(open(os.path.join(R, 'symbols', 'classes.json'))).values():
    for vt in c.get('vtables', []):
        base = int(vt['va'], 16)
        for i, s in enumerate(vt['slots']):
            VSLOT.setdefault(int(s, 16), set()).add(4 * i)
            SLOTADDR.add(base + 4 * i)


def file_to_va(off):
    for s in info['sections']:
        if s['raw'] <= off < s['raw'] + s['vsize']:
            return s['va'] + off - s['raw']
    return None


def ins_at(a):
    o = a - TEXT['va']
    if not 0 <= o < len(code):
        return None
    return next(md.disasm(code[o:o + 16], a), None)


def func_of(a):
    i = bisect.bisect_right(starts, a) - 1
    return starts[i], (starts[i + 1] if i + 1 < len(starts) else a + 0x1000)


_vsites = {}


def vtable_sites(off):
    """Indirect calls/jmps through [reg + off] anywhere in .text (any class)."""
    if off not in _vsites:
        out = []
        pat = re.compile(rb'\xFF[\x10-\x17\x20-\x27\x50-\x57\x60-\x67\x90-\x97\xA0-\xA7]', re.S)
        for m in pat.finditer(code):
            a = TEXT['va'] + m.start()
            i = ins_at(a)
            if i and i.mnemonic in ('call', 'jmp') and len(i.operands) == 1:
                op = i.operands[0]
                if op.type == x86.X86_OP_MEM and op.mem.base not in (0, x86.X86_REG_ESP, x86.X86_REG_EBP) \
                        and op.mem.index == 0 and op.mem.disp == off:
                    out.append((a, i.mnemonic))
        _vsites[off] = out
    return _vsites[off]


EAXS = {'eax', 'ax', 'al', 'ah'}
_memo = {}


def is_x87(i):
    return x86.X86_GRP_FPU in i.groups or i.mnemonic.startswith('f')


PARTS = {'al': {'l'}, 'ah': {'h'}, 'ax': {'l', 'h'}, 'eax': {'l', 'h', 'u'}}
FULL = frozenset('lhu')


def walk(site_after, fstart, fend, mode, killed0=frozenset()):
    """-> ('dead'|'read'|'ret', where) from the return address of one site.
    killed: the parts of eax (l = al, h = ah, u = upper 16 bits) already
    overwritten on this path; for 'ret', where = {killed set at each ret}."""
    seen, work, rets = set(), [(site_after, killed0)], set()
    while work:
        a, killed = work.pop()
        while True:
            if (a, killed) in seen or not fstart <= a < fend:
                break
            seen.add((a, killed))
            i = ins_at(a)
            if i is None:
                return 'read', '%X (undecodable)' % a
            m = i.mnemonic
            nxt = a + i.size
            if mode == 'x87':
                if m in ('fninit', 'finit'):
                    break
                if is_x87(i) or m in ('call', 'emms') or (m == 'jmp' and i.operands[0].type != x86.X86_OP_IMM):
                    return 'read', '%X %s %s' % (a, m, i.op_str)
            else:
                try:
                    rd, wr = i.regs_access()
                except capstone.CsError:
                    return 'read', '%X (no register info)' % a
                rd = {i.reg_name(r) for r in rd}; wr = {i.reg_name(r) for r in wr}
                if m in ('xor', 'sub') and len(i.operands) == 2 and i.operands[0].type == x86.X86_OP_REG \
                        and i.operands[0].reg == i.operands[1].reg and i.reg_name(i.operands[0].reg) in PARTS:
                    killed = killed | frozenset(PARTS[i.reg_name(i.operands[0].reg)])   # xor eax, eax
                    if killed == FULL:
                        break
                    a = nxt
                    continue
                if (m == 'or' and len(i.operands) == 2 and i.operands[1].type == x86.X86_OP_IMM
                        and i.operands[1].imm in (-1, 0xFFFFFFFF) or
                        m == 'and' and len(i.operands) == 2 and i.operands[1].type == x86.X86_OP_IMM
                        and i.operands[1].imm == 0) and i.operands[0].type == x86.X86_OP_REG \
                        and i.reg_name(i.operands[0].reg) == 'eax':
                    killed = FULL                                # or eax, -1 / and eax, 0
                    if killed == FULL:
                        break
                    a = nxt
                    continue
                if m == 'call':
                    op = i.operands[0]
                    if op.type == x86.X86_OP_IMM:
                        if 'eax' in abi.get('0x%08X' % (op.imm & 0xFFFFFFFF), {}).get('regs', []):
                            return 'read', '%X call %X (eax input)' % (a, op.imm)
                        break                                    # the callee's result replaces it
                    if any(PARTS[r] - killed for r in rd & EAXS):
                        return 'read', '%X %s %s' % (a, m, i.op_str)
                    break
                if any(PARTS[r] - killed for r in rd & EAXS):
                    return 'read', '%X %s %s' % (a, m, i.op_str)
                for r in wr & EAXS:
                    killed = killed | frozenset(PARTS[r])
                if killed == FULL:
                    break
            if m in ('ret', 'retf'):
                rets.add(killed); break
            if m == 'jmp' or m in ('ja', 'jae', 'jb', 'jbe', 'je', 'jne', 'jg', 'jge', 'jl', 'jle', 'jo', 'jno',
                                   'jp', 'jnp', 'js', 'jns', 'jecxz', 'jcxz', 'loop', 'loope', 'loopne'):
                op = i.operands[0]
                if op.type == x86.X86_OP_IMM:
                    t = op.imm & 0xFFFFFFFF
                    if fstart <= t < fend:
                        work.append((t, killed))
                    else:
                        if mode == 'eax' and 'eax' in abi.get('0x%08X' % t, {}).get('regs', []):
                            return 'read', '%X jmp %X (eax input)' % (a, t)
                        rets.add(killed)                         # tail jump: may hand eax on
                elif op.type == x86.X86_OP_MEM and op.mem.scale == 4 and op.mem.base == 0:
                    for k in range(512):                         # switch table
                        t = struct.unpack('<I', xbe_read(op.mem.disp + 4 * k))[0]
                        if not fstart <= t < fend:
                            break
                        work.append((t, killed))
                else:
                    rets.add(killed)                             # indirect tail jump
                if m == 'jmp':
                    break
            if m in ('int3', 'hlt', 'ud2'):
                break
            a = nxt
    return ('ret', rets) if rets else ('dead', None)


def xbe_read(va):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['vsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + 4]
    return b'\0\0\0\0'


_sites_memo = {}


def sites_of(va):
    """-> (list of (site, kind), problem or None)"""
    if va not in _sites_memo:
        _sites_memo[va] = _sites_of(va)
    return _sites_memo[va]


def _sites_of(va):
    sites = list(SITES.get(va, []))
    p = struct.pack('<I', va)
    j = -1
    other = []
    vt = False
    while True:
        j = xbe.find(p, j + 1)
        if j < 0:
            break
        a = file_to_va(j)
        if a is None:
            continue
        if a in SLOTADDR:
            vt = True
            continue
        other.append(a)
    if other:
        return sites, 'data reference at %s (function pointer)' % ', '.join('%X' % x for x in other[:4])
    if vt:
        for off in VSLOT.get(va, ()):
            sites += vtable_sites(off)
    return sites, None


_assumed = []


def live(va, mode='eax', depth=0, stack=(), killed=frozenset()):
    """-> ('dead', []) | ('read', [sites]) | ('unknown', [why]); killed: the
    parts of eax the function itself overwrote before returning."""
    key = (va, mode, killed)
    if key in _memo:
        return _memo[key]
    if (va, killed) in stack:
        _assumed.append((va, killed))                                  # least fixed point on recursion
        return 'dead', []
    if depth > 8:
        return 'unknown', ['tail-call chain deeper than 8 at %X' % va]
    mark = len(_assumed)
    sites, prob = sites_of(va)
    if prob:
        r = ('unknown', ['%X: %s' % (va, prob)])
        _memo[key] = r
        return r
    if not sites:
        r = ('unknown', ['%X: no callers found' % va])
        _memo[key] = r
        return r
    reads, unk = [], []
    for site, kind in sites:
        fs, fe = func_of(site)
        i = ins_at(site)
        if kind == 'call':
            st, w = walk(site + i.size, fs, fe, mode, killed)
        else:
            st, w = 'ret', {killed}                              # jmp / taken jcc: a tail call
        if st == 'read':
            reads.append('%X(in %X): %s' % (site, fs, w))
        elif st == 'ret':
            for k2 in w:
                s2, w2 = live(fs, mode, depth + 1, stack + ((va, killed),), k2)
                if s2 == 'read':
                    reads.append('%X(in %X) returns it -> %s' % (site, fs, w2[0])); break
                elif s2 == 'unknown':
                    unk.append('%X(in %X) returns it -> %s' % (site, fs, w2[0]))
        if reads:
            break                                                # one read decides it
    r = ('read', reads) if reads else ('unknown', unk) if unk else ('dead', [])
    # a result that leaned on an in-progress function is final only at the root
    # or when it is a read (reads do not depend on the assumption)
    if r[0] == 'read' or not stack or not [x for x in _assumed[mark:] if x != (va, killed)]:
        _memo[key] = r
    if not stack:
        del _assumed[mark:]
    return r


def candidates_from_paths():
    vp = json.load(open('/home/claude/avpe-decomp/verified_paths.json'))
    out = {}
    for k, e in vp.items():
        diffs = [d for c in e['checks'].values() if c['status'] != 'PASS' for d in c.get('diff', [])]
        if diffs and all(re.match(r'(eax|x87 stack|st0):', d) for d in diffs):
            out[int(k, 16)] = 'x87' if any(not d.startswith('eax') for d in diffs) else 'eax'
    return out


if __name__ == '__main__':
    argv = sys.argv[1:]
    mdout = None
    if '--md' in argv:
        i = argv.index('--md'); mdout = argv[i + 1]; del argv[i:i + 2]
    mode = 'x87' if '--x87' in argv else 'eax'
    todo = {}
    if '--from-paths' in argv:
        todo.update(candidates_from_paths())
    for a in argv:
        if not a.startswith('--'):
            v, _, mm = a.partition(':')
            todo[int(v, 16)] = mm or mode
    dead = []
    for va, mm in sorted(todo.items()):
        st, w = live(va, mm)
        print('%08X %-4s %s%s' % (va, mm, st, (': ' + '; '.join(w[:3])) if w else ''))
        if st == 'dead':
            dead.append((va, mm))
    print('%d of %d provably dead' % (len(dead), len(todo)))
    if mdout:
        open(mdout + '.dead', 'w').write(''.join('%08X %s\n' % d for d in dead))
