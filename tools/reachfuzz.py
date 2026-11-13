#!/usr/bin/env python3
"""reachfuzz.py: inputs that drive a game function down every path, found by
running the ORIGINAL machine code in Unicorn on a paused game's memory.

    reachfuzz.py explore MEM.BIN VA [VA ...] [--runs N] [--out FILE]

MEM.BIN is the whole guest memory of a paused replay (the fork server's
mem.bin, tools/sweepfork.py / forkprobe.py). The functions we cannot reach in
play are rarely unreachable in principle: their callers run, but never with
the state that takes their branches, or no object of their class exists at
the moments we pause. The explorer builds that state.

1. `this` and the arguments come from the function's ABI (symbols/abi.json)
   and its mangled signature: a class pointer gets a live object of that
   class from MEM.BIN (found by vtable), a reference to a vector / matrix a
   block of small floats, an HGOBJECT a live unit's handle, the rest 0.
   A method of a class with no live object gets one made by the class's own
   Create, run in Unicorn first (the probe repeats that call in the game).
2. Each run records the function's basic blocks (tools/bblocks.py), every
   cmp / test it executes with its operand values, and every memory word it
   reads. For a conditional branch seen going one way only, the operands of
   its compare are matched to the reads and inputs that produced them
   (input-to-state correspondence, as Redqueen): that word is set to the
   other operand, +-1, 0, 1, or a fresh block, and the run repeated.
3. A read through a null pointer (an address below 0x1000) gives the word
   that held the pointer a fresh zeroed block (lazy initialisation, as
   UC-KLEE): structures appear where the code expects them.
4. Inputs that add coverage join the corpus; the minimal set that covers
   what was reached is written as probe lines (verify_multi --probes
   syntax: pokes, registers, arguments; blocks as @S+offset, objects made
   by Create as @C<Create>+offset).

The explorer only decides WHAT to probe. Verification is unchanged: the
probes run in a fork of the same paused game on the decompiled code, and
verify_multi compares every byte against the original replayed in the
oracle from the probe's real entry state.
"""
import bisect, collections, csv, json, os, re, struct, sys
import capstone
from capstone import x86 as cx
from unicorn import Uc, UcError, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_MEM_READ, \
    UC_HOOK_MEM_UNMAPPED, UC_PROT_ALL, UC_HOOK_MEM_WRITE
from unicorn.x86_const import *

T = os.path.dirname(os.path.abspath(__file__))
R = os.path.join(T, '..')
sys.path.insert(0, T)
import bblocks

ABI = json.load(open(R + '/symbols/abi.json'))
DB = {int(r['xbox'], 16): r['name'] for r in csv.DictReader(open(R + '/symbols/xbox_symbols.csv'))}
ABI_STARTS = sorted(int(k, 16) for k in ABI)
_NR = json.load(open(R + '/recomp/noreturn.json'))     # calls that never return, spin loops: a run ends there
NORET_CALLEES = {int(k, 16) for k in _NR['callees']}
NORET_POINTS = {int(k, 16) for k in _NR['points']}
MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
MD.detail = True

RAM = 0x04000000
FS = 0x4000
STACK_TOP = 0x03F80000           # explorer's stack (inside the region the oracle keeps free)
LAZY = 0x03C00000                # lazy blocks: become @S+offset in probes (the bridge's scratch)
LAZY_N, LAZY_SZ = 64, 0x400      # 64 blocks of 1 KB = the bridge's 64 KB scratch
RET = 0xDEC0DE00                 # the probe's marker return address
KERNEL = 0xFE000000
GDT = 0xC0000000
REGS = {'eax': UC_X86_REG_EAX, 'ecx': UC_X86_REG_ECX, 'edx': UC_X86_REG_EDX, 'ebx': UC_X86_REG_EBX,
        'esi': UC_X86_REG_ESI, 'edi': UC_X86_REG_EDI, 'ebp': UC_X86_REG_EBP}
JCC = {'je', 'jne', 'jg', 'jge', 'jl', 'jle', 'ja', 'jae', 'jb', 'jbe', 'js', 'jns', 'jo', 'jno', 'jp', 'jnp',
       'jcxz', 'jecxz'}


def fn_end(va):
    i = bisect.bisect_right(ABI_STARTS, va)
    return ABI_STARTS[i] if i < len(ABI_STARTS) else va + 0x1000


