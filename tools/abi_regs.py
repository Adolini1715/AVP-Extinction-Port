#!/usr/bin/env python3
"""True calling conventions for every Xbox function: register inputs (live-in
at entry), stack bytes the callee pops, and whether a caller ever uses the
return value in eax.

The build used link-time code generation, so non-exported functions take
arguments in whatever registers the compiler chose (CShell::ShellLoadLevel:
this in ecx, the level name in eax; the texture release at 0x138070: the
object in esi). Decompiled C++ has to be glued to the lifted code through
exactly those inputs, so they are computed rather than assumed:

  * per function: basic blocks from a sweep of [start, end), register
    read/write sets from capstone; backward liveness to a fixpoint;
  * a call reads its callee's live-in set (iterated across the call graph to
    a fixpoint) and clobbers eax, ecx, edx; an indirect call reads ecx;
  * the prologue's saves of callee-saved registers (leading push ebx/esi/edi/
    ebp) are not reads;
  * return used: eax is live after some call to the function.

Output: symbols/abi.json  {addr: {regs:[...], pops:N, ret_eax:bool}}
"""
import json, os, struct, collections
import capstone
from capstone import x86

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
FUNCS = '/home/claude/xboxrecomp/tools/disasm/output/functions.json'
funcs = json.load(open(FUNCS))

md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = True
GPR = {'eax', 'ecx', 'edx', 'ebx', 'esi', 'edi', 'ebp'}
SUB = {'al': 'eax', 'ah': 'eax', 'ax': 'eax', 'cl': 'ecx', 'ch': 'ecx', 'cx': 'ecx',
       'dl': 'edx', 'dh': 'edx', 'dx': 'edx', 'bl': 'ebx', 'bh': 'ebx', 'bx': 'ebx',
       'si': 'esi', 'di': 'edi', 'bp': 'ebp'}
SAVED = {'ebx', 'esi', 'edi', 'ebp'}
CLOBBER = {'eax', 'ecx', 'edx'}
KTHUNK = None   # set below from the XBE header


