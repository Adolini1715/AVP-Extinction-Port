#!/usr/bin/env python3
"""Basic blocks of an original function, for path coverage.

    bblocks.py <va> [...]        print each function's block starts

A function spans [va, next abi.json start). Blocks are found by recursive
descent from the entry with capstone: every jcc/jmp/loop target inside the
range and every fall-through after a conditional branch starts a block;
jump tables (jmp [reg*4+table]) are followed while their entries point into
the range. Tail jumps out of the range and calls do not start blocks.

Used by verify_lib (which blocks a check's oracle replay executed,
verified_paths.json) and progress2.py (every-path vs partial).
"""
import bisect, json, os, struct, sys
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_info = _xbe = _starts = None
_cache = {}


def _load():
    global _info, _xbe, _starts
    if _info is None:
        _info = json.load(open(os.path.join(R, 'xbe_info.json')))
        _xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
        _starts = sorted(int(k, 16) for k in json.load(open(os.path.join(R, 'symbols', 'abi.json'))))


def read(va, n):
    _load()
    for s in _info['sections']:
        if s['va'] <= va < s['va'] + s['vsize']:
            o = s['raw'] + va - s['va']
            return _xbe[o:o + min(n, s['va'] + s['vsize'] - va)]
    return b''


def end(va):
    """Where the function ends: the next abi.json start."""
    _load()
    i = bisect.bisect_right(_starts, va)
    return _starts[i] if i < len(_starts) else va + 0x1000


_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
_md.detail = True
_JCC = {'jo', 'jno', 'jb', 'jae', 'je', 'jne', 'jbe', 'ja', 'js', 'jns', 'jp', 'jnp', 'jl', 'jge', 'jle', 'jg',
        'jcxz', 'jecxz', 'loop', 'loope', 'loopne'}


def blocks(va):
    """-> sorted list of block start addresses of the function at va."""
    if va in _cache:
        return _cache[va]
    hi = end(va)
    code = read(va, hi - va)
    starts = {va}
    seen = set()
    work = [va]
    while work:
        pc = work.pop()
        while va <= pc < hi and pc not in seen:
            seen.add(pc)
            ins = next(_md.disasm(code[pc - va:pc - va + 16], pc), None)
            if ins is None:
                break
            m = ins.mnemonic
            nxt = pc + ins.size
            if m in _JCC or m == 'jmp':
                op = ins.operands[0]
                if op.type == capstone.x86.X86_OP_IMM:
                    t = op.imm & 0xFFFFFFFF
                    if va <= t < hi:
                        starts.add(t); work.append(t)
                elif op.type == capstone.x86.X86_OP_MEM and op.mem.base == 0 and op.mem.scale == 4 and op.mem.disp:
                    tab = op.mem.disp & 0xFFFFFFFF                 # jump table
                    for k in range(512):
                        w = read(tab + 4 * k, 4)
                        if len(w) < 4:
                            break
                        t = struct.unpack('<I', w)[0]
                        if not va <= t < hi:
                            break
                        starts.add(t); work.append(t)
                if m == 'jmp':
                    break
                starts.add(nxt)
            elif m in ('ret', 'retf', 'int3', 'hlt', 'ud2', 'iret', 'iretd'):
                break
            pc = nxt
    r = sorted(s for s in starts if s in seen)
    _cache[va] = r
    return r


def hit(va, executed):
    """Block starts of va among the executed instruction addresses."""
    ex = set(executed)
    return [b for b in blocks(va) if b in ex]


if __name__ == '__main__':
    for a in sys.argv[1:]:
        v = int(a, 16)
        b = blocks(v)
        print('%08X..%08X %d blocks: %s' % (v, end(v), len(b), ' '.join('%X' % x for x in b)))
