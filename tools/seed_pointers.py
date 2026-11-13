#!/usr/bin/env python3
"""Function starts the disassembler missed because nothing calls them
directly: callbacks passed as constants (input handlers registered with
`push 0x637F0`, thread routines, sort comparators, state-table entries) and
pointers stored in data tables.

A candidate is any 32-bit constant, in an instruction operand or an aligned
data word, that points into a code section at a 16-byte boundary where the
previous bytes are padding (int3/nop) or a return, as MSVC lays functions out,
and is not already a known function start. Each candidate must also decode as
at least a few valid instructions.

Writes recomp/seeds_pointers.json for recomp/relift.sh, merging runtime-observed
unresolved indirect-call targets from logs given on the command line.
    seed_pointers.py [run.log ...]
"""
import json, os, re, struct, sys
import capstone

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
funcs = json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
known = {int(f['start'], 16) for f in funcs}
spans = sorted((int(f['start'], 16), int(f['start'], 16) + f['size']) for f in funcs)
code_secs = [s for s in info['sections'] if s['name'] in ('.text', 'D3D', 'XGRPH', 'DSOUND', 'WMADEC', 'XMV', 'XPP', 'DOLBY', 'D3DX')]
data_secs = [s for s in info['sections'] if s['name'] in ('.rdata', '.data')]


def sec_of(va, secs):
    for s in secs:
        if s['va'] <= va < s['va'] + s['rsize']:
            return s
    return None


def byte_at(va):
    s = sec_of(va, info['sections'])
    return xbe[s['raw'] + va - s['va']] if s else None


md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)


import bisect
starts = [a for a, _ in spans]


def inside_known(t):
    # a target strictly inside a known function is a mid-function address
    # (an early return followed by aligned code looks like a function start:
    # GEnviroSwarm::Process at 0x100AF0 was split this way and crashed)
    i = bisect.bisect_right(starts, t) - 1
    return i >= 0 and spans[i][0] < t < spans[i][1]


def plausible(t):
    if t in known or t & 0xF or not sec_of(t, code_secs) or inside_known(t):
        return False
    prev = byte_at(t - 1)
    if prev not in (0xCC, 0x90, 0xC3) and not (byte_at(t - 3) == 0xC2):
        return False
    s = sec_of(t, code_secs)
    o = s['raw'] + t - s['va']
    ins = list(md.disasm(xbe[o:o + 32], t))
    return len(ins) >= 3 and ins[0].mnemonic not in ('int3', 'nop', 'add') and not ins[0].mnemonic.startswith('(')


found = {}
md_d = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md_d.detail = True
# constants only from instructions inside known functions: a linear sweep of
# a whole section misdecodes data and padding into fake immediates
for a, b in spans:
    s = sec_of(a, code_secs)
    if not s:
        continue
    for i in md_d.disasm(xbe[s['raw'] + a - s['va']:s['raw'] + b - s['va']], a):
        for op in i.operands:
            if op.type == capstone.x86.X86_OP_IMM:
                t = op.imm & 0xFFFFFFFF
                if i.mnemonic not in ('call', 'jmp') and not i.mnemonic.startswith('j') and plausible(t):
                    found.setdefault(t, 'constant in %s at 0x%08X' % (i.mnemonic, i.address))
for s in data_secs:
    seg = xbe[s['raw']:s['raw'] + s['rsize']]
    for o in range(0, len(seg) - 3, 4):
        t = struct.unpack_from('<I', seg, o)[0]
        if plausible(t):
            found.setdefault(t, 'pointer in data at 0x%08X' % (s['va'] + o))
observed = set()
for log in sys.argv[1:]:
    for m in re.finditer(r'Failed to resolve VA 0x([0-9A-Fa-f]{8})', open(log, errors='ignore').read()):
        observed.add(int(m.group(1), 16))
out = [{'start': '0x%08X' % t, 'note': n} for t, n in sorted(found.items())]
for t in sorted(observed - set(found) - known):
    out.append({'start': '0x%08X' % t, 'observed': True, 'note': 'unresolved indirect-call target observed at runtime'})
# cumulative: functions.json from the last relift already contains earlier
# seeds as known functions, so they are carried over from the previous file
path = os.path.join(R, 'recomp', 'seeds_pointers.json')
prev = json.load(open(path)) if os.path.exists(path) else []
have = {int(e['start'], 16) for e in out}
out = sorted(out + [e for e in prev if int(e['start'], 16) not in have], key=lambda e: int(e['start'], 16))
json.dump(out, open(path, 'w'), indent=1)
print('%d candidate function starts from constants/data, %d observed at runtime (%d new)' % (
    len(found), len(observed), len(observed - set(found) - known)))
print('includes 0x637F0 (pause handler):', 0x637F0 in found)
