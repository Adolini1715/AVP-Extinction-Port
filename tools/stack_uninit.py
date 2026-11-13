#!/usr/bin/env python3
"""Find original functions that read their own stack frame before writing it.

    stack_uninit.py [va ...] [--all] [--json OUT]

A read of a local slot (below the entry esp) that no instruction of the
function wrote on every path to it sees whatever an earlier call left there.
In the all-lifted build that is the original's garbage; in the hybrid build a
decompiled function that ran at that depth earlier may have left different
bytes (its GuestFrame reserves the pushes and locals without writing them, its
calls push 0xDEC0DE00 as the return address), and the read diverges. Two
replay failures came from this (notes/wave1.md: 0x119920, 0xAA67A) and one
held-back function (0xB9DA0, notes/Menu.md).

The analysis is a forward must-write dataflow over each function's machine
code: offsets relative to the entry esp (0 = return address, +4 = first
argument). It tracks esp and ebp (mov ebp, esp / leave), push/pop, sub/add
esp, calls (callee pops from symbols/abi.json; an indirect call followed by
`add esp` is cdecl, else the bytes pushed since the last call are popped).
Writes are pushes and memory operands with write access; bytes below esp are
dead (a pop or add esp forgets them). Taking a local's address (lea, mov reg,
esp) counts as writing the 0x40 bytes after it (out buffers). Paths it cannot
follow (alloca, and esp, -16) are dropped and the function is marked.

Output: one line per function with findings, "va file status reads", where
reads are insn:offset/size. status is decomp (a C++ body exists) or lifted.
"""
import bisect, json, os, re, sys, csv
import capstone
from capstone import x86

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
abi = {int(k, 16): v for k, v in json.load(open(os.path.join(R, 'symbols', 'abi.json'))).items()}
starts = sorted(abi)
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True
ESP, EBP = x86.X86_REG_ESP, x86.X86_REG_EBP
JCC = {'jo', 'jno', 'jb', 'jae', 'je', 'jne', 'jbe', 'ja', 'js', 'jns', 'jp', 'jnp', 'jl', 'jge', 'jle', 'jg',
       'jcxz', 'jecxz', 'loop', 'loope', 'loopne'}
ESCAPE = 0x40
# capstone marks these x87 stores' memory operand as read
FSTORE = {'fst', 'fstp', 'fist', 'fistp', 'fisttp', 'fnstsw', 'fstsw', 'fnstcw', 'fstcw', 'fbstp', 'fnsave',
          'fsave', 'fnstenv', 'fstenv'}


def access(ins, op):
    if ins.mnemonic in FSTORE:
        return capstone.CS_AC_WRITE
    return op.access


