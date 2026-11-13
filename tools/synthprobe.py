#!/usr/bin/env python3
"""synthprobe.py VA [VA ...] [--out FILE] [--per-block N]

Probes for functions no run calls, on synthetic objects (runs under
/home/claude/angrenv/bin/python).

For each function: symbolic execution of the original (angr, from
decompiler/avpe.elf) from its entry, with every input register the ABI lists
pointing at, or holding, symbolic data: ecx (or the first register input) is
the address of a 4 KB symbolic object, stack arguments are symbolic words.
Calls are not followed (their results are unconstrained). For every basic
block of the function (bblocks.py), a path that reaches it is searched; the
object bytes and arguments that path constrains are solved, and written as a
probe spec for the bridge's AVPE_PROBE_MULTI:

    va=XXXX ecx=@S poke=@S+0:<vtable> poke=@S+OFF:VAL ... arg=...

(@S is the bridge's 4 KB zeroed scratch block; a symbolic pointer field that a
path dereferences is pointed at a second region inside @S, @S+800.) Blocks
whose path depends on a callee's result are reported as such: their probe may
not take that path in the game, so the verify run decides.

The oracle comparison stays the arbiter: original and decompiled run on the
same synthetic state, so a pass is a real equivalence check on that state.
"""
import json, logging, os, sys
logging.disable(logging.WARNING)
T = os.path.dirname(os.path.abspath(__file__))
sys.path = [p for p in sys.path if os.path.abspath(p or '.') != T]   # tools/queue.py shadows the stdlib angr needs
import angr
from angr.rustylib import claripy   # angr 10 ships its own claripy
sys.path.append(T)
import bblocks

R = os.path.join(T, '..')
abi = {int(k, 16): v for k, v in json.load(open(os.path.join(R, 'symbols', 'abi.json'))).items()}
classes = json.load(open(os.path.join(R, 'symbols', 'classes.json')))
OBJ = 0x0F000000          # the symbolic object in angr's address space
SUB = OBJ + 0x800         # where symbolic pointers inside it point
ARGS = 6

proj = angr.Project('/home/claude/decompiler/avpe.elf', auto_load_libs=False)

# Calls: small leaf callees (getters, flag tests: <= LEAF bytes and calling
# nothing) are executed, so branches on their results trace back to object
# fields; every other callee returns an unconstrained value (following every
# call explodes: two functions took over 28 minutes).
LEAF = int(os.environ.get('SYNTH_LEAF', '96'))
_fj = json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
_hooked = 0
for _x in _fj:
    _a = int(_x['start'], 16)
    if _x['size'] > LEAF or _x['calls_to']:
        proj.hook(_a, angr.SIM_PROCEDURES['stubs']['ReturnUnconstrained'](), replace=True)
        _hooked += 1
del _fj


def vtable_for(va):
    """the vtable of a class this function is a method of (by slot), or None"""
    for cname, c in classes.items():
        for vt in c.get('vtables', []):
            if '0x%x' % va in [s.lower() for s in vt['slots']]:
                return int(vt['va'], 16), cname
    return None, None


def probes(va, per_block=1):
    a = abi.get(va, {})
    regs = a.get('regs') or ['ecx']
    opts = {angr.options.SYMBOL_FILL_UNCONSTRAINED_MEMORY,
            angr.options.SYMBOL_FILL_UNCONSTRAINED_REGISTERS}
    was_hooked = proj.is_hooked(va)
    if was_hooked:
        proj.unhook(va)                   # the function itself runs
    st = proj.factory.blank_state(addr=va, add_options=opts | {
angr.options.SYMBOL_FILL_UNCONSTRAINED_REGISTERS})
    # one symbolic variable per field word, so a path's constraints name the fields it reads
    words = [claripy.BVS('f%03X' % (4 * i), 32) for i in range(0x800 // 4)]
    for i, w in enumerate(words):
        st.memory.store(OBJ + 4 * i, w, endness='Iend_LE')
    vt, cname = vtable_for(va)
    if vt:
        st.memory.store(OBJ, claripy.BVV(vt, 32), endness='Iend_LE')
    this = regs[0] if regs else 'ecx'
    setattr(st.regs, this, OBJ)
    esp = 0x0E000000
    st.regs.esp = esp
    st.memory.store(esp, claripy.BVV(0xDEC0DE00, 32), endness='Iend_LE')
    args = [claripy.BVS('arg%d' % i, 32) for i in range(ARGS)]
    for i, x in enumerate(args):
        st.memory.store(esp + 4 + 4 * i, x, endness='Iend_LE')
    blocks = sorted(bblocks.blocks(va))
    out, reached, callee_dep = [], set(), set()
    for b in blocks:
        if b in reached:
            continue
        sm = proj.factory.simulation_manager(st)
        sm.explore(find=b, avoid=[0xDEC0DE00], n=int(os.environ.get('SYNTH_STEPS', '400')),
                   num_find=1)
        sm.move('active', 'deadended', filter_func=lambda x: len(x.history.bbl_addrs) > 3000)
        if not sm.found:
            continue
        s = sm.found[0]
        reached.add(b)
        pokes = []
        cons = s.solver.constraints
        varnames = set()
        for c in cons:
            varnames |= c.variables
        if any(n.startswith(('fake_ret', 'unconstrained_ret', 'mem_')) for n in varnames):
            callee_dep.add(b)
        # fields this path constrains, at the values that take it (the scratch block starts zeroed)
        for i, w in enumerate(words[1:], 1):
            if w.args[0] not in varnames:
                continue
            val = s.solver.eval(w)
            if val:
                pokes.append('poke=@S+%X:%X' % (4 * i, val))
        argv = ['arg=%X' % s.solver.eval(x) for x, _ in zip(args, range(a.get('pops', 0) // 4))]
        spec = 'va=%X %s=@S %s %s %s' % (va, this, ('poke=@S+0:%X' % vt) if vt else '', ' '.join(pokes), ' '.join(argv))
        spec = ' '.join(spec.split())
        if spec not in out:
            out.append(spec)
        # blocks on the same path count as reached
        for h in s.history.bbl_addrs:
            if h in blocks:
                reached.add(h)
    if was_hooked:
        proj.hook(va, angr.SIM_PROCEDURES['stubs']['ReturnUnconstrained'](), replace=True)
    return out, len(blocks), reached, callee_dep, cname


if __name__ == '__main__':
    av = sys.argv[1:]
    outf = av[av.index('--out') + 1] if '--out' in av else None
    lines = []
    for x in [v for v in av if not v.startswith('--') and v != outf]:
        va = int(x, 16)
        try:
            ps, nb, rch, dep, cname = probes(va)
        except Exception as e:
            print('%08X error: %s' % (va, str(e)[:120])); continue
        print('%08X %-24s blocks %d, reached %d (%d depend on a callee result), %d probes' % (
            va, cname or '?', nb, len(rch), len(dep), len(ps)))
        lines += ps
    if outf:
        open(outf, 'w').write('\n'.join(lines) + '\n')
    else:
        print('\n'.join(lines))
