#!/home/claude/angrenv/bin/python
"""Structured C draft of a game function, from angr's decompiler.

    tools/draft.py VA [VA ...] [--fields]

A reading aid for hand-writing a function, not code to paste: the C names the
callees (Class_Method from the symbol DB), uses their conventions from
symbols/abi.json (register arguments, stack bytes popped, eax returned or
not) and reads .data / .bss as memory, never as constants. What it cannot
know and the hand-code must still get right: exact stack temporaries (the
original's frame), x87 stack effects, and register outputs the callers read
(fninfo.py shows the ABI line; tools/eaxlive.py what callers use).

The image is /home/claude/decompiler/avpe.elf (the XBE's sections at their
addresses; rebuild with /home/claude/decompiler/mkelf.py). Runs under the
angr venv (/home/claude/angrenv).
"""
import csv, json, logging, os, re, sys
logging.disable(logging.CRITICAL)
import angr
from angr.calling_conventions import SimCCUsercall, SimRegArg, SimStackArg
from angr.knowledge_plugins.functions.function import PrototypeSource
from angr.sim_type import SimTypeFunction, SimTypeInt, SimTypeBottom

R = '/home/claude/avpe'
ELF = '/home/claude/decompiler/avpe.elf'
abi = json.load(open(R + '/symbols/abi.json'))
db = {int(r['xbox'], 16): r for r in csv.DictReader(open(R + '/symbols/xbox_symbols.csv'))}
classes = json.load(open(R + '/symbols/classes.json'))
ed = json.load(open(R + '/symbols/editables.json'))
FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}


def short(va):
    r = db.get(va)
    n = r['name'] if r and r['tier'] in ('A', 'B', 'C') else ''
    if not n:
        return 'sub_%08X' % va
    m = re.match(r'(__\w\w|\w+?)__(\d+)(\w+)', n)
    if m:
        k = int(m.group(2)); cls = m.group(3)[:k]
        meth = {'__ct': 'ctor', '__dt': 'dtor'}.get(m.group(1), m.group(1).lstrip('_'))
        return '%s_%s' % (cls, meth)
    m = re.match(r'(\w+?)__F', n)
    return m.group(1) if m else re.sub(r'\W', '_', n)


def apply_abi(p, f):
    a = abi.get('0x%08X' % f.addr)
    if not a or a.get('funclet'):
        return
    args = [SimRegArg(r, 4) for r in a['regs']] + [SimStackArg(4 + 4 * i, 4) for i in range(a['pops'] // 4)]
    if not args and a['pops'] == 0:
        return                                  # cdecl: let angr count the pushed arguments
    f.calling_convention = SimCCUsercall(p.arch, args, SimRegArg('eax', 4))
    ret = SimTypeInt().with_arch(p.arch) if a['ret_eax'] else SimTypeBottom(label='void').with_arch(p.arch)
    f.set_prototype(None, SimTypeFunction([SimTypeInt().with_arch(p.arch) for _ in args], ret).with_arch(p.arch),
                    source=max(PrototypeSource))


def fields_of(va):
    r = db.get(va)
    m = re.match(r'^\w+?__(\d+)(\w+)$', r['name']) if r else None
    c = m.group(2)[:int(m.group(1))] if m else None
    out = {}
    while c:
        for f in ed.get(c, {}).get('fields', []):
            out.setdefault(f['offset'], '%s::%s' % (c, f['name']))
        c = classes.get(c, {}).get('parent')
    return out


def tidy(text, va):
    # offsets in hex; a legend of the named fields of `this` the code touches
    text = re.sub(r'\+ (\d{2,})\)', lambda m: '+ 0x%X)' % int(m.group(1)), text)
    flds = fields_of(va)
    used = sorted({int(x, 16) for x in re.findall(r'\+ 0x([0-9A-F]+)\)', text)} & set(flds))
    if used:
        text = '/* fields: %s */\n' % ', '.join('+0x%X %s' % (o, flds[o]) for o in used) + text
    return text


def draft(p, va):
    size = FUNCS.get(va, {}).get('size', 0x400)
    cfg = p.analyses.CFGFast(function_starts=[va], start_at_entry=False, normalize=True,
                             regions=[(va, va + size)], force_complete_scan=False)
    kb = cfg.kb
    for f in list(kb.functions.values()):
        if f.addr != va:
            f.name = short(f.addr)
        apply_abi(p, f)
    f = kb.functions[va]
    f.name = short(va)
    d = p.analyses.Decompiler(f, cfg=cfg.model)
    a = abi.get('0x%08X' % va, {})
    head = '/* %08X %s  [in: %s; pops %d%s] */' % (va, (db.get(va) or {}).get('name', ''), ','.join(a.get('regs', [])) or '-',
                                                 a.get('pops', 0), '; ret eax' if a.get('ret_eax') else '')
    return head + '\n' + tidy(d.codegen.text, va) if d.codegen else head + '\n(no output)'


if __name__ == '__main__':
    vas = [int(x, 16) for x in sys.argv[1:] if not x.startswith('--')]
    if not vas:
        print(__doc__); sys.exit(2)
    p = angr.Project(ELF, auto_load_libs=False)
    for v in vas:
        try:
            print(draft(p, v))
        except Exception as e:
            print('/* %08X: decompiler failed: %s */' % (v, e))
        print()