def read(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['vsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + min(n, s['va'] + s['vsize'] - va)]
    return b''


def fend(va):
    i = bisect.bisect_right(starts, va)
    return starts[i] if i < len(starts) else va + 0x1000


def decode(va, hi):
    code = read(va, hi - va)
    out = {}
    for ins in md.disasm(code, va):
        out[ins.address] = ins
    return out


def analyse(va):
    hi = fend(va)
    insns = decode(va, hi)
    # state: (esp, ebp or None, frozenset written, pushed_since)
    state = {va: (0, None, frozenset(), 0)}
    work = [va]
    reads = {}
    flags = set()
    iters = 0
    while work and iters < 200000:
        iters += 1
        pc = work.pop()
        if pc not in insns:
            continue
        esp, ebp, wr, since = state[pc]
        ins = insns[pc]
        m = ins.mnemonic
        wr = set(wr)
        succ = []
        nxt = pc + ins.size

        def mem_off(op):
            mm = op.mem
            if mm.index != 0:
                return None
            if mm.base == ESP:
                return esp + mm.disp
            if mm.base == EBP and ebp is not None:
                return ebp + mm.disp
            return None

        def kill_below(new_esp):
            return {b for b in wr if b >= new_esp}

        # reads first (with the esp before this instruction)
        if m != 'lea':
            for op in ins.operands:
                if op.type == x86.X86_OP_MEM and (access(ins, op) & capstone.CS_AC_READ or m in ('push',)):
                    o = mem_off(op)
                    if o is None or o >= 0:
                        continue
                    sz = op.size or 4
                    missing = [b for b in range(o, o + sz) if b < 0 and b not in wr]
                    if missing:
                        reads.setdefault(pc, (o, sz))
        # escapes
        if m == 'lea':
            op = ins.operands[1]
            o = mem_off(op)
            if op.mem.base in (ESP, EBP) and o is not None and o < 0:
                wr |= set(range(o, min(0, o + ESCAPE)))
            elif op.mem.base in (ESP, EBP):
                wr |= set(range(esp, 0))
        if m == 'mov' and len(ins.operands) == 2 and ins.operands[1].type == x86.X86_OP_REG and \
                ins.operands[1].reg == ESP and ins.operands[0].reg != EBP:
            wr |= set(range(esp, 0))
        # writes through memory operands
        if m not in ('push', 'lea', 'call'):
            for op in ins.operands:
                if op.type == x86.X86_OP_MEM and access(ins, op) & capstone.CS_AC_WRITE:
                    if op.mem.base == ESP and op.mem.index != 0:
                        wr |= set(range(esp, 0))
                        continue
                    o = mem_off(op)
                    if o is not None:
                        wr |= set(range(o, o + (op.size or 4)))
            if m.startswith('rep') or m in ('stosd', 'stosb', 'movsd', 'movsb', 'stosw', 'movsw'):
                pass  # destination via edi: covered when edi took a local's address (lea)
        # stack pointer effects
        stop = False
        if m == 'push':
            esp -= 4
            wr |= set(range(esp, esp + 4))
            since += 4
        elif m == 'pop':
            op = ins.operands[0]
            if op.type == x86.X86_OP_REG and op.reg == EBP:
                ebp = None
            esp += 4
            wr = kill_below(esp)
        elif m in ('pushal', 'pushad'):
            esp -= 32; wr |= set(range(esp, esp + 32)); since += 32
        elif m in ('popal', 'popad'):
            esp += 32; wr = kill_below(esp)
        elif m in ('pushfd', 'pushf'):
            esp -= 4; wr |= set(range(esp, esp + 4)); since += 4
        elif m in ('popfd', 'popf'):
            esp += 4; wr = kill_below(esp)
        elif m in ('sub', 'add') and ins.operands[0].type == x86.X86_OP_REG and ins.operands[0].reg == ESP:
            if ins.operands[1].type == x86.X86_OP_IMM:
                d = ins.operands[1].imm
                if m == 'sub':
                    esp -= d; since = 0
                else:
                    esp += d; wr = kill_below(esp); since = max(0, since - d)
            else:
                flags.add('alloca'); stop = True
        elif m == 'and' and ins.operands[0].type == x86.X86_OP_REG and ins.operands[0].reg == ESP:
            flags.add('aligned'); stop = True
        elif m == 'mov' and ins.operands[0].type == x86.X86_OP_REG and ins.operands[0].reg == EBP:
            src = ins.operands[1]
            ebp = esp if (src.type == x86.X86_OP_REG and src.reg == ESP) else None
        elif m == 'mov' and ins.operands[0].type == x86.X86_OP_REG and ins.operands[0].reg == ESP:
            src = ins.operands[1]
            if src.type == x86.X86_OP_REG and src.reg == EBP and ebp is not None:
                esp = ebp; wr = kill_below(esp)
            else:
                flags.add('esp'); stop = True
        elif m == 'leave':
            if ebp is None:
                stop = True
            else:
                esp = ebp + 4; ebp = None; wr = kill_below(esp)
        elif m == 'lea' and ins.operands[0].reg == ESP:
            src = ins.operands[1]
            if src.mem.base == ESP and src.mem.index == 0:
                d = src.mem.disp; esp += d
                if d > 0: wr = kill_below(esp)
            else:
                flags.add('esp'); stop = True
        elif any(op.type == x86.X86_OP_REG and op.reg == EBP and op.access & capstone.CS_AC_WRITE
                 for op in ins.operands) and m not in ('push', 'cmp', 'test'):
            ebp = None
        if m == 'call':
            op = ins.operands[0]
            pops = None
            if op.type == x86.X86_OP_IMM:
                t = op.imm & 0xFFFFFFFF
                if t in abi:
                    pops = abi[t]['pops']
            if pops is None:
                n2 = insns.get(nxt)
                if n2 is not None and n2.mnemonic == 'add' and n2.operands[0].type == x86.X86_OP_REG and \
                        n2.operands[0].reg == ESP:
                    pops = 0
                else:
                    pops = since
            esp += pops
            wr = kill_below(esp)
            since = 0
            succ.append(nxt)
        elif m in ('ret', 'retn', 'iret', 'int3', 'hlt', 'ud2'):
            pass
        elif m == 'jmp':
            op = ins.operands[0]
            if op.type == x86.X86_OP_IMM:
                t = op.imm & 0xFFFFFFFF
                if va <= t < hi:
                    succ.append(t)
            elif op.type == x86.X86_OP_MEM and op.mem.base == 0 and op.mem.scale == 4 and op.mem.disp:
                tab = op.mem.disp & 0xFFFFFFFF
                for k in range(512):
                    w = read(tab + 4 * k, 4)
                    if len(w) < 4: break
                    t = int.from_bytes(w, 'little')
                    if not (va <= t < hi): break
                    succ.append(t)
        elif m in JCC:
            op = ins.operands[0]
            if op.type == x86.X86_OP_IMM:
                t = op.imm & 0xFFFFFFFF
                if va <= t < hi:
                    succ.append(t)
            succ.append(nxt)
        else:
            succ.append(nxt)
        if stop:
            continue
        if m in JCC or m == 'jmp':
            since = 0                       # arguments are pushed after the last branch
        new = (esp, ebp, frozenset(wr), since)
        for s in succ:
            if s not in insns:
                continue
            old = state.get(s)
            if old is None:
                state[s] = new; work.append(s)
            else:
                if old[0] != esp:
                    flags.add('esp-merge')
                    continue
                inter = old[2] & new[2]
                eb = old[1] if old[1] == ebp else None
                if inter != old[2] or eb != old[1]:
                    state[s] = (esp, eb, inter, old[3]); work.append(s)
    # re-check reads against the final (fixpoint) states
    final = {}
    for pc, (o, sz) in reads.items():
        st = state.get(pc)
        if st is None:
            continue
        wr = st[2]
        if any(b < 0 and b not in wr for b in range(o, o + sz)):
            final[pc] = (o, sz)
    return final, flags


def decompiled():
    d = set()
    src = '/home/claude/avpe-decomp/src'
    for f in os.listdir(src):
        if f.endswith('.cpp'):
            for mm in re.finditer(r'^void sub_([0-9A-Fa-f]{8})\(void\)', open(os.path.join(src, f)).read(), re.M):
                d.add(int(mm.group(1), 16))
    return d


def main():
    a = sys.argv[1:]
    out_json = None
    if '--json' in a:
        i = a.index('--json'); out_json = a[i + 1]; del a[i:i + 2]
    files = {}
    for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv'))):
        files[int(r['xbox'], 16)] = r['file']
    if '--all' in a or not a:
        vas = [v for v in starts if v < 0x170000]
    else:
        vas = [int(x, 16) for x in a]
    dec = decompiled()
    res = {}
    for v in vas:
        try:
            f, fl = analyse(v)
        except Exception as e:
            print('%08X error %s' % (v, e), file=sys.stderr); continue
        if f:
            res[v] = (f, fl)
            print('%08X %-28s %-6s %s%s' % (v, files.get(v, '?'), 'decomp' if v in dec else 'lifted',
                                           ' '.join('%X:%d/%d' % (pc, o, sz) for pc, (o, sz) in sorted(f.items())),
                                           (' [' + ','.join(sorted(fl)) + ']') if fl else ''))
    if out_json:
        json.dump({'%08X' % v: {'reads': {'%08X' % pc: [o, sz] for pc, (o, sz) in f.items()},
                                'flags': sorted(fl), 'file': files.get(v, '?'), 'decomp': v in dec}
                   for v, (f, fl) in res.items()}, open(out_json, 'w'), indent=1)
    print('%d functions with uninitialised frame reads (%d decompiled)' %
          (len(res), sum(1 for v in res if v in dec)), file=sys.stderr)


if __name__ == '__main__':
    main()