def code(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['vsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]
    return b''


def canon(r):
    r = SUB.get(r, r)
    return r if r in GPR else None


starts = {int(f['start'], 16) for f in funcs}
KTHUNK = info['hdr']['kernel_thunk']
info_f = {}
for f in funcs:
    a, e = int(f['start'], 16), int(f['end'], 16)
    if e <= a or e - a > 0x20000:
        continue
    ins = list(md.disasm(code(a, e - a), a))
    if not ins:
        continue
    # Saves of callee-saved registers: a push of ebx/esi/edi/ebp that the
    # function also pops back is a save, wherever it sits (MSVC often saves
    # after the first instructions, or only on the path that needs it). An
    # argument push of the same register has no matching pop.
    popped = {i.op_str for i in ins if i.mnemonic == 'pop' and i.op_str in SAVED}
    # __SEH_prolog pushes ebx/esi/edi for its caller; __SEH_epilog pops them.
    seh = any('fs:[0]' in i.op_str for i in ins)
    save_addrs = {i.address for i in ins if i.mnemonic == 'push' and i.op_str in SAVED
                  and (i.op_str in popped or seh)}
    # A __finally / __except block borrows its parent's frame: it reads ebp
    # without ever setting it. Its register reads are the parent's state, not
    # inputs a caller supplies.
    sets_ebp = any(i.mnemonic in ('mov', 'lea', 'pop') and i.op_str.startswith('ebp,') or
                   (i.mnemonic == 'pop' and i.op_str == 'ebp') for i in ins)
    funclet = (not sets_ebp) and any('[ebp' in i.op_str for i in ins)
    # push ecx / push eax as one of the first instructions: MSVC reserving a
    # 4-byte local, not a read. (A real ecx input is read elsewhere too.)
    for i in ins[:3]:
        if i.mnemonic == 'push' and i.op_str in ('ecx', 'eax', 'edx'):
            save_addrs.add(i.address)
    # blocks
    leaders = {a}
    for k, i in enumerate(ins):
        g = i.groups
        if x86.X86_GRP_JUMP in g or x86.X86_GRP_RET in g:
            if k + 1 < len(ins):
                leaders.add(ins[k + 1].address)
            if x86.X86_GRP_JUMP in g and i.operands and i.operands[0].type == x86.X86_OP_IMM:
                t = i.operands[0].imm
                if a <= t < e:
                    leaders.add(t)
    blocks = collections.OrderedDict()
    cur = None
    for i in ins:
        if i.address in leaders:
            cur = i.address; blocks[cur] = []
        if cur is not None:
            blocks[cur].append(i)
    order = list(blocks)
    succ = {}
    for k, b in enumerate(order):
        last = blocks[b][-1]
        g = last.groups
        s = []
        if x86.X86_GRP_RET in g:
            pass
        elif x86.X86_GRP_JUMP in g:
            if last.operands and last.operands[0].type == x86.X86_OP_IMM:
                t = last.operands[0].imm
                if a <= t < e:
                    s.append(t)
                elif t in starts:
                    s.append(('tail', t))
            if last.mnemonic != 'jmp' and k + 1 < len(order):
                s.append(order[k + 1])
        elif k + 1 < len(order):
            s.append(order[k + 1])
        succ[b] = s
    pops = 0
    for i in ins:
        if i.mnemonic == 'ret' and i.operands:
            pops = max(pops, i.operands[0].imm)
    info_f[a] = dict(blocks=blocks, order=order, succ=succ, saves=save_addrs, pops=pops, funclet=funclet)


def insn_effect(i, live_in_of, save_addrs):
    """(uses, defs) for one instruction."""
    if i.address in save_addrs:
        return set(), set()
    if x86.X86_GRP_CALL in i.groups:
        uses = set()
        if i.operands and i.operands[0].type == x86.X86_OP_IMM:
            t = i.operands[0].imm
            if not (t in info_f and info_f[t]['funclet']):
                uses |= live_in_of.get(t, set())
        elif i.operands and i.operands[0].type == x86.X86_OP_MEM and i.operands[0].mem.base == 0 \
                and KTHUNK <= i.operands[0].mem.disp < KTHUNK + 4 * 512:
            pass                                   # kernel import: stdcall, stack only
        else:
            uses |= {'ecx'}
            try:
                rr, _ = i.regs_access()
                uses |= {canon(i.reg_name(r)) for r in rr} - {None}
            except Exception:
                pass
        return uses, set(CLOBBER)
    try:
        rr, ww = i.regs_access()
    except Exception:
        return set(), set()
    uses = {canon(i.reg_name(r)) for r in rr} - {None}
    defs = {canon(i.reg_name(r)) for r in ww} - {None}
    m = i.mnemonic
    # Alignment padding MSVC emits as multi-byte no-ops: lea r, [r] / mov r, r
    if (m == 'lea' and len(i.operands) == 2 and i.operands[1].type == x86.X86_OP_MEM
            and i.operands[1].mem.index == 0 and i.operands[1].mem.disp == 0
            and i.operands[1].mem.base == i.operands[0].reg) or \
       (m == 'mov' and len(i.operands) == 2 and i.operands[0].type == x86.X86_OP_REG
            and i.operands[1].type == x86.X86_OP_REG and i.operands[0].reg == i.operands[1].reg):
        return set(), set()
    # xor r, r / sub r, r: definitions, not reads
    if m in ('xor', 'sub') and len(i.operands) == 2 and i.operands[0].type == x86.X86_OP_REG \
            and i.operands[1].type == x86.X86_OP_REG and i.operands[0].reg == i.operands[1].reg:
        uses -= {canon(i.reg_name(i.operands[0].reg))}
    # 8/16-bit writes leave the rest of the register: partial write reads it.
    # Treat them as defs only (conservative for inputs would be noisier).
    return uses, defs


def live_in(a, live_in_of):
    f = info_f[a]
    bin_ = {b: set() for b in f['order']}
    changed = True
    while changed:
        changed = False
        for b in reversed(f['order']):
            out = set()
            for s in f['succ'][b]:
                if isinstance(s, tuple):
                    out |= live_in_of.get(s[1], set())
                else:
                    out |= bin_.get(s, set())
            live = out
            for i in reversed(f['blocks'][b]):
                u, d = insn_effect(i, live_in_of, f['saves'])
                live = (live - d) | u
            if live != bin_[b]:
                bin_[b] = live; changed = True
    return bin_[f['order'][0]] - {'ebp'} if f['order'] else set()


live_in_of = {a: set() for a in info_f}
own = {a: live_in(a, {}) for a in info_f}      # without callee contributions
for rnd in range(12):
    changed = 0
    for a in info_f:
        li = live_in(a, live_in_of)
        if li != live_in_of[a]:
            live_in_of[a] = li; changed += 1
    print('round', rnd, 'changed', changed)
    if not changed:
        break

# return used: after a direct call, is eax read before being written?
ret_used = collections.Counter()
for a, f in info_f.items():
    for b in f['order']:
        blk = f['blocks'][b]
        for k, i in enumerate(blk):
            if x86.X86_GRP_CALL in i.groups and i.operands and i.operands[0].type == x86.X86_OP_IMM:
                t = i.operands[0].imm
                for j in blk[k + 1:]:
                    u, d = insn_effect(j, live_in_of, set())
                    if 'eax' in u:
                        ret_used[t] += 1; break
                    if 'eax' in d:
                        break

out = {}
for a in info_f:
    out['0x%08X' % a] = {'regs': sorted(live_in_of[a]), 'own': sorted(own[a]), 'pops': info_f[a]['pops'],
                         'funclet': info_f[a]['funclet'],
                         'ret_eax': ret_used[a] > 0}
json.dump(out, open(os.path.join(R, 'symbols', 'abi.json'), 'w'), indent=0)
c = collections.Counter(tuple(v['regs']) for v in out.values())
print(len(out), 'functions; register-input patterns:', c.most_common(8))