# ---------------------------------------------------------------- the image
class Image:
    """MEM.BIN plus what the explorer needs to know about it: live objects by vtable."""

    def __init__(self, path):
        self.mem = open(path, "rb").read()
        assert len(self.mem) == RAM
        assert struct.unpack_from('<I', self.mem, FS + 0x18)[0] == FS, 'no TIB at 0x4000'
        C = json.load(open(R + '/symbols/classes.json'))
        self.parent = {c: d.get('parent') for c, d in C.items() if d.get('parent')}
        self.cls_vt = {}                                     # class -> its offset-0 vtable
        self.vt_cls = {}                                     # any vtable -> (class, offset)
        self.fn_vts = collections.defaultdict(set)           # function -> vtables holding it
        for c, d in C.items():
            for v in d.get('vtables', []):
                a = int(v['va'], 16)
                self.vt_cls[a] = (c, v['offset'])
                if v['offset'] == 0:
                    self.cls_vt.setdefault(c, a)
                for sl in v['slots']:
                    self.fn_vts[int(sl, 16)].add(a)
        import numpy as np
        words = np.frombuffer(self.mem, dtype='<u4')
        vts = np.array(sorted(self.vt_cls), dtype='<u4')
        idx = np.nonzero(np.isin(words, vts))[0]
        self.at_vt = collections.defaultdict(list)           # vtable -> addresses holding it (heap)
        self.objs = collections.defaultdict(list)            # class -> objects (offset-0 vtable)
        for i in idx:
            a = int(i) * 4
            if 0x00265D60 <= a < 0x03C00000:
                w = int(words[i])
                self.at_vt[w].append(a)
                c, off = self.vt_cls[w]
                if off == 0:
                    self.objs[c].append(a)
        self.words = words
        self._creates_init()
        # tiny real functions that only return (xor eax, eax first when there is one), by bytes
        # popped: a vtable slot or function pointer the explorer has to fill gets one of these,
        # with the pop count its call site implies (both builds then call the same real code)
        self.stubs = {}
        fl = json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
        for f in fl:
            a0 = int(f['start'], 16)
            if not (0x11000 <= a0 < 0x170D60) or int(f.get('size', 99)) > 64:
                continue
            ins = list(MD.disasm(self.mem[a0:a0 + 64], a0))
            ok, rank = True, 2
            for k, x in enumerate(ins):
                if x.mnemonic == 'ret':
                    n = x.operands[0].imm if x.operands else 0
                    zero = k == 1 and ins[0].mnemonic == 'xor' and ins[0].op_str in ('eax, eax', 'al, al')
                    r_ = 0 if zero else (1 if k == 0 else rank)
                    if ok and (n not in self.stubs or r_ < self.stubs[n][2]):
                        self.stubs[n] = (a0, zero, r_)
                    break
                # side-effect-free: register moves / loads, no calls, no writes, no esp changes
                if x.mnemonic in ('fld', 'fild', 'fldz', 'fld1', 'fmul', 'fadd', 'fsub', 'fdiv'):
                    rank = max(rank, 3)                 # leaves a value on the x87 stack: last resort
                elif x.mnemonic not in ('mov', 'xor', 'movzx', 'movsx', 'lea', 'and', 'or', 'neg', 'sete', 'setne', 'test', 'cmp', 'shl', 'shr', 'sar', 'add', 'sub', 'imul'):
                    ok = False
                elif x.operands and x.operands[0].type == cx.X86_OP_MEM:
                    ok = False
                elif 'esp' in x.op_str.split(',')[0]:
                    ok = False
        # fallback: the smallest real function at the same vtable slot with the same pop count
        sizes = {int(f['start'], 16): int(f['size']) for f in fl}
        self.slot_pool = {}
        for c, d in C.items():
            for v in d.get('vtables', []):
                for k, sl in enumerate(v['slots']):
                    fa = int(sl, 16)
                    pops = ABI.get('0x%08X' % fa, {}).get('pops')
                    if pops is None or fa not in sizes:
                        continue
                    key = (4 * k, pops)
                    if key not in self.slot_pool or sizes[fa] < sizes[self.slot_pool[key]]:
                        self.slot_pool[key] = fa
        cnt = collections.defaultdict(collections.Counter)
        for c, d in C.items():
            for v in d.get('vtables', []):
                for k, sl in enumerate(v['slots']):
                    pops = ABI.get('0x%08X' % int(sl, 16), {}).get('pops')
                    if pops is not None:
                        cnt[4 * k][pops] += 1
        self.slot_pops = {k: c.most_common(1)[0][0] for k, c in cnt.items()}
        self.slot_pops_all = {k: [p for p, _ in c.most_common()] for k, c in cnt.items()}   # every count a class uses there
        self.by_pops = {}
        for fa, sz in sizes.items():
            pops = ABI.get('0x%08X' % fa, {}).get('pops')
            if pops is not None and 0x11000 <= fa < 0x170D60 and (pops not in self.by_pops or sz < sizes[self.by_pops[pops]]):
                self.by_pops[pops] = fa

    def stub_for(self, n, slot_off=None):
        if n in self.stubs:
            return self.stubs[n][0]
        if slot_off is not None and (slot_off, n) in self.slot_pool:
            return self.slot_pool[(slot_off, n)]
        return self.by_pops.get(n)

    def _creates_init(self):
        self.creates = {}                                    # class -> its Create
        for va, n in DB.items():
            m = re.match(r'^Create__(\d+)(\w+?)FP7GObject', n)
            if m:
                self.creates[m.group(2)[:int(m.group(1))]] = va

    def entry_of(self, create):
        """The ClassTypeEntry (GObject::AddClassId) whose factory (+0x10) is create."""
        import numpy as np
        for i in np.nonzero(self.words == create)[0]:
            e = int(i) * 4 - 0x10
            if e >= 0x00265D60 and 0x10000 <= int(self.words[e // 4 + 1]) < RAM:
                return e, int(self.words[e // 4 + 5])
        return None, None

    def subclasses(self, c):
        out = {c}
        changed = True
        while changed:
            changed = False
            for k, p in self.parent.items():
                if p in out and k not in out:
                    out.add(k); changed = True
        return out

    def object_of(self, c, k=0):
        cands = []
        for s in sorted(self.subclasses(c)):
            cands += self.objs.get(s, [])
        return cands[k] if len(cands) > k else None


# ---------------------------------------------------------------- one run
# kernel imports by thunk slot, and the calls the explorer (like the oracle) answers: ordinal -> (args, eax)
_xi = json.load(open(R + '/xbe_info.json'))
_xb = open(R + '/disc/default.xbe', 'rb').read()
_kt = _xi['hdr']['kernel_thunk']
_ks = [s for s in _xi['sections'] if s['va'] <= _kt < s['va'] + s['vsize']][0]
KORD = []
while struct.unpack_from('<I', _xb, _ks['raw'] + _kt - _ks['va'] + 4 * len(KORD))[0]:
    KORD.append(struct.unpack_from('<I', _xb, _ks['raw'] + _kt - _ks['va'] + 4 * len(KORD))[0] & 0x7FFFFFFF)
KCALLS = {277: (1, 0), 294: (1, 0), 291: (1, 0), 129: (0, 0), 160: (0, 0), 161: (0, 0), 99: (3, 0), 171: (1, 0)}


STUB = os.environ.get('RF_STUB', '1') == '1'     # callees return at once (compositional verification)
XDK_STARTS = {int(f['start'], 16) for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
              if int(f['start'], 16) >= 0x170D60}


class Run:
    def __init__(self, img, va, inp, budget=3_000_000, mem=None):
        self.img, self.va, self.inp = img, va, inp
        self.base = mem if mem is not None else inp.get('mem', img.mem)
        self.end = fn_end(va)
        self.blocks = set(bblocks.blocks(va))
        self.hit = set()
        self.cmps = []            # (addr, [(kind, loc, value)], next_jcc_addr, seq)
        self.reads = collections.deque(maxlen=20000)   # (addr_of_insn, location, size, value, seq) by the function itself
        self.seq = 0
        self.nullref = None       # (insn, address) of the last read below 0x1000
        self.stop = 'ret'
        self.budget = budget
        self.icount = 0
        self.lazy_used = set()
        self.lastcall = None
        self.stubcalls = []

    def pops_ok(self, caddr, sp):
        """The pop counts a callee of the call at caddr may have: frame_pops, less any
        registers saved under the arguments (push ebx; push edi; push arg; push arg: the
        registers the function pops again after the call). A wrong one leaves the stack
        off and the run does not come back."""
        n = self.frame_pops(caddr, sp)
        saves = sum(1 for a_, x in self.insns.items() if a_ > caddr and x.mnemonic == 'pop'
                    and x.op_str in ('ebx', 'esi', 'edi', 'ebp'))
        ok = {n - 4 * j for j in range(saves + 1) if n - 4 * j >= 0}
        # (a register argument pushed unchanged (push esi, esi an input) looks like a save to
        # frame_pops: the pushes just before the call are the other reading)
        m = call_pops(self, caddr)
        if m and m != n:
            ok |= {m - 4 * j for j in range(saves + 1) if m - 4 * j >= 0}
        return ok

    def frame_pops(self, caddr, sp):
        """Bytes the callee of the call at caddr must pop, with esp = sp at the call:
        0 when the caller cleans up; else the pushes between the frame base and sp."""
        addrs = sorted(self.insns)
        k = addrs.index(caddr) if caddr in self.insns else -1
        if 0 <= k < len(addrs) - 1:
            nx = self.insns[addrs[k + 1]]
            if nx.mnemonic == 'add' and nx.op_str.startswith('esp,'):
                return 0
        d = (self.f_base - sp) & 0xFFFFFFFF
        if d <= 0x40 and d % 4 == 0:
            return d
        return call_pops(self, caddr)

    def go(self):
        img = self.img
        uc = Uc(UC_ARCH_X86, UC_MODE_32)
        uc.mem_map(0, RAM)
        uc.mem_write(0, self.base)
        uc.mem_map(GDT, 0x1000)
        uc.mem_map(RET & ~0xFFF, 0x1000)
        uc.mem_map(KERNEL, 0x10000)

        def desc(base, limit, access, flags):
            d = limit & 0xFFFF | (base & 0xFFFFFF) << 16 | access << 40 | ((limit >> 16) & 0xF) << 48 | flags << 52 | (base >> 24) << 56
            return struct.pack('<Q', d)
        gdt = desc(0, 0, 0, 0) + desc(0, 0xFFFFF, 0x9A, 0xC) + desc(0, 0xFFFFF, 0x92, 0xC) + desc(FS, 0xFFFFF, 0x92, 0xC)
        uc.mem_write(GDT, gdt)
        uc.reg_write(UC_X86_REG_GDTR, (0, GDT, len(gdt) - 1, 0))
        uc.reg_write(UC_X86_REG_CS, 1 << 3); uc.reg_write(UC_X86_REG_SS, 2 << 3)
        uc.reg_write(UC_X86_REG_DS, 2 << 3); uc.reg_write(UC_X86_REG_ES, 2 << 3)
        uc.reg_write(UC_X86_REG_FS, 3 << 3)
        uc.reg_write(UC_X86_REG_FPCW, 0x027F)
        # inputs: pokes (lazy blocks are plain memory here), registers, stack arguments
        for a, v in self.inp.get('pokes', {}).items():
            uc.mem_write(a, struct.pack('<I', v & 0xFFFFFFFF))
        esp = STACK_TOP
        args = self.inp.get('args', [])
        for v in reversed(args):
            esp -= 4; uc.mem_write(esp, struct.pack('<I', v & 0xFFFFFFFF))
        esp -= 4; uc.mem_write(esp, struct.pack('<I', RET))
        self.argbase = esp + 4
        for r, v in self.inp.get('regs', {}).items():
            uc.reg_write(REGS[r], v & 0xFFFFFFFF)
        uc.reg_write(UC_X86_REG_ESP, esp)
        uc.reg_write(UC_X86_REG_EBP, STACK_TOP + 0x1000)
        self.uc = uc
        va, end = self.va, self.end
        insns = {}
        code = bytes(self.base[va:end]) if end - va < 0x10000 else b''
        for i in MD.disasm(code, va):
            insns[i.address] = i
        self.insns = insns
        pending_cmp = [None]
        # stack height: the frame base is esp after the prologue (register saves: a push of
        # ebx/ebp/esi/edi still holding its entry value; sub esp, imm) and before the first
        # call; the arguments of a call are what lies between the base and esp at the call
        self.f_entry = {r: uc.reg_read(REGS[r]) for r in ('ebx', 'ebp', 'esi', 'edi')}
        self.f_written, self.f_saved = set(), set()
        self.w_all, self.cmp_written = set(), {}
        self.f_base, self.f_calls = esp, 0

        def on_stop(uc, addr, size, _):
            if addr == RET:
                self.stop = 'ret'
            else:
                slot = (addr - KERNEL) // 4
                h = KCALLS.get(KORD[slot] if slot < len(KORD) else -1)
                if h is not None:                          # what the oracle and the bridge both do
                    sp = uc.reg_read(UC_X86_REG_ESP)
                    ra = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
                    uc.reg_write(UC_X86_REG_EAX, h[1]); uc.reg_write(UC_X86_REG_ESP, sp + 4 + 4 * h[0])
                    uc.reg_write(UC_X86_REG_EIP, ra)
                    return
                self.stop = 'kernel %d' % (KORD[slot] if slot < len(KORD) else -1)
            uc.emu_stop()

        def on_code(uc, addr, size, _):
            # only the function's own instructions come here (hook range)
            if addr in self.blocks:
                self.hit.add(addr)
            i = insns.get(addr)
            if i is None:
                return
            m = i.mnemonic
            if addr in NORET_POINTS or (m == 'call' and i.operands and i.operands[0].type == cx.X86_OP_IMM
                                        and (i.operands[0].imm & 0xFFFFFFFF) in NORET_CALLEES):
                self.stop = 'ret'; self.noret = addr; uc.emu_stop(); return   # compared there, as the probe is
            try:
                for r_ in i.regs_access()[1]:
                    self.w_all.add(_REG32.get(i.reg_name(r_), i.reg_name(r_)))
            except Exception:
                pass
            if m.startswith('cmp') or m == 'test':
                self.cmp_written[addr] = frozenset(self.w_all)
            if not self.f_calls:
                sp_ = uc.reg_read(UC_X86_REG_ESP)
                o_ = i.operands
                if m == 'push' and o_ and o_[0].type == cx.X86_OP_REG:
                    rn = i.reg_name(o_[0].reg)
                    if rn in self.f_entry and rn not in self.f_written and rn not in self.f_saved \
                            and uc.reg_read(REGS[rn]) == self.f_entry[rn]:
                        self.f_saved.add(rn); self.f_base = sp_ - 4
                elif m == 'sub' and len(o_) == 2 and o_[0].type == cx.X86_OP_REG and i.reg_name(o_[0].reg) == 'esp' \
                        and o_[1].type == cx.X86_OP_IMM:
                    self.f_base = sp_ - o_[1].imm
                elif m == 'and' and len(o_) == 2 and o_[0].type == cx.X86_OP_REG and i.reg_name(o_[0].reg) == 'esp' \
                        and o_[1].type == cx.X86_OP_IMM:
                    self.f_base = sp_ & o_[1].imm & 0xFFFFFFFF
                try:
                    for r_ in i.regs_access()[1]:
                        self.f_written.add(i.reg_name(r_))
                except Exception:
                    pass
                if m == 'call':
                    self.f_calls = 1
            # the words it reads (before the instruction runs: what it will see)
            for op in i.operands:
                # (a store's destination is not a read: a value found there is no input)
                if op.type == cx.X86_OP_MEM and m != 'lea' and op.size <= 4 and (op.access & capstone.CS_AC_READ):
                    a = _ea(uc, i, op)
                    try:
                        v = struct.unpack('<I', bytes(uc.mem_read(a, 4)))[0] & ((1 << (8 * op.size)) - 1)
                    except UcError:
                        continue
                    self.reads.append((addr, a, op.size, v, self.seq))
            if STUB and m in ('call', 'jmp') and i.operands:
                op = i.operands[0]
                if op.type == cx.X86_OP_IMM:
                    tgt = op.imm & 0xFFFFFFFF
                elif op.type == cx.X86_OP_MEM:
                    try:
                        tgt = struct.unpack('<I', bytes(uc.mem_read(_ea(uc, i, op), 4)))[0]
                    except UcError:
                        tgt = 0
                else:
                    tgt = uc.reg_read(_ucreg(op.reg))
                if m == 'call' or not (va <= tgt < end):
                    if tgt < 0x11000:
                        return                                  # a wild target: let the repair logic see it
                    bad_pops = False
                    if m == 'call' and op.type != cx.X86_OP_IMM and '0x%08X' % tgt in ABI:
                        # an indirect callee must pop what this call site leaves for it
                        bad_pops = (ABI['0x%08X' % tgt].get('pops') or 0) not in self.pops_ok(addr, uc.reg_read(UC_X86_REG_ESP))
                    if bad_pops or '0x%08X' % tgt not in ABI and not (0x170D60 <= tgt < 0x1C0000):
                        # not a function: the game build skips such a call while the original
                        # would run whatever is there, so no input may call it; stop as a wild
                        # call and let the repair put a real function in the pointer
                        if op.type == cx.X86_OP_MEM:
                            ea = _ea(uc, i, op)
                            if op.mem.base == cx.X86_REG_ESP and m == 'call':
                                pass                            # esp not yet moved: ea is the slot
                            loc = ('mem', ea)
                        else:
                            loc = ('regval', tgt)
                        self.stub_wild = (loc, self.frame_pops(addr, uc.reg_read(UC_X86_REG_ESP)) if m == 'call' else 0)
                        self.wild_okpops = self.pops_ok(addr, uc.reg_read(UC_X86_REG_ESP)) if m == 'call' else {0}
                        self.wild_disp = op.mem.disp if op.type == cx.X86_OP_MEM and op.mem.base != cx.X86_REG_ESP else None
                        self.stop = 'wild'; uc.emu_stop(); return
                    k = len(self.stubcalls)
                    rets_ = self.inp.get('rets', {})
                    rv = rets_[k] if k in rets_ else self.inp.get('srets', {}).get(addr + i.size, 0)
                    pops = ABI.get('0x%08X' % tgt, {}).get('pops')
                    if pops is None:
                        pops = self.frame_pops(addr, uc.reg_read(UC_X86_REG_ESP)) if m == 'call' else 0
                    self.stubcalls.append((k, addr, tgt, self.seq))
                    sp = uc.reg_read(UC_X86_REG_ESP)
                    if m == 'call':
                        uc.reg_write(UC_X86_REG_ESP, sp + pops); uc.reg_write(UC_X86_REG_EIP, addr + i.size)
                    else:                                       # tail jump: the callee returns for the function
                        ra = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
                        uc.reg_write(UC_X86_REG_ESP, sp + 4 + pops); uc.reg_write(UC_X86_REG_EIP, ra)
                    uc.reg_write(UC_X86_REG_EAX, rv)
                    return
            if m == 'call' and i.operands and i.operands[0].type != cx.X86_OP_IMM:
                op = i.operands[0]
                if op.type == cx.X86_OP_MEM:
                    ea = _ea(uc, i, op)
                    try:
                        v = struct.unpack('<I', bytes(uc.mem_read(ea, 4)))[0]
                    except UcError:
                        v = None
                    self.lastcall = (addr, 'mem', ea, v)
                else:
                    self.lastcall = (addr, 'reg', i.reg_name(op.reg), uc.reg_read(_ucreg(op.reg)))
            if m in ('cmp', 'test'):
                ops = []
                for op in i.operands:
                    if op.type == cx.X86_OP_REG:
                        try:
                            v = uc.reg_read(_ucreg(op.reg))
                        except Exception:
                            v = None
                        ops.append(('reg', i.reg_name(op.reg), v))
                    elif op.type == cx.X86_OP_IMM:
                        ops.append(('imm', None, op.imm & 0xFFFFFFFF))
                    elif op.type == cx.X86_OP_MEM:
                        a = _ea(uc, i, op)
                        try:
                            v = struct.unpack('<I', bytes(uc.mem_read(a, 4)))[0] & ((1 << (8 * op.size)) - 1)
                        except UcError:
                            v = None
                        ops.append(('mem', a, v))
                pending_cmp[0] = (addr, ops)
            elif m in JCC and len(self.cmps) >= 4000:
                self.seq += 1                                # a long loop: the first branches are enough
            elif m in JCC:
                if pending_cmp[0]:
                    self.cmps.append((pending_cmp[0][0], pending_cmp[0][1], addr, self.seq))
                    pending_cmp[0] = None
                else:
                    self.cmps.append((addr, [], addr, self.seq))
                self.seq += 1

        def on_read(uc, access, addr, size, value, _):
            # the last read below a page: the one nearest the failure (a first one is often
            # harmless, as a test of a field that is zero)
            self.nullref = (uc.reg_read(UC_X86_REG_EIP), addr)
            self.nullref_n = len(self.reads) if len(self.reads) < self.reads.maxlen else None   # the reads up to it

        ring = collections.deque(maxlen=96)               # (address, value) of recent 4-byte reads, all code
        self.ring = ring

        def on_any_read(uc, access, addr, size, value, _):
            if size == 4 and 0x10000 <= addr < RAM and not (STACK_TOP - 0x4000 <= addr < STACK_TOP + 0x2000):
                try:
                    ring.append((addr, struct.unpack('<I', bytes(uc.mem_read(addr, 4)))[0]))
                except UcError:
                    pass

        lastb = collections.deque(maxlen=3)
        self.lastb = lastb

        def on_block(uc, addr, size, _):
            lastb.append((addr, size))

        def on_unmapped(uc, access, addr, size, value, _):
            # only what the runtime really maps outside guest RAM: the NV2A
            # registers and the contiguous window (zeros here). Anything else
            # crashes the real process, so the input is useless as a probe.
            if 0xFD000000 <= addr < 0xFE000000 or 0x80000000 <= addr < 0x84000000:
                try:
                    uc.mem_map(addr & ~0xFFF, 0x1000)
                    return True
                except UcError:
                    return False
            self.stop = 'fault %08X' % addr
            return False

        uc.hook_add(UC_HOOK_CODE, on_code, begin=va, end=end - 1)
        uc.hook_add(UC_HOOK_CODE, on_stop, begin=RET, end=RET)

        def on_wild(uc, addr, size, _):
            self.wild_esp = (uc.reg_read(UC_X86_REG_ESP) + 4) & 0xFFFFFFFF   # esp at the call (before the return address)
            self.stop = 'wild'; uc.emu_stop()
        uc.hook_add(UC_HOOK_CODE, on_wild, begin=0, end=0x10FFF)
        uc.hook_add(UC_HOOK_CODE, on_stop, begin=KERNEL, end=KERNEL + 0xFFFF)

        def on_xdk(uc, addr, size, _):
            # Xbox library code: the platform layer serves it in the native build and the
            # oracle replays what the native run returned; here it just returns (0)
            if addr not in XDK_STARTS:
                return
            sp = uc.reg_read(UC_X86_REG_ESP)
            ra = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
            pops = ABI.get('0x%08X' % addr, {}).get('pops') or 0
            uc.reg_write(UC_X86_REG_EAX, 0); uc.reg_write(UC_X86_REG_ESP, sp + 4 + pops)
            uc.reg_write(UC_X86_REG_EIP, ra)
        uc.hook_add(UC_HOOK_CODE, on_xdk, begin=0x170D60, end=0x1C0000)
        uc.hook_add(UC_HOOK_MEM_READ, on_read, begin=0, end=0xFFF)
        from unicorn import UC_HOOK_BLOCK
        uc.hook_add(UC_HOOK_MEM_READ, on_any_read)
        uc.hook_add(UC_HOOK_BLOCK, on_block)
        uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_unmapped)
        if os.environ.get('RF_DEBUG'):
            from unicorn import UC_HOOK_BLOCK
            self.bcount = collections.Counter()
            uc.hook_add(UC_HOOK_BLOCK, lambda uc, a, s_, _: self.bcount.update([a]))
        self.stop = 'budget'
        try:
            uc.emu_start(va, RET, timeout=4_000_000, count=self.budget)   # 4 s: a rep with a junk count
        except UcError as e:
            self.stop = 'fault %s at %08X' % (e, uc.reg_read(UC_X86_REG_EIP))
        self.last_eip = uc.reg_read(UC_X86_REG_EIP)
        if self.stop == 'budget' and uc.reg_read(UC_X86_REG_EIP) == RET:
            self.stop = 'ret'
        if self.stop == 'ret' and self.nullref and not getattr(self, 'noret', None):
            # a read below 0x1000 faults on the Xbox (page 0 is not mapped): no input
            self.stop = 'nullread'
        self.eax = uc.reg_read(UC_X86_REG_EAX)
        self.lastb = list(lastb)[-16:]
        self.wildfrom = None
        if self.stop == 'wild' and getattr(self, 'stub_wild', None):
            self.wildfrom = self.stub_wild
        elif self.stop == 'wild' and len(lastb) >= 2:
            self.wildfrom = transfer_of(self, uc, lastb[-2])
        if getattr(self, 'keep_mem', False):
            self.after = bytes(uc.mem_read(0, RAM))
        self.uc = None                                # 64 MB each: never keep the emulator
        del uc
        return self


_REGMAP = {}


def _ucreg(capreg):
    if not _REGMAP:
        for nm in dir(cx):
            if nm.startswith('X86_REG_'):
                u = 'UC_' + nm
                if u in globals():
                    _REGMAP[getattr(cx, nm)] = globals()[u]
    return _REGMAP[capreg]


def _ea(uc, i, op):
    m = op.mem
    a = m.disp
    if m.base:
        a += uc.reg_read(_ucreg(m.base))
    if m.index:
        a += uc.reg_read(_ucreg(m.index)) * m.scale
    if m.segment == cx.X86_REG_FS:
        a += FS
    return a & 0xFFFFFFFF


# ---------------------------------------------------------------- inputs from the signature
def sig_args(name):
    """Mangled CodeWarrior/GCC2-style parameter list -> list of kinds:
    ('ptr', Class) / ('ref', Class) / ('handle',) / ('int',) / ('float',)."""
    m = re.match(r'^\w+?__(\d+)(\w+)$', name)
    if not m:
        return None, []
    n = int(m.group(1)); cls = m.group(2)[:n]; rest = m.group(2)[n:]
    if not rest.startswith('F'):
        return cls, []
    s = rest[1:]
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == 'v':
            break
        if c in 'PR':
            kind = 'ptr' if c == 'P' else 'ref'
            i += 1
            while i < len(s) and s[i] in 'CPR':
                i += 1
            if i < len(s) and s[i].isdigit():
                j = i
                while s[j].isdigit():
                    j += 1
                k = int(s[i:j]); out.append((kind, s[j:j + k])); i = j + k
            else:
                out.append((kind, s[i] if i < len(s) else '?')); i += 1
        elif c.isdigit():
            j = i
            while s[j].isdigit():
                j += 1
            k = int(s[i:j]); t = s[j:j + k]; i = j + k
            out.append(('handle',) if t == 'HGOBJECT' else ('obj', t))
        elif c in 'fd':
            out.append(('float',)); i += 1
        elif c in 'U':
            i += 1
        elif c == 'C':
            i += 1
        else:
            out.append(('int',)); i += 1
    return cls, out


class Lazy:
    """The 1 KB blocks (the bridge's scratch) an input uses: a new one is the lowest free."""
    def __init__(self):
        self.used = set()
        self.keep = set()        # the further blocks of a multi-block object (no value points into them)
        self.peek = False        # mutations: every candidate gets the same next block, none is used up

    @property
    def n(self):
        return max(self.used) + 1 if self.used else 0

    def new(self, n=1):
        """n blocks in a row (an object bigger than one block: a unit is 3.5 KB)."""
        k = next((i for i in range(LAZY_N - n + 1) if not any(j in self.used for j in range(i, i + n))), None)
        if k is None:
            return None
        if not self.peek:
            self.used.update(range(k, k + n))
            self.keep.update(range(k + 1, k + n))
        return LAZY + LAZY_SZ * k


def _lazy_used(c):
    """The blocks an input refers to are its used set (blocks a repair replaced are free again)."""
    used = set()
    vals = list(c['pokes'].items()) + [(0, v) for v in c['regs'].values()] + [(0, v) for v in c['args']] \
        + [(0, v) for v in c.get('rets', {}).values()] + [(0, v) for v in c.get('srets', {}).values()]
    for k, v in vals:
        for x in (k, v):
            if LAZY <= x < LAZY + LAZY_N * LAZY_SZ:
                used.add((x - LAZY) // LAZY_SZ)
    c['lazy'].used = used | c['lazy'].keep


def base_input(img, va):
    """The first input: this, registers and arguments from ABI and signature."""
    abi = ABI.get('0x%08X' % va, {})
    regs_in = abi.get('regs', [])
    pops = abi.get('pops') or 0
    name = DB.get(va, '')
    cls, params = sig_args(name)
    inp = {'regs': {}, 'args': [], 'pokes': {}, 'notes': []}
    this = None
    holders = [h for vt in sorted(img.fn_vts.get(va, ())) for h in img.at_vt.get(vt, [])]
    if holders:
        this = holders[0]
    if cls and this is None:
        this = img.object_of(cls)
        if this is None:
            made = make_object(img, cls)
            if made:
                this, inp['mem'], inp['made'] = made
                transplant(img, cls, inp)
            elif cls in img.cls_vt:
                lz0 = inp.setdefault('lazy0', Lazy())
                this = lz0.new(4)                      # (room for a unit's fields)
                inp['pokes'][this] = img.cls_vt[cls]
                inp['notes'].append('no live %s and no factory: a block with its vtable' % cls)
            else:
                # no vtable to go by: an empty block, its fields inputs like any memory
                lz0 = inp.setdefault('lazy0', Lazy())
                this = lz0.new(4)
                inp['notes'].append('no live %s and no factory: an empty block' % cls)
    # a member function that reads no register and pops one word more than its
    # parameters takes this on the stack, as the first argument
    if this is None and 'ecx' in regs_in:
        # a function that reads ecx and no class to go by (unnamed): an empty block for this
        lz0 = inp.setdefault('lazy0', Lazy())
        this = lz0.new()
        inp['notes'].append('ecx in, no class: an empty block')
    this_on_stack = bool(cls) and not regs_in and pops == 4 * (len(params) + 1)
    thisreg = 'ecx' if 'ecx' in regs_in else (regs_in[0] if regs_in else 'ecx')
    if this is not None and not this_on_stack:
        inp['regs'][thisreg] = this
    for r in regs_in:
        inp['regs'].setdefault(r, 0)
    nargs = pops // 4 if pops else len(params)
    lz = inp.pop('lazy0', None) or Lazy()
    inp['lazy'] = lz
    # an input register the function reads memory through before it writes it (a this in
    # esi, a constructor's new object): a block, not null
    if not cls:
        written = set()
        for x in MD.disasm(img.mem[va:va + 160], va):
            for o in x.operands:
                if o.type == cx.X86_OP_MEM and o.mem.base:
                    rn = x.reg_name(o.mem.base)
                    if rn in regs_in and rn not in written and inp['regs'].get(rn) == 0:
                        b = lz.new(4)
                        if b is not None:
                            inp['regs'][rn] = b
                            inp['notes'].append('%s is read through: a block' % rn)
            try:
                written |= {x.reg_name(r_) for r_ in x.regs_access()[1]}
            except Exception:
                pass
            if x.mnemonic in ('ret', 'jmp') or x.mnemonic.startswith('j'):
                break
    if this_on_stack:
        inp['args'].append(this or 0)
        inp['notes'].append('this on the stack')
        nargs -= 1
    for k in range(nargs):
        p = params[k] if k < len(params) else ('int',)
        v = 0
        if p[0] in ('ptr', 'ref'):
            o = img.object_of(p[1]) if len(p) > 1 and p[1] in img.cls_vt else None
            if o is not None:
                v = o
            else:
                b = lz.new()
                if b is not None:
                    v = b
                    if len(p) > 1 and p[1] in img.cls_vt:
                        inp['pokes'][b] = img.cls_vt[p[1]]
                    if p[1] in ('CVector', 'fRECT', 'CMatrix', 'RECT'):
                        for w in range(0, 0x40, 4):
                            inp['pokes'][b + w] = struct.unpack('<I', struct.pack('<f', 1.0 + w))[0]
        elif p[0] == 'handle':
            u = img.object_of('GUnit')
            v = struct.unpack_from('<I', img.mem, u + 0x18)[0] if u else 0
        elif p[0] == 'float':
            v = struct.unpack('<I', struct.pack('<f', 1.0))[0]
        inp['args'].append(v)
    if not pops:                                     # cdecl: the caller cleans up, so spare stack words cost
        inp['args'] += [0] * 4                       # nothing and catch arguments the signature does not show
    return inp


def make_object(img, cls):
    """An object of cls (or of a concrete subclass), made by its Create in
    Unicorn as the probe will make it in the game: Create(parent 0, entry, no
    handle, no fill data, no load buffer). -> (object, memory after, (create,
    entry, lo, hi)) or None."""
    for c in [cls] + sorted(img.subclasses(cls) - {cls}):
        cr = img.creates.get(c)
        if not cr:
            continue
        e, size = img.entry_of(cr)
        if e is None:
            continue
        r = Run(img, cr, {'regs': {'ecx': 0, 'edx': e}, 'args': [0, 0, 0], 'pokes': {}, 'notes': [], 'lazy': Lazy()}, mem=img.mem)
        r.keep_mem = True
        r.blocks = set()
        r.go()
        if r.stop == 'ret' and 0x1000 <= r.eax < 0x03C00000:
            size = size if size and 0x34 <= size <= 0x4000 else 0x200
            return r.eax, r.after, (cr, e, r.eax, r.eax + size)
    return None


def ancestors(img, c):
    out = []
    while c in img.parent:
        c = img.parent[c]; out.append(c)
    return out


def transplant(img, cls, inp):
    """A made object has the constructor's defaults: no owner, no target,
    empty lists. Its base-class fields are taken from a live object of the
    closest relative (a class sharing the deepest base): the words after the
    GObject header (0x34) up to the smaller of the two sizes. The made
    object keeps its own vtable, handle, links and class entry."""
    cr, e, lo, hi = inp['made']
    for anc in ancestors(img, cls):
        if anc in ('GObject', None):
            break
        rel = None
        for d in sorted(img.subclasses(anc)):
            if d != cls and img.objs.get(d):
                rel = (d, img.objs[d][0]); break
        if not rel:
            continue
        rc = img.creates.get(rel[0])
        rsize = img.entry_of(rc)[1] if rc else None
        n = min(hi - lo, rsize or (hi - lo))
        asz = img.entry_of(img.creates[anc])[1] if anc in img.creates and img.entry_of(img.creates[anc])[0] else None
        if asz:
            n = min(n, asz)
        mem = inp['mem']
        n = min(n, 0x1000, RAM - lo, RAM - rel[1])
        for off in range(0x34, n & ~3, 4):
            v = struct.unpack_from('<I', img.mem, rel[1] + off)[0]
            if v != struct.unpack_from('<I', mem, lo + off)[0]:
                inp['pokes'][lo + off] = v
        inp['notes'].append('fields 0x34..0x%X from live %s %08X' % (n, rel[0], rel[1]))
        return


def clone(inp):
    c = {'regs': dict(inp['regs']), 'args': list(inp['args']), 'pokes': dict(inp['pokes']),
         'notes': list(inp['notes']), 'lazy': Lazy()}
    for k in ('mem', 'made'):
        if k in inp:
            c[k] = inp[k]
    c['lazy'].used = set(inp['lazy'].used)
    c['lazy'].keep = set(inp['lazy'].keep)
    c['depth'] = inp.get('depth', 0)
    c['rets'] = dict(inp.get('rets', {}))
    c['srets'] = dict(inp.get('srets', {}))
    return c


# ---------------------------------------------------------------- the search
_REG32 = {r: r for r in ('eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp')}
_REG32.update({'al': 'eax', 'cl': 'ecx', 'dl': 'edx', 'bl': 'ebx', 'ax': 'eax', 'cx': 'ecx', 'dx': 'edx', 'bx': 'ebx',
               'si': 'esi', 'di': 'edi'})
_REGMASK = {'al': 0xFF, 'cl': 0xFF, 'dl': 0xFF, 'bl': 0xFF, 'ax': 0xFFFF, 'cx': 0xFFFF, 'dx': 0xFFFF, 'bx': 0xFFFF,
            'si': 0xFFFF, 'di': 0xFFFF}


def locate(run, value, size=4, before=None):
    """Where an operand value came from: the latest read by the function
    (up to the instruction that used it: before = its seq) whose value
    matches, else an input register or argument."""
    mask = (1 << (8 * size)) - 1
    for pc, a, sz, v, _q in reversed(run.reads):
        if before is not None and _q > before:
            continue
        if v & mask == value & mask and not (STACK_TOP - 0x2000 <= a < run.argbase):
            if run.argbase <= a < run.argbase + 4 * len(run.inp['args']):
                return ('arg', (a - run.argbase) // 4)
            return ('mem', a)
    for r, v in run.inp['regs'].items():
        if v & mask == value & mask:
            return ('reg', r)
    return None


def apply(inp, loc, v):
    c = clone(inp)
    k, x = loc
    if k == 'mem' and (x < 0x10000 or x >= RAM):      # low memory (TIB, image header) is shared by everything
        return c
    if k == 'mem' and (0x11000 <= x < 0x170D60 or 0x2050C0 <= x < 0x265D50):
        return c                                      # .text, .rdata: read-only in the XBE, no input changes a constant
    if k == 'mem' and STACK_TOP - 0x10000 <= x < STACK_TOP + 0x2000:
        return c                                      # the explorer's own stack: no input of the probe's
    if k == 'mem':
        c['pokes'][x] = v & 0xFFFFFFFF
    elif k == 'arg':
        c['args'][x] = v & 0xFFFFFFFF
    else:
        c['regs'][x] = v & 0xFFFFFFFF
    return c


def mutations(run):
    """Inputs that may take the branches this run left one-sided."""
    run.inp['lazy'].peek = True
    try:
        out = _mutations(run)
    finally:
        run.inp['lazy'].peek = False
    for _t, c in out:
        c['lazy'].peek = False
        _lazy_used(c)
    return out


def _mutations(run):
    out = []
    wf_ = getattr(run, 'wildfrom', None)
    callslot = wf_[0][1] if run.stop == 'wild' and wf_ and wf_[0][0] == 'mem' else None   # a function goes there, not a block
    # the null-pointer repairs answer a failure that is a null access (a fault, a call
    # through a slot near 0), not any earlier read of a zero field
    mem_src = [None]                                # the null pointer was loaded from memory (there)
    chain = [False]                                 # ... through a pointer argument
    nullfail = run.nullref and (run.stop.startswith('fault') or run.stop == 'nullread' or
                                (callslot is not None and callslot < 0x1000) or run.stop == 'budget')
    # null pointer read: give the pointer a block
    if nullfail and run.nullref[0] in run.insns:
        # the faulting instruction's base register: the last load of it is the null pointer
        pc, a = run.nullref
        nn = getattr(run, 'nullref_n', None)
        x = run.insns[pc]
        bases = [cx.reg_name if False else x.reg_name(o.mem.base) for o in x.operands
                 if o.type == cx.X86_OP_MEM and o.mem.base not in (0, cx.X86_REG_ESP, cx.X86_REG_EBP)]
        if bases:
            for pc_, loc, sz, v, _q in reversed(list(run.reads)[:nn] if nn is not None else run.reads):
                y = run.insns.get(pc_)
                if y is None or y.mnemonic != 'mov' or not y.op_str.startswith(bases[0] + ','):
                    continue
                if v < 0x1000 and loc >= 0x10000 and not (STACK_TOP - 0x10000 <= loc < STACK_TOP + 0x2000):
                    lb = run.inp['lazy'].new()
                    if lb is not None:
                        out.append(('lazy', apply(run.inp, ('mem', loc), lb)))
                        mem_src[0] = loc
                break
    if nullfail and mem_src[0] is None:
        pc, a = run.nullref
        nn = getattr(run, 'nullref_n', None)
        for _, loc, sz, v, _q in reversed(list(run.reads)[:nn] if nn is not None else run.reads):   # (not later zero reads)
            if loc == callslot or (nn is not None and loc == a):
                continue                                # (the null access itself)
            if sz == 4 and v < 0x1000 and v <= a < v + 0x4000:
                if loc < 0x10000:
                    # read through the null itself: a chain (p = arg; q = p->x; q->vt): the
                    # first null of the chain is the one to give a block, else a register
                    rl = list(run.reads)[:nn] if nn is not None else list(run.reads)
                    a2 = loc
                    for _2, loc2, sz2, v2, _q2 in reversed(rl):
                        if sz2 == 4 and v2 < 0x1000 and v2 <= a2 < v2 + 0x4000 and loc2 != a2:
                            if loc2 < 0x10000:
                                a2 = loc2; continue
                            if run.argbase <= loc2 < run.argbase + 4 * len(run.inp['args']):
                                lb = run.inp['lazy'].new()
                                if lb is not None:
                                    out.append(('lazy', apply(run.inp, ('arg', (loc2 - run.argbase) // 4), lb)))
                                    chain[0] = True
                            elif not (STACK_TOP - 0x10000 <= loc2 < STACK_TOP + 0x2000):
                                lb = run.inp['lazy'].new()
                                if lb is not None:
                                    out.append(('lazy', apply(run.inp, ('mem', loc2), lb)))
                                    mem_src[0] = loc2
                            break
                    break
                lb = run.inp['lazy'].new()
                if lb is not None:
                    out.append(('lazy', apply(run.inp, ('mem', loc), lb)))
                    mem_src[0] = loc
                break
    if nullfail and mem_src[0] is None and not chain[0]:   # (a pointer loaded from memory is the null one, not a zero register)
        pc, a = run.nullref
        for r, v in run.inp['regs'].items():
            if v < 0x1000 and v <= a < v + 0x4000:
                lb = run.inp['lazy'].new()
                if lb is not None:
                    out.append(('lazy', apply(run.inp, ('reg', r), lb)))
        for k, v in enumerate(run.inp['args']):
            if v < 0x1000 and v <= a < v + 0x4000:
                lb = run.inp['lazy'].new()
                if lb is not None:
                    out.append(('lazy', apply(run.inp, ('arg', k), lb)))
    if nullfail and getattr(run, 'ring', None):
        pc, a = run.nullref
        for loc, v in reversed(run.ring):
            if loc == callslot:
                continue
            if v < 0x1000 and v <= a < v + 0x4000:
                lb = run.inp['lazy'].new()
                if lb is not None:
                    out.append(('lazy', apply(run.inp, ('mem', loc), lb)))
                break
    if run.stop == 'wild' and getattr(run, 'wildfrom', None):
        (kind, where), n = run.wildfrom
        slot = where - LAZY - LAZY_SZ * ((where - LAZY) // LAZY_SZ) if kind == 'mem' and LAZY <= where < LAZY + LAZY_N * LAZY_SZ else None
        if slot is not None and getattr(run, 'wild_disp', None) is not None:
            slot = run.wild_disp & 0xFFFFFFFF          # (a block pointer need not point at its start)
        # (the pop count is the call site's own, measured from the frame: a guess by slot
        # would put a function there that leaves the stack wrong)
        cands = []
        okp = getattr(run, 'wild_okpops', None) or {n}
        if slot is not None:                            # what the classes' own functions there pop, if the frame allows it
            for p_ in run.img.slot_pops_all.get(slot, []):
                if p_ in okp and p_ != n:
                    fa_ = run.img.stub_for(p_, slot)
                    if fa_ and fa_ not in cands:
                        cands.append(fa_)
        if n in run.img.stubs:
            cands.append(run.img.stubs[n][0])
        if slot is not None and (slot, n) in run.img.slot_pool:
            cands.append(run.img.slot_pool[(slot, n)])
        if not cands and run.img.by_pops.get(n):
            cands.append(run.img.by_pops[n])
        # the pushes counted at the call site can include saved registers (push ebx; push
        # edi; push arg; push arg): the counts the classes' own functions at the slot
        # pop are the other candidates, the run that comes back decides
        for fa in cands:
            if kind == 'mem':
                out.append(('lazy', apply(run.inp, ('mem', where), fa)))
            else:
                for loc, v in reversed(run.ring):
                    if v == where:
                        out.append(('lazy', apply(run.inp, ('mem', loc), fa))); break
                else:
                    for r, v in run.inp['regs'].items():
                        if v == where:
                            out.append(('lazy', apply(run.inp, ('reg', r), fa))); break
    elif run.stop == 'wild' and run.lastcall and run.lastcall[3] is not None and run.lastcall[3] < 0x11000:
        caddr, kind, where, v = run.lastcall
        n = call_pops(run, caddr)
        st = run.img.stubs.get(n)
        if st:
            if kind == 'mem':
                out.append(('lazy', apply(run.inp, ('mem', where), st[0])))
            else:
                loc = locate(run, v)
                if loc:
                    out.append(('lazy', apply(run.inp, loc, st[0])))

    if run.nullref:                                       # a broken pointer chain first: the slot address is junk
        out = [m for m in out if not (m[0] == 'lazy' and m[1]['pokes'].get(-1))]
    kf = lambda i_: (tuple(sorted(i_['pokes'].items())), tuple(sorted(i_['regs'].items())), tuple(i_['args']),
                     tuple(sorted(i_.get('rets', {}).items())), tuple(sorted(i_.get('srets', {}).items())))
    key0 = kf(run.inp)
    out = [m for m in out if kf(m[1]) != key0]

    if run.stubcalls:
        # a null pointer that came back from a stubbed call: that call returns a block
        # (first: a pointer a stubbed call returns is the likeliest null: the latest calls
        # that returned less than a page, each made to return a block (that call alone,
        # and every call from its site, for a loop) or 1 (a status the code tests first))
        wf = getattr(run, 'wildfrom', None)
        slot_in_lazy = run.stop == 'wild' and wf and wf[0][0] == 'mem' and LAZY <= wf[0][1] < LAZY + LAZY_N * LAZY_SZ
        # only when the failure itself is a null access (a call through a slot near 0, a
        # fault), not for any earlier read of a zero field
        nullish = (run.stop == 'wild' and wf and wf[0][0] == 'mem' and wf[0][1] < 0x1000) or \
            (run.stop.startswith('fault') and run.nullref) or run.stop == 'nullread'
        if nullish:                                     # (a virtual call through a null object too)
            fixes = []
            for sc in [x for x in reversed(run.stubcalls[-4:]) if run.inp.get('rets', {}).get(x[0], 0) < 0x1000]:
                same = [x[0] for x in run.stubcalls if x[1] == sc[1]]
                ra_ = sc[1] + run.insns[sc[1]].size
                if run.inp.get('srets', {}).get(ra_, 0) >= 0x1000 and sc[0] not in run.inp.get('rets', {}):
                    continue                            # the site already returns a block: not this call
                prev = next((run.inp['rets'][k] for k in same if run.inp.get('rets', {}).get(k, 0) >= 0x1000), None)
                lb = prev or run.inp['lazy'].new()
                if lb is None:
                    break
                c = clone(run.inp); c['rets'][sc[0]] = lb; fixes.append(('lazy', c))
                if len(same) > 1:                       # every call from the site, later iterations too
                    c = clone(run.inp)
                    for k in same:
                        c['rets'].pop(k, None)
                    c['srets'][sc[1] + run.insns[sc[1]].size] = lb
                    fixes.append(('lazy', c))
                c = clone(run.inp); c['rets'][sc[0]] = 1; fixes.append(('lazy', c))
            if slot_in_lazy or chain[0] or (mem_src[0] is not None and not (STACK_TOP - 0x10000 <= mem_src[0] < STACK_TOP + 0x2000)):
                out += fixes                            # a slot to fill, a pointer in memory to give a block: first
            else:
                out[:0] = fixes
        # branches on what a call returned: the other values it could return
        # (the tag names the call index too: a loop's later iterations are calls of their own)
        for k, caddr, tgt, sq in run.stubcalls[-6:]:
            for v in (1, 0xFFFFFFFF, 0x100, 0):
                if run.inp.get('rets', {}).get(k, 0) != v:
                    c = clone(run.inp); c['rets'][k] = v
                    out.append((('ret', caddr, k), c))
            lb = run.inp['lazy'].new()
            if lb is not None:
                c = clone(run.inp); c['rets'][k] = lb
                out.append((('ret', caddr, k), c))
                same = [x[0] for x in run.stubcalls if x[1] == caddr]
                if len(same) > 1:                       # every call from the site returns the block
                    c = clone(run.inp)
                    for k2 in same:
                        c['rets'].pop(k2, None)
                    c['srets'][caddr + run.insns[caddr].size] = lb
                    out.append((('ret', caddr, 'all'), c))

    def fl(v):
        f = struct.unpack('<f', struct.pack('<I', v & 0xFFFFFFFF))[0]
        return f if v and 1e-6 < abs(f) < 1e9 else None

    def fcands(f):
        return {struct.unpack('<I', struct.pack('<f', x))[0] for x in (0.0, -f, f * 2, f / 2, f + 1, f - 1, 1e6, -1e6, 1.0, -1.0)}

    prevseq = -1
    done = set()
    for addr, ops, jcc, seq in run.cmps:
        locs = []
        vals = [o for o in ops if o[2] is not None]
        other = [o[2] for o in vals]
        for kind, where, v in vals:
            if kind == 'reg' and _REG32.get(where) in run.inp['regs'] and \
                    _REG32[where] not in run.cmp_written.get(addr, frozenset(['?'])) and \
                    run.inp['regs'][_REG32[where]] & _REGMASK.get(where, 0xFFFFFFFF) == v:
                locs.append((('reg', _REG32[where]), v))   # an input register still holding its value
                continue
            loc = ('mem', where) if kind == 'mem' else locate(run, v, before=seq)
            if kind == 'reg':                            # a copy of an input register (mov ebx, eax) as well
                for r_in, rv in run.inp['regs'].items():
                    if rv == v and ('reg', r_in) != loc:
                        locs.append((('reg', r_in), v))
            if loc is not None:
                locs.append((loc, v))
            elif kind == 'reg':                          # a field read, then inc / dec (a count less one)
                for d in (1, -1):                        # (every recent read of that value: a zero is common)
                    got = []
                    for _pc, a_, sz_, rv, q_ in reversed(run.reads):
                        if q_ > seq or sz_ != 4 or rv != (v + d) & 0xFFFFFFFF or STACK_TOP - 0x2000 <= a_ < run.argbase:
                            continue
                        if ('mem', a_) not in got:
                            got.append(('mem', a_))
                        if len(got) >= 3:
                            break
                    for loc in got:
                        locs.append((loc, v, d))
        if not locs:                                     # x87 compare, flags of arithmetic: the reads just before
            for _pc, a, sz, v, q in reversed(run.reads):
                if q != seq:
                    continue
                if sz == 4 and not (STACK_TOP - 0x2000 <= a < run.argbase):
                    loc = ('arg', (a - run.argbase) // 4) if run.argbase <= a < run.argbase + 4 * len(run.inp['args']) else ('mem', a)
                    if loc not in [l for l, _ in locs]:
                        locs.append((loc, v))
                if len(locs) >= 4:
                    break
        for lv in locs:
            loc, v = lv[0], lv[1]
            d_ = lv[2] if len(lv) > 2 else 0
            key = (jcc, loc)
            if key in done:
                continue
            done.add(key)
            cands = {0, 1, 2, 4, 0xFFFFFFFF, v ^ 1, v + 1, v - 1, v ^ 0x80000000}   # (2, 4: a loop count, halved or not)
            for w in other:
                cands |= {w, w + 1, w - 1}
            f = fl(v)
            if f is not None:
                cands |= fcands(f)
            for c in cands:
                out.append(((addr, jcc), apply(run.inp, loc, (c + d_) & 0xFFFFFFFF)))
            if v == 0 and not d_:
                lb = run.inp['lazy'].new()
                if lb is not None:
                    out.append(((addr, jcc), apply(run.inp, loc, lb)))
    return out


_FSTARTS = None


def owner_fn(a):
    global _FSTARTS
    if _FSTARTS is None:
        _FSTARTS = sorted(int(f['start'], 16) for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json')))
    i = bisect.bisect_right(_FSTARTS, a) - 1
    return _FSTARTS[i] if i >= 0 else None


def transfer_of(run, uc, blk):
    """The indirect call / jmp ending block blk that went to a wild address:
    (location of the pointer ('mem', ea) or ('reg', value), bytes the target must pop)."""
    a, size = blk
    code = bytes(uc.mem_read(a, size + 16))
    ins = list(MD.disasm(code, a))
    body = [i for i in ins if i.address < a + size]
    if not body:
        return None
    last = body[-1]
    if last.mnemonic not in ('call', 'jmp') or not last.operands:
        return None
    op = last.operands[0]
    if op.type == cx.X86_OP_IMM:
        return None
    if op.type == cx.X86_OP_MEM:
        ea = _ea(uc, last, op)
        run.wild_disp = op.mem.disp if op.mem.base != cx.X86_REG_ESP else None   # the vtable slot
        if op.mem.base == cx.X86_REG_ESP and last.mnemonic == 'call':
            ea = (ea + 4) & 0xFFFFFFFF                 # esp already holds the return address
        loc = ('mem', ea)
    else:
        loc = ('regval', uc.reg_read(_ucreg(op.reg)))
    if last.mnemonic == 'jmp':                          # tail call: pops what the function it leaves pops
        f = owner_fn(last.address)
        pops = (ABI.get('0x%08X' % f, {}).get('pops') or 0) if f is not None else 0
    else:
        nxt = [i for i in ins if i.address >= a + size]
        if nxt and nxt[0].mnemonic == 'add' and nxt[0].op_str.startswith('esp,'):
            pops = 0
        elif getattr(run, 'wild_esp', None) is not None and hasattr(run, 'f_base'):
            pops = run.frame_pops(last.address, run.wild_esp)
            run.wild_okpops = run.pops_ok(last.address, run.wild_esp)
        else:
            pops = 4 * sum(1 for i in body[:-1] if i.mnemonic == 'push')
    return loc, pops


def call_pops(run, caddr):
    """Bytes the callee at caddr must pop: 0 when the caller cleans up (add esp
    after the call), else the pushes just before the call."""
    addrs = sorted(run.insns)
    k = addrs.index(caddr) if caddr in run.insns else -1
    if k < 0:
        return 0
    if k + 1 < len(addrs):
        nx = run.insns[addrs[k + 1]]
        if nx.mnemonic == 'add' and nx.op_str.startswith('esp,'):
            return 0
    n = 0
    for j in range(k - 1, -1, -1):
        i = run.insns[addrs[j]]
        if i.mnemonic == 'push':
            n += 4
        elif i.mnemonic in ('call', 'ret', 'jmp', 'pop') or i.mnemonic in JCC or 'esp' in i.op_str.split(',')[0]:
            break
    return n


def _inp_key(i_):
    return (tuple(sorted(i_['pokes'].items())), tuple(sorted(i_['regs'].items())), tuple(i_['args']),
            tuple(sorted(i_.get('rets', {}).items())), tuple(sorted(i_.get('srets', {}).items())))


def explore(img, va, runs=400, log=print):
    blocks = set(bblocks.blocks(va))
    import time as _time
    t_end = _time.time() + float(os.environ.get('RF_SECONDS', '150'))
    inp = base_input(img, va)
    first = Run(img, va, inp, budget=3_000_000).go()          # long setup walks (object searches) once
    corpus = [(inp, first)]
    covered = set(first.hit) if first.stop == 'ret' else set()   # only runs that come back count
    seen_branch = collections.Counter()
    reached = set(first.hit)                                  # blocks any run reached, failing or not
    # best first: the candidates of the run that got furthest (blocks reached, then calls
    # made) go first, repairs before other values for a return before branch flips
    import heapq
    heap, seq_ = [], [0]
    seen_inp = set()

    def push(muts, parent):
        pr = (-len(parent.hit), -len(parent.stubcalls))
        seq_[0] += 1
        batch = seq_[0]
        why = (parent.stop.split(' ')[0], parent.nullref and parent.nullref[0], len(parent.hit))
        for idx, (tag, c) in enumerate(muts):
            key = _inp_key(c)
            if key in seen_inp:
                continue
            if tag == 'lazy':
                c['why'] = why                          # the failure it repairs (a cap on repairs that go nowhere)
            seen_inp.add(key)
            rank = 0 if tag == 'lazy' else (1 if isinstance(tag, tuple) and tag[0] == 'ret' else 2)
            # (ties: the deepest repair chain, the newest run's, in the order its list gives:
            # depth first on a plateau, likeliest repair first)
            heapq.heappush(heap, (pr + (-c.get('depth', 0), rank, -batch, idx), tag, c))
        if len(heap) > 40000:                         # the worst half goes
            heap[:] = heapq.nsmallest(20000, heap)
            heapq.heapify(heap)
    seen_inp.add(_inp_key(inp))
    push(mutations(first), first)

    def dive(r, cand):
        """From a failing run, follow its repairs greedily (the best of the first few at each
        step): a chain of objects, slots and returns is many steps deep, each one a plateau
        a search across variants would spread out on. -> the returning run reached, or None."""
        nonlocal n
        for _step in range(int(os.environ.get('RF_DIVE', '80'))):
            if _time.time() >= t_end or n >= runs:
                return None
            fix = [m for m in mutations(r) if m[0] == 'lazy' or (isinstance(m[0], tuple) and m[0][0] == 'ret')]
            best = None
            for _t, c in fix[:6]:                       # the first repair, in the order the list gives
                key = _inp_key(c)
                if key in seen_inp:
                    continue
                seen_inp.add(key)
                rr = Run(img, va, c).go(); n += 1
                if rr.stop == 'ret' and not (rr.hit - covered):
                    continue                            # comes back, but by a way already known
                best = (None, rr, c)
                break
            if best is None:
                return None
            r, cand = best[1], best[2]
            if os.environ.get('RF_TRACE'):
                print('    dive %d: %s hit=%d calls=%d' % (_step, r.stop[:20], len(r.hit), len(r.stubcalls)))
            reached.update(r.hit)
            if r.stop == 'ret':
                return r, cand
        return None
    n = 1
    depth_cap = int(os.environ.get('RF_DEPTH', '64'))
    while heap and n < runs and covered != blocks and _time.time() < t_end:
        _pr, tag, cand = heapq.heappop(heap)
        if tag != 'lazy':
            seen_branch[tag] += 1
            if seen_branch[tag] > 24:
                continue
        elif cand.get('why'):
            seen_branch[('lazy',) + cand['why']] += 1
            if seen_branch[('lazy',) + cand['why']] > 48:
                continue                                # the same failure, repaired 48 ways already
        r = Run(img, va, cand).go(); n += 1
        if os.environ.get('RF_TRACE'):
            print('  run %d %s depth %d stop=%s hit=%d nullref=%s wildfrom=%s' % (n, tag if tag == 'lazy' else 'br', cand.get('depth', 0), r.stop,
                  len(r.hit), r.nullref and '%08X@%08X' % r.nullref, getattr(r, 'wildfrom', None)), r.stop, r.lastcall, sorted(r.hit), cand.get('rets'), cand.get('srets'), cand['regs'])
        d = cand.get('depth', 0) + 1
        if r.stop != 'ret' and d <= depth_cap:
            # a failing run: its repairs (lazy blocks; other values for what the last calls
            # returned: a divisor, a count, a status), and when it got further than any run
            # yet, its latest branches too
            lastk = {x[0] for x in r.stubcalls[-2:]}
            allm = mutations(r)
            fix = [m for m in allm if m[0] == 'lazy' or
                   (isinstance(m[0], tuple) and m[0][0] == 'ret' and len(m[0]) > 2 and m[0][2] in lastk)]
            more = []
            if r.hit - reached:
                more = [m for m in allm if not any(m is f for f in fix)][-64:]
                got = dive(r, cand)
                if got and got[0].hit - covered:
                    covered |= got[0].hit
                    corpus.append((got[1], got[0]))
                    ms = mutations(got[0])
                    for _t, c in ms:
                        c['depth'] = 0
                    push(ms, got[0])
            for _t, c in fix + more:
                c['depth'] = d
            push(fix + more, r)
        reached |= r.hit
        if r.hit - covered and r.stop == 'ret':          # a probe must come back: faults crash the game, loops hang it
            covered |= r.hit
            corpus.append((cand, r))
            ms = mutations(r)
            for _t, c in ms:
                c['depth'] = 0
            push(ms, r)
    # the smallest set of corpus inputs covering what was covered (greedy)
    keep, got = [], set()
    corpus = [(i_, r_) for i_, r_ in corpus if r_.stop == 'ret']
    covered = set().union(*[r_.hit for _, r_ in corpus]) if corpus else set()
    for inp_, r in sorted(corpus, key=lambda x: -len(x[1].hit)):
        if r.hit - got:
            keep.append((inp_, r)); got |= r.hit
    sys.stdout.flush()
    log('%08X %-44s %d/%d blocks in %d runs; %d inputs; stops: %s' % (
        va, DB.get(va, '')[:44], len(covered), len(blocks), n, len(keep),
        ','.join(sorted({r.stop.split(' ')[0] for _, r in keep}))))
    return keep, covered, blocks


# ---------------------------------------------------------------- probe lines
def fmt(v, made=None):
    if LAZY <= v < LAZY + LAZY_N * LAZY_SZ:
        return '@S+%X' % (v - LAZY)
    if made and made[2] <= v < made[3]:
        return '@N%X+%X' % (made[0], v - made[2])
    return '%X' % (v & 0xFFFFFFFF)


def probe_lines(va, inp, stub=False):
    """One fork's batch: the object's Create when the explorer made one, then the probe.
    stub: the probe skips the calls the function makes, returning the explorer's values."""
    made = inp.get('made')
    pre = ['va=%X ecx=0 edx=%X arg=0 arg=0 arg=0' % (made[0], made[1])] if made else []
    parts = ['va=%X' % va]
    f = lambda v: fmt(v, made)
    for a, v in sorted(inp['pokes'].items()):
        parts.append('poke=%s:%s' % (f(a), f(v)))
    for r, v in sorted(inp['regs'].items()):
        parts.append('%s=%s' % (r, f(v)))
    for v in inp['args']:
        parts.append('arg=%s' % f(v))
    if stub:
        parts.append('stub=1')
        for k, v in sorted(inp.get('rets', {}).items()):
            parts.append('ret=%d:%s' % (k, f(v)))
        for ra, v in sorted(inp.get('srets', {}).items()):
            parts.append('sret=%X:%s' % (ra, f(v)))
    return pre + [' '.join(parts)]


def probe_line(va, inp):
    parts = ['va=%X' % va]
    for a, v in sorted(inp['pokes'].items()):
        parts.append('poke=%s:%s' % (fmt(a), fmt(v)))
    for r, v in sorted(inp['regs'].items()):
        parts.append('%s=%s' % (r, fmt(v)))
    for v in inp['args']:
        parts.append('arg=%s' % fmt(v))
    return ' '.join(parts)


if __name__ == '__main__':
    a = sys.argv[1:]
    if len(a) < 3 or a[0] != 'explore':
        sys.exit(__doc__)
    runs = int(a[a.index('--runs') + 1]) if '--runs' in a else 400
    out = a[a.index('--out') + 1] if '--out' in a else None
    vas = [int(x, 16) for x in a[2:] if re.fullmatch(r'[0-9A-Fa-f]{5,8}', x)]
    img = Image(a[1])
    lines = []
    for va in vas:
        try:
            keep, cov, bl = explore(img, va, runs)
        except Exception as e:
            print('%08X error %s' % (va, e)); continue
        for inp, r in keep:
            lines.append(' | '.join(probe_lines(va, inp)))
        sys.stdout.flush()
    if out:
        open(out, 'w').write('\n'.join(lines) + '\n')
    else:
        print('\n'.join(lines))
