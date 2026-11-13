#!/usr/bin/env python3
"""Remove false function starts from the disassembler's function list and
extend the function that falls through into each, so the lifter translates
the code as one function.

A false start splits a function where flags are live: the lifted half that
begins at the false start no longer sees the flags the instruction before it
set, and takes its first conditional branch on a fallback value. Found so far
by a coincidental constant in DirectSound's data (0x000E1007 inside
CSoundDefinition's constructor; the lifter detects it as an immediate
reference target, strong evidence, so --coalesce-functions refuses it).

    false_starts.py [recomp/false_starts.json]   (edits functions.json in place;
                                                  keeps functions.json.prefalse)
The list: [{"start": "0x...", "owner": "0x...", "why": "..."}].
The owner must end at the start, or end before it with only padding (nop or
int3 bytes) in between and in the false start's own range: a false start
inside the padding after a function (the owner then takes the padding).
"""
import json, os, shutil, sys
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
# AVPE_FUNCS_JSON, AVPE_XBE, AVPE_XBE_INFO: the installer's paths (defaults: this workspace)
FUNCS = os.environ.get('AVPE_FUNCS_JSON', '/home/claude/xboxrecomp/tools/disasm/output/functions.json')
lst = json.load(open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(R, 'recomp', 'false_starts.json')))
funcs = json.load(open(FUNCS))
by = {int(f['start'], 16): f for f in funcs}
XBE = None
def padding(a, b):                                  # bytes [a, b) all nop/int3
    global XBE
    if XBE is None:
        info = json.load(open(os.environ.get('AVPE_XBE_INFO', os.path.join(R, 'xbe_info.json'))))
        XBE = (info['sections'], open(os.environ.get('AVPE_XBE', os.path.join(R, 'disc', 'default.xbe')), 'rb').read())
    sec = [x for x in XBE[0] if x['va'] <= a and b <= x['va'] + x['vsize']]
    if not sec:
        return False
    o = sec[0]['raw'] + a - sec[0]['va']
    return all(c in (0x90, 0xCC) for c in XBE[1][o:o + b - a])
changed = 0
for e in lst:
    s, o = int(e['start'], 16), int(e['owner'], 16)
    if s not in by:
        continue                                    # already removed
    oe = int(by[o]['end'], 16) if o in by else -1
    if not (oe == s or 0 <= oe < s and padding(oe, int(by[s]['end'], 16))):
        sys.exit('owner 0x%08X does not end at 0x%08X' % (o, s))
    victim = by.pop(s)
    by[o]['num_instructions'] = by[o].get('num_instructions', 0) + s - oe   # nops
    by[o]['end'] = victim['end']
    by[o]['size'] = int(victim['end'], 16) - o
    by[o]['num_instructions'] = by[o].get('num_instructions', 0) + victim.get('num_instructions', 0)
    changed += 1
    print('merged 0x%08X into 0x%08X (now ends %s): %s' % (s, o, victim['end'], e.get('why', '')))
if changed:
    if not os.path.exists(FUNCS + '.prefalse'):
        shutil.copy(FUNCS, FUNCS + '.prefalse')
    json.dump([by[a] for a in sorted(by)], open(FUNCS, 'w'), indent=1)
print('%d false starts removed' % changed)
