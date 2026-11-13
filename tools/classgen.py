#!/usr/bin/env python3
"""Generate the class boilerplate every game class has, as decompiled C++.

Every GObject class has the same three small functions, written by Zono with
a macro or by copy and paste, compiled with the class's constants:
  Create                static factory: operator new, construct,
                        PostConstruction unless the class table is being built
  IsDerivedFromClassId  is `id` this class or one of the ancestors whose test
                        the compiler inlined? else the tail call up the chain
  GetParentClassId      the parent's class id
This tool reads each one's machine code, checks it is exactly one of the
known shapes (by interpreting it, not by name), and writes C++ for it into
the class's source file, between AVPE-CLASSGEN markers (regenerated each
run; everything else in the file is left alone). Functions that do not fit a
shape, or that a file already decompiles by hand, are skipped and listed.

    classgen.py [--report] [--exclude A.cpp,B.cpp]
      --report   shapes only, writes nothing
      --exclude  files to leave untouched (someone is editing them); their
                 functions are not generated

Functions that are the same boilerplate with something extra compiled in
(a Create with its constructor inlined, a constructor that is GObject's
inlined constructor plus fields, a static initialiser that copies data) are
written by tools/symgen.py, which runs the machine code symbolically along
every path and writes what it does as C++ statements. classgen_skip.txt
lists functions not to generate (they failed verification), with the reason.

Then: rebuild (recomp/rebuild.sh) and verify (tools/verify_multi.py: Create
by its natural call at boot, the others with probes from --probes files this
tool writes to /home/claude/avpe-recomp/verify/classgen_*.probes).
"""
import collections, csv, json, os, re, sys
import capstone
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import editgen
import symgen

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
D = '/home/claude/avpe-decomp'
OUT = '/home/claude/avpe-recomp'
info = json.load(open(os.path.join(R, 'xbe_info.json')))
xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
abi = json.load(open(os.path.join(R, 'symbols', 'abi.json')))
db = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'xbox_symbols.csv')))}
smap = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv')))}
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
md.detail = False
OP_NEW, BUILD = 0xD6CB0, 0x26B410
EXCLUDE = set()
if '--exclude' in sys.argv:
    EXCLUDE = set(sys.argv[sys.argv.index('--exclude') + 1].split(','))
SKIPF = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'classgen_skip.txt')
PENDF = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'classgen_pending.txt')
gen_pending = set()
if os.path.exists(PENDF):
    gen_pending = {int(l.split()[0], 16) for l in open(PENDF) if l.strip() and not l.startswith('#')}
SKIP = {int(l.split()[0], 16) for l in open(SKIPF) if l.strip() and not l.startswith('#')} if os.path.exists(SKIPF) else set()

# Functions symgen.py writes as straight statements because they are twins of
# a shape every class has (notes/twins.md), with what they are.
METHODS = {
    # GTask subclass constructors: GObject's constructor inlined, the GTask
    # fields cleared, the vtable (twins.md row 8)
    0x119E0: 'GTask', 0x11AB0: 'GTask', 0x11BD0: 'GTask', 0x11C20: 'GTask', 0x12F90: 'GTask', 0x13150: 'GTask',
    0x13740: 'GTask', 0x138D0: 'GTask', 0x13E40: 'GTask', 0x14540: 'GTask', 0x14590: 'GTask', 0x14780: 'GTask',
    0x149B0: 'GTask', 0x14A80: 'GTask', 0x14C20: 'GTask', 0x14E40: 'GTask', 0x14E90: 'GTask', 0x15020: 'GTask',
    0x15770: 'GTask', 0x18340: 'GTask', 0x18670: 'GTask', 0x18C70: 'GTask',
    # straight-line twins (one path: a natural call runs all of it), by
    # their notes/twins.md row
    0x11270: 'S4', 0x11350: 'S4', 0x113E0: 'S4', 0x11950: '85', 0x13FF0: 'S36', 0x14920: '85',
    0x18390: 'S19', 0x185E0: '85', 0x19400: 'S1', 0x19570: 'S1', 0x20CA0: '108', 0x20CD0: '86',
    0x20D00: 'S15', 0x218F0: '138', 0x22080: 'S4', 0x22090: 'S6', 0x22280: 'S5', 0x240F0: 'S5',
    0x26750: '138', 0x271E0: 'S4', 0x27EB0: 'S1', 0x2B1A0: 'S47', 0x4B940: 'S4', 0x4EFE0: '72',
    0x4FEF0: 'S5', 0x50F90: 'S1', 0x515E0: 'S1', 0x51C10: 'S1', 0x54620: 'S20', 0x54F10: 'S20',
    0x56920: 'S1', 0x59C10: 'S5', 0x5ADE0: '108', 0x5AE10: '86', 0x5AE40: 'S15', 0x60470: 'S5',
    0x60CC0: 'S19', 0x61C90: 'S10', 0x633E0: 'S33', 0x68650: 'S31', 0x6AE80: 'S5', 0x6D6B0: 'S9',
    0x6E480: 'S1', 0x6E6B0: 'S1', 0x6EFE0: 'S6', 0x6F4F0: 'S1', 0x70CC0: 'S8', 0x70D00: 'S8',
    0x71C10: 'S35', 0x72070: 'S9', 0x72780: 'S1', 0x72D50: 'S33', 0x7D4E0: 'S4', 0x7F0A0: 'S25',
    0x82410: 'S19', 0x84820: 'S4', 0x85CC0: 'S8', 0x85D00: 'S8', 0x86450: '108', 0x86480: '86',
    0x864B0: 'S15', 0x86BB0: 'S6', 0x86CE0: 'S8', 0x86D20: 'S8', 0x86F60: 'S39', 0x87040: 'S13',
    0x87C50: 'S6', 0x89710: 'S4', 0x89CD0: 'S1', 0x8AA70: 'S9', 0x8CAE0: 'S4', 0x8CAF0: '72',
    0x8CB20: '72', 0x8CC40: 'S1', 0x8CED0: 'S5', 0x8DB10: 'S6', 0x8F8E0: 'S10', 0x8FCC0: '86',
    0x91BC0: 'S1', 0x928F0: 'S5', 0x94C70: 'S10', 0x969C0: 'S10', 0x980D0: 'S1', 0x99350: 'S35',
    0x9C240: 'S1', 0xA46F0: 'S5', 0xA62E0: 'S22', 0xA6570: 'S22', 0xA7F60: 'S22', 0xAA070: 'S24',
    0xAB5F0: 'S24', 0xAD3E0: 'S5', 0xAEA90: 'S1', 0xB1520: 'S24', 0xB15E0: 'S54', 0xC1070: 'S5',
    0xC3110: 'S5', 0xC40F0: 'S1', 0xC8140: 'S10', 0xCF9E0: 'S6', 0xD1A10: 'S42', 0xD85E0: 'S1',
    0xDAE30: 'S25', 0xDB8F0: 'S25', 0xE1050: 'S13', 0xEBEB0: 'S20', 0xEE290: 'S1', 0xEEFB0: 'S1',
    0xF1010: 'S1', 0xF11D0: 'S1', 0xF1300: 'S1', 0xF7CE0: 'S1', 0xFB710: 'S9', 0xFF050: 'S13',
    0xFF810: 'S13', 0x100540: 'S1', 0x102750: 'S1', 0x105A60: 'S1', 0x106520: 'S9', 0x108080: 'S1',
    0x1099A0: 'S1', 0x10C020: 'S1', 0x112480: 'S1', 0x1140E0: 'S1', 0x11B190: 'S31', 0x11B760: 'S1',
    0x131F90: 'S1', 0x136940: 'S4', 0x138AC0: 'S4', 0x138AD0: 'S46', 0x13E800: 'S1', 0x13F2A0: 'S4',
    0x140B80: 'S36',
}
METHOD_WHAT = {
    'GTask': 'a GTask subclass constructor: GObject\'s constructor inlined, the task fields cleared, then the class\'s vtable',
}
for _k in set(METHODS.values()) - set(METHOD_WHAT):
    METHOD_WHAT[_k] = 'the same code as its twins in notes/twins.md row %s' % _k

# The long tail (tools/symgen_tail.txt): small functions of every kind that
# symgen writes out exactly (accessors, setters, flag tests, straight-line
# methods), each verified by a natural call or by probes. One VA per line.
TAILF = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'symgen_tail.txt')
TAIL = [int(l.split()[0], 16) for l in open(TAILF) if l.strip() and not l.startswith('#')] if os.path.exists(TAILF) else []


def code(va, n):
    for s in info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return xbe[o:o + n]
    return b''


def insns(va):
    f = FUNCS.get(va)
    return list(md.disasm(code(va, f['size']), va)) if f else []


def imm(op):
    m = re.search(r'0x([0-9a-f]+)', op)
    return int(m.group(1), 16) if m else None


def cls_of(name):
    m = re.match(r'^\w+?__(\d+)(\w+)$', name)
    return m.group(2)[:int(m.group(1))] if m else None


# ---------------------------------------------------------------- shapes
def parse_isderived(va):
    """-> (ids, tail) if the function only compares eax=[esp+4] with class ids
    (true on a match) and otherwise tail-jumps with the id; checked by
    interpreting it for every id and for a value matching none."""
    ins = insns(va)
    if not ins or ins[0].mnemonic != 'mov' or ins[0].op_str != 'eax, dword ptr [esp + 4]':
        return None
    ok = {'cmp': r'eax, dword ptr \[0x[0-9a-f]+\]', 'je': r'0x[0-9a-f]+', 'jne': r'0x[0-9a-f]+',
          'mov': r'(al, 1|dword ptr \[esp \+ 4\], eax|eax, dword ptr \[esp \+ 4\])', 'ret': r'4', 'jmp': r'0x[0-9a-f]+',
          'call': r'0x[0-9a-f]+'}
    for i in ins:
        if i.mnemonic not in ok or not re.fullmatch(ok[i.mnemonic], i.op_str):
            return None
    ids = [imm(i.op_str) for i in ins if i.mnemonic == 'cmp']
    tails = [imm(i.op_str) for i in ins if i.mnemonic in ('jmp', 'call')]
    conv = {i.mnemonic for i in ins if i.mnemonic in ('jmp', 'call')}
    if len(set(tails)) != 1 or not ids or len(conv) != 1:
        return None
    if conv == {'call'}:
        # call PARENT; ret 4: an LTCG clone of the parent's test, id in eax
        k = next(k for k, i in enumerate(ins) if i.mnemonic == 'call')
        if k + 1 >= len(ins) or ins[k + 1].mnemonic != 'ret' or ins[k + 1].op_str != '4':
            return None
        if abi.get('0x%08X' % tails[0], {}).get('regs') != ['eax'] or abi.get('0x%08X' % tails[0], {}).get('pops') != 0:
            return None
    at = {i.address: k for k, i in enumerate(ins)}

    def run(value_of):
        k, eax, flag = 0, None, None
        for _ in range(200):
            i = ins[k]
            if i.mnemonic == 'mov' and i.op_str == 'eax, dword ptr [esp + 4]':
                eax = 'arg'
            elif i.mnemonic == 'cmp':
                flag = value_of(imm(i.op_str))
            elif i.mnemonic in ('je', 'jne'):
                if (i.mnemonic == 'je') == flag:
                    k = at.get(imm(i.op_str)); continue
            elif i.mnemonic == 'mov' and i.op_str == 'al, 1':
                return 'true' if ins[k + 1].mnemonic == 'ret' else None
            elif i.mnemonic in ('jmp', 'call'):
                return 'tail'
            k += 1
            if k is None or k >= len(ins):
                return None
        return None
    for g in ids:
        if run(lambda c, g=g: c == g) != 'true':
            return None
    if run(lambda c: False) != 'tail':
        return None
    return ids, tails[0], conv == {'call'}


def parse_parentid(va):
    ins = insns(va)
    if len(ins) == 2 and ins[0].mnemonic == 'mov' and re.fullmatch(r'eax, dword ptr \[0x[0-9a-f]+\]', ins[0].op_str) \
            and ins[1].mnemonic == 'ret' and not ins[1].op_str:
        return imm(ins[0].op_str)
    return None


CREATE_HEAD = ['mov eax, ecx', 'mov ecx, dword ptr [esp + 0x10]', 'push ecx', 'mov ecx, dword ptr [esp + 0x10]',
               'push ecx', 'push edx', 'mov edx, dword ptr [esp + 0x14]', 'push edx']


GOBJ_INLINE = ['fld dword ptr [ecx + 0x2c]', 'mov dword ptr [ecx], 0x%x', 'fcomp dword ptr [0x251fc4]', 'fnstsw ax',
               'test ah, 0x41', 'jne', 'mov dword ptr [ecx + 0x2c], edx', 'cmp dword ptr [ecx + 0x28], edx', 'je',
               'mov eax, dword ptr [0x2070c8]', 'mov dword ptr [ecx + 0x2c], eax', 'mov dword ptr [ecx + 0x28], edx']


def parse_create(va):
    """Create = push R; operator new(size); construct; PostConstruction unless
    the class table is being built; return R. Construct shapes:
      ctor      the constructor is a function: memory in eax and one of ecx,
                esi, edi (or pushed), returns this
      base+vt   the class's own constructor only sets the vtable (and maybe one
                byte), after its base's constructor (a function)
      inline    no constructor function: GObject's constructor inlined, then
                the vtable
    -> dict(size, kind, ctor, regs, vt, byte, saved)"""
    ins = upto_ret(insns(va))
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in ins]
    if len(t) < 20 or not re.fullmatch(r'push (esi|edi|ebx)', t[0]) or t[1:9] != CREATE_HEAD:
        return None
    R = t[0].split()[1]
    m = re.fullmatch(r'mov ecx, (0x[0-9a-f]+)', t[9])
    if not m or t[10] != 'call 0x%x' % OP_NEW:
        return None
    size = int(m.group(1), 16)
    # the common tail: PostConstruction (vtable slot 1) unless building the class table
    k = max((j for j, x in enumerate(t) if '0x%x' % BUILD in x), default=None)
    if k is None:
        return None
    tail = [re.sub(r'0x[0-9a-f]+$', '', x) if x.startswith('j') else x for x in t[k:]]
    zero_edx = False
    if tail[:3] == ['mov eax, dword ptr [0x%x]' % BUILD, 'test eax, eax', 'jne ']:
        rest = tail[3:]
    elif tail[:2] == ['cmp dword ptr [0x%x], edx' % BUILD, 'jne ']:
        rest, zero_edx = tail[2:], True
    else:
        return None
    if len(rest) != 6 or not re.fullmatch(r'mov (eax|edx), dword ptr \[%s\]' % R, rest[0]):
        return None
    X = rest[0].split()[1].rstrip(',')
    if rest[1:] != ['mov ecx, %s' % R, 'call dword ptr [%s + 4]' % X, 'mov eax, %s' % R, 'pop %s' % R, 'ret 0xc']:
        return None
    mid = t[11:k]
    if len(mid) < 2 or mid[-1] != 'xor %s, %s' % (R, R) or not mid[-2].startswith('jmp '):
        return None
    mid = mid[:-2]
    # ctor
    if len(mid) >= 4 and mid[0] == 'test eax, eax' and mid[1].startswith('je ') and not zero_edx:
        r = mid[2:]
        regs, stack = ['eax'], False
        if r[0] in ('mov ecx, eax', 'mov esi, eax'):
            regs.append(r[0].split()[1].rstrip(',')); r = r[1:]
        elif r[:2] == ['push edi', 'mov edi, eax'] and r[-1] == 'pop edi':
            regs.append('edi'); r = r[2:-1]
        elif r[0] == 'push eax':
            stack = True; r = r[1:]
        if len(r) == 2 and re.fullmatch(r'call 0x[0-9a-f]+', r[0]) and r[1] == 'mov %s, eax' % R:
            ctor = imm(r[0])
            if stack and _pops(ctor) != 4:
                return None
            return dict(size=size, kind='ctor', ctor=ctor, regs=regs, stack=stack, saved=R)
        return None
    # base+vt
    mm = re.fullmatch(r'mov (e[a-z]{2}), eax', mid[0]) if mid else None
    if mm and not zero_edx:
        Q = mm.group(1)
        r = mid[1:]
        if len(r) >= 4 and r[0] == 'test %s, %s' % (Q, Q) and r[1].startswith('je ') and re.fullmatch(r'call 0x[0-9a-f]+', r[2]):
            base = imm(r[2]); r = r[3:]
            byte = None
            mb = re.fullmatch(r'mov byte ptr \[%s \+ (0x[0-9a-f]+)\], (\d+|0x[0-9a-f]+)' % Q, r[0])
            if mb:
                byte = (int(mb.group(1), 16), int(mb.group(2), 0)); r = r[1:]
            mv = re.fullmatch(r'mov dword ptr \[%s\], (0x[0-9a-f]+)' % Q, r[0]) if r else None
            if mv and (r[1:] == [] if Q == R else r[1:] == ['mov %s, %s' % (R, Q)]):
                return dict(size=size, kind='base+vt', ctor=base, regs=[Q] if Q != 'eax' else ['eax'],
                            vt=int(mv.group(1), 16), byte=byte, saved=R)
        return None
    # inline GObject constructor + vtable
    if zero_edx and mid[:4] == ['mov ecx, eax', 'xor edx, edx', 'cmp ecx, edx', mid[3]] and mid[3].startswith('je '):
        g = [re.sub(r' 0x[0-9a-f]+$', '', x) if x.startswith('j') else x for x in mid[4:16]]
        if g == [x % VT_GOBJECT if '%' in x else x for x in GOBJ_INLINE] and len(mid) == 18:
            mv = re.fullmatch(r'mov dword ptr \[ecx\], (0x[0-9a-f]+)', mid[16])
            if mv and mid[17] == 'mov %s, ecx' % R:
                return dict(size=size, kind='inline', vt=int(mv.group(1), 16), saved=R)
    return None


VT_GOBJECT = 0x250D54


def _pops(va):
    return abi.get('0x%08X' % va, {}).get('pops')


def cstr(va):
    b = code(va, 256)
    return b[:b.index(b'\0')].decode('latin1') if b and b'\0' in b else None


ADD_CLASS_ID = 0xD7180


def upto_ret(ins):
    for k, i in enumerate(ins):
        if i.mnemonic == 'ret':
            return ins[:k + 1]
    return ins


def parse_register(va):
    """g = GObject::AddClassId(name, Create, flags, &out): the registration
    every class has (push x4, call, store, ret). -> dict(name, create, flags, out, g)"""
    ins = upto_ret(insns(va))
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in ins]
    if len(t) != 7 or not all(re.fullmatch(r'push 0x[0-9a-f]+', x) for x in t[:4]) or t[4] != 'call 0x%x' % ADD_CLASS_ID \
            or not re.fullmatch(r'mov dword ptr \[0x[0-9a-f]+\], eax', t[5]) or t[6] != 'ret ':
        return None
    out, flags, create, name = (imm(x) for x in t[:4])
    if cstr(name) is None:
        return None
    return dict(name=name, text=cstr(name), create=create, flags=flags, out=out, g=imm(t[5]))


def parse_crc(va):
    """g = CRC of a string constant (the engine's CRC-32, no final xor), the
    loop inlined with the first character as an immediate. -> dict(s, g)"""
    ins = upto_ret(insns(va))
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in ins]
    if len(t) != 16:
        return None
    pat = ['or eax, 0xffffffff', 'xor ecx, ecx', r'mov edx, 0x[0-9a-f]+', r'mov cl, 0x[0-9a-f]+', r'lea esp, \[esp\]',
           'xor ecx, eax', 'and ecx, 0xff', r'mov ecx, dword ptr \[ecx\*4 \+ 0x26afa0\]', 'shr eax, 8', 'xor eax, ecx',
           r'mov cl, byte ptr \[edx \+ 1\]', 'inc edx', 'test cl, cl', r'jne 0x[0-9a-f]+', r'mov dword ptr \[0x[0-9a-f]+\], eax', 'ret ']
    if not all(re.fullmatch(p_, x) for p_, x in zip(pat, t)):
        return None
    sva, first = imm(t[2]), imm(t[3])
    txt = cstr(sva)
    if not txt or ord(txt[0]) != first or imm(t[13]) != ins[5].address:
        return None
    return dict(s=sva, text=txt, g=imm(t[14]))


MSG_ADD, HEAP_ALLOC, VBAIL = 0xD7B20, 0xDC4D0, 0x11A640


def num(op):
    """the last operand as a number (capstone prints 0..9 in decimal)"""
    return int(op.rsplit(', ', 1)[-1].split()[-1], 0)


def parse_msgreg(va):
    """MessageTypeEntry::Add(id) -> entry {Create, size, name}; the id kept
    in a static. -> dict(id, create, size, name, text, g)"""
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in upto_ret(insns(va))]
    pat = [r'mov eax, (?:0x[0-9a-f]+|\d+)', 'call 0x%x' % MSG_ADD, r'mov dword ptr \[eax\], (?:0x[0-9a-f]+|\d+)',
           r'mov dword ptr \[eax \+ 4\], (?:0x[0-9a-f]+|\d+)', r'mov dword ptr \[eax \+ 8\], (?:0x[0-9a-f]+|\d+)',
           r'mov dword ptr \[(?:0x[0-9a-f]+|\d+)\], (?:0x[0-9a-f]+|\d+)', 'ret ']
    if len(t) != len(pat) or not all(re.fullmatch(p_, x) for p_, x in zip(pat, t)):
        return None
    i = num(t[0]); g, i2 = (int(x, 0) for x in re.findall(r'(0x[0-9a-f]+|\b\d+\b)', t[5].split('ptr ')[1]))
    if i2 != i or cstr(num(t[4])) is None:
        return None
    return dict(id=i, create=num(t[2]), size=num(t[3]), name=num(t[4]), text=cstr(num(t[4])), g=g)


def parse_msgcreate(va):
    """CXxxMessage::Create: allocate from the game heap (fatal message when
    out of memory, returns 0), set the type id, flags byte, next = 0.
    -> dict(size, id, flags)"""
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in insns(va)]
    pat = ['push 0x10', r'push (?:0x[0-9a-f]+|\d+)', 'push 0x2cc2e4', 'call 0x%x' % HEAP_ALLOC, 'test eax, eax',
           r'jne (?:0x[0-9a-f]+|\d+)', 'push 0x215bb0', 'call 0x%x' % VBAIL, 'add esp, 4', 'xor eax, eax', 'ret ',
           r'mov dword ptr \[eax\], (?:0x[0-9a-f]+|\d+)', r'mov byte ptr \[eax \+ 8\], (?:0x[0-9a-f]+|\d+)',
           r'mov dword ptr \[eax \+ 4\], 0', 'ret ']
    if len(t) < len(pat) or not all(re.fullmatch(p_, x) for p_, x in zip(pat, t)):
        return None
    if imm(t[5]) != insns(va)[11].address:
        return None
    return dict(size=num(t[1]), id=num(t[11]), flags=num(t[12]))


NAME_ENTRY, ATEXIT = 0x1368C0, 0x1476B9


def parse_named(va):
    """the name table (0x1368C0: esi name, ebx out, value on the stack):
    out = the entry for name; a non-zero value is stored in it.
    -> dict(value, name, text, out)"""
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in upto_ret(insns(va))]
    pat = ['push ebx', 'push esi', r'push (?:0x[0-9a-f]+|\d+)', r'mov esi, (?:0x[0-9a-f]+|\d+)',
           r'mov ebx, (?:0x[0-9a-f]+|\d+)', 'call 0x%x' % NAME_ENTRY, 'pop esi', 'pop ebx', 'ret ']
    if len(t) != len(pat) or not all(re.fullmatch(p_, x) for p_, x in zip(pat, t)):
        return None
    if cstr(num(t[3])) is None:
        return None
    return dict(value=num(t[2]), name=num(t[3]), text=cstr(num(t[3])), out=num(t[4]))


def parse_atexit(va):
    """atexit(fn): a static object's destructor. -> fn"""
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in upto_ret(insns(va))]
    if len(t) == 4 and re.fullmatch(r'push 0x[0-9a-f]+', t[0]) and t[1] == 'call 0x%x' % ATEXIT and t[2] == 'pop ecx' and t[3] == 'ret ':
        return num(t[0])
    return None


def parse_return_global(va):
    """mov eax, [g]; ret -> g"""
    t = ['%s %s' % (i.mnemonic, i.op_str) for i in upto_ret(insns(va))]
    if len(t) == 2 and re.fullmatch(r'mov eax, dword ptr \[(?:0x[0-9a-f]+|\d+)\]', t[0]) and t[1] == 'ret ':
        return imm(t[0])
    return None


def c_str(txt):
    return '"' + txt.replace('\\', '\\\\').replace('"', '\\"') + '"'


# ---------------------------------------------------------------- collect
by_cls = collections.defaultdict(dict)
for va, r in db.items():
    if r['tier'] not in ('A', 'B', 'C'):
        continue
    n = r['name']
    for kind in ('Create', 'IsDerivedFromClassId', 'GetParentClassId', 'SpecifyEditable'):
        if n.startswith(kind + '__'):
            c = cls_of(n)
            if c:
                by_cls[c][kind] = va

# functions the bridge wraps (tracing, HLE) keep their wrapper
hand = {int(m, 16) for m in re.findall(r'^void sub_([0-9A-Fa-f]{8})\(void\)', open(os.path.join(R, 'recomp', 'avpe_bridge.c')).read(), re.M)}
for f in os.listdir(os.path.join(D, 'src')):
    if f.endswith('.cpp'):
        s = open(os.path.join(D, 'src', f)).read()
        s = re.sub(r'/\* AVPE-CLASSGEN BEGIN.*?/\* AVPE-CLASSGEN TAIL \*/', '', s, flags=re.S)
        hand |= {int(m, 16) for m in re.findall(r'^void (?:sub|pending)_([0-9A-Fa-f]{8})\(void\)', s, re.M)}

gen = collections.defaultdict(list)      # file -> [(va, kind, class, params)]
rejects = {}
skipped = collections.Counter()
stats = collections.Counter()
for c, fs in sorted(by_cls.items()):
    for kind, va in fs.items():
        if va in hand:
            skipped['decompiled by hand'] += 1; continue
        if smap.get(va, {}).get('how') in ('runtime', 'alias', None):
            skipped['not game code'] += 1; continue
        p = {'Create': parse_create, 'IsDerivedFromClassId': parse_isderived, 'GetParentClassId': parse_parentid,
             'SpecifyEditable': lambda v: editgen.analyse(v)[0]}[kind](va)
        if kind == 'Create' and p is not None and p['kind'] != 'inline':
            need = set(abi.get('0x%08X' % p['ctor'], {}).get('regs', []))
            have = set(p['regs'])
            if p['kind'] == 'base+vt':
                have |= {'eax'}                      # eax still holds the memory at the call
            if not need <= have:
                p = None                             # symgen passes what the constructor reads
            else:
                p['ctor_regs'] = sorted(have)
        if kind == 'Create' and p is None and va not in SKIP:
            try:
                p = dict(symgen.create(va), kind='code')
            except symgen.Reject as e:
                rejects[va] = str(e)
        if p is None:
            skipped['%s: other shape' % kind] += 1; continue
        stats['%s%s' % (kind, (' (' + p['kind'] + ')') if kind == 'Create' else '')] += 1
        gen[smap[va]['file']].append((va, kind, c, p))

msg_factories = {}
# static initialisers (srcmap: how sinit*)
for va, r in sorted(smap.items()):
    if not r['how'].startswith('sinit') or va in hand:
        continue
    p = parse_register(va)
    if p:
        stats['static init: AddClassId'] += 1
        gen[r['file']].append((va, 'sinit_register', cls_of(db.get(p['create'], {}).get('name', '')) or '', p)); continue
    p = parse_crc(va)
    if p:
        stats['static init: CRC constant'] += 1
        gen[r['file']].append((va, 'sinit_crc', '', p)); continue
    p = parse_msgreg(va)
    if p:
        stats['static init: message type'] += 1
        gen[r['file']].append((va, 'sinit_message', p['text'], p))
        # the message's factory: in the same file (the PS2 build puts each
        # CXxxMessage::Create beside the TU that registers it)
        f = parse_msgcreate(p['create']) if p['create'] not in hand else None
        if f and f['id'] == p['id']:
            stats['message Create'] += 1
            gen[r['file']].append((p['create'], 'message_create', p['text'], f))
            msg_factories[p['create']] = (r['file'], p['text'])
        else:
            skipped['message Create: other shape'] += 1
        continue
    p = parse_named(va)
    if p:
        stats['static init: name table'] += 1
        gen[r['file']].append((va, 'sinit_named', '', p)); continue
    p = parse_atexit(va)
    if p is not None:
        stats['static init: atexit'] += 1
        gen[r['file']].append((va, 'sinit_atexit', '', p)); continue
    if va not in SKIP:
        try:
            p = symgen.plain(va, 'sinit', False)
            stats['static init: code (symgen)'] += 1
            gen[r['file']].append((va, 'sinit_code', '', p)); continue
        except symgen.Reject as e:
            rejects[va] = str(e)
    skipped['static init: other shape'] += 1

# twins of a shape (METHODS): written by symgen
for va, what in sorted(METHODS.items()):
    r = smap.get(va)
    if va in hand or va in SKIP or not r:
        continue
    ab = abi.get('0x%08X' % va, {})
    if set(ab.get('regs', [])) - {'ecx'}:
        rejects[va] = 'register inputs %s' % ab.get('regs'); continue
    try:
        p = symgen.plain(va, 'method', bool(ab.get('ret_eax')))
    except symgen.Reject as e:
        rejects[va] = str(e); continue
    stats['%s (symgen)' % ('GTask constructor' if what == 'GTask' else 'twins')] += 1
    gen[r['file']].append((va, 'method_code', what, p))

# the long tail: written by symgen
done = {va for items in gen.values() for va, *_ in items}
for va in TAIL:
    r = smap.get(va)
    if va in hand or va in SKIP or va in METHODS or va in done or not r:
        continue
    ab = abi.get('0x%08X' % va, {})
    try:
        p = symgen.plain(va, 'method' if 'ecx' in ab.get('regs', []) else 'free', bool(ab.get('ret_eax')), exact=True)
    except symgen.Reject as e:
        rejects[va] = str(e); continue
    stats['long tail (symgen)'] += 1
    gen[r['file']].append((va, 'tail_code', '', p))

# GetParentClassId compiled without a name (vtable folding left it unnamed):
# "return <class id static>" where the static is one a class registration
# writes
class_of_id = {}
for items in gen.values():
    for va, kind, c, p in items:
        if kind == 'sinit_register':
            class_of_id[p['g']] = c or p['text']
done = {va for items in gen.values() for va, *_ in items}
for va, r in sorted(smap.items()):
    if va in hand or va in done or r['how'] in ('runtime', 'alias', None) or r['how'].startswith('sinit'):
        continue
    g = parse_return_global(va)
    if g in class_of_id:
        stats['GetParentClassId (unnamed)'] += 1
        gen[r['file']].append((va, 'GetParentClassId', '', g))

print('generated:', dict(stats), '= %d functions in %d files' % (sum(stats.values()), len(gen)))
print('skipped:', dict(skipped))
excluded = [va for f, items in gen.items() if f.replace('/', '_') in EXCLUDE for va, kind, c, p in items
            if kind in ('sinit_code', 'method_code', 'tail_code') or (kind == 'Create' and p['kind'] == 'code')]
if excluded:
    print('not generated (excluded files):', ' '.join('%X' % v for v in sorted(excluded)))
if '-v' in sys.argv:
    for va, why in sorted(rejects.items()):
        print('symgen rejects %08X %s: %s' % (va, smap.get(va, {}).get('file'), why))
if '--report' in sys.argv:
    sys.exit(0)


# ---------------------------------------------------------------- emit
def code_entry(va, lines, ret, pops, pre=()):
    body = ['    ' + x for x in pre] + list(lines)
    body.append('    ret(%s, %d);' % (ret, pops) if ret else '    ret_void(%d);' % pops)
    return ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n' % (va, va) + '\n'.join(body) +
            '\n    avpe_verify_exit(0x%08X);\n}\n' % va)


def tail_entry(va, p):
    """a long-tail function: its inputs (this, input registers, stack
    arguments), the statements, the result as the original leaves it"""
    n = symgen.fname(va)
    h = '/* 0x%08X  %s(generated: tools/classgen.py, written out by tools/symgen.py from its machine code) */\n' % (
        va, (n + ' ') if n else '')
    pre = (['uint32_t sp = g_esp;                          /* esp at entry: the stack slots the original uses as memory */']
           if p.get('uses_sp') else []) + (['uint32_t self = reg(ECX);'] if p['uses_self'] else []) + \
        ['uint32_t in_%s = reg(%s);' % (r, r.upper()) for r in p['inregs']] + \
        ['uint32_t a%d = arg(%d);' % (i, i) for i in p['args']] + ['float fa%d = argf(%d);' % (i, i) for i in p['fargs']]
    body = ['    ' + x for x in pre] + list(p['lines'])
    if p['st0'] is not None:
        if p['ret']:
            body.append('    g_eax = %s;' % p['ret'])
        body.append('    ret_st0(%s, %d);' % (p['st0'], p['pops']))
    elif p['ret'] and p['ret_kind'] == 'al':
        body.append('    ret_al(%s, %d);' % (p['ret'], p['pops']))
    elif p['ret']:
        body.append('    ret(%s, %d);' % (p['ret'], p['pops']))
    else:
        body.append('    ret_void(%d);' % p['pops'])
    return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n' % (va, va) + '\n'.join(body) +
                '\n    avpe_verify_exit(0x%08X);\n}\n' % va)


def emit(va, kind, c, p):
    if kind == 'tail_code':
        return tail_entry(va, p)
    if kind == 'sinit_code':
        h = '/* 0x%08X  static initialiser (generated: tools/classgen.py; its straight-line code written out by tools/symgen.py) */\n' % va
        return h + code_entry(va, p['lines'], p['ret'], p['pops'], ['uint32_t sp = g_esp;'] if p.get('uses_sp') else [])
    if kind == 'method_code':
        n = symgen.fname(va)
        h = '/* 0x%08X  %s%s (generated: tools/classgen.py, written out by tools/symgen.py) */\n' % (
            va, (n + ': ') if n else '', METHOD_WHAT[c])
        pre = (['uint32_t sp = g_esp;'] if p.get('uses_sp') else []) + \
            (['uint32_t self = reg(ECX);'] if p['uses_self'] else []) + ['uint32_t a%d = arg(%d);' % (i, i) for i in p['args']]
        return h + code_entry(va, p['lines'], p['ret'], p['pops'], pre)
    if kind == 'Create' and p['kind'] == 'code':
        h = '/* 0x%08X  %s::Create (generated: tools/classgen.py; the constructor as compiled into it, written out by tools/symgen.py) */\n' % (va, c)
        lam = '\n'.join('    ' + x for x in p['lines'])
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    uint32_t r = classgen::Create(reg(ECX), reg(EDX), HGOBJECT{arg(0)}, arg(1), arg(2), 0x%X, [](uint32_t mem) -> uint32_t {\n'
                    '%s\n        return %s;\n    }%s);\n'
                    '    avpe_probe_note_va(0x%08X, r);\n    ret(r, 12);\n    avpe_verify_exit(0x%08X);\n}\n') % (
            va, va, p['size'], lam, p['ret'], (', %d' % p['saves']) if p['saves'] != 4 else '', va, va)
    if kind == 'sinit_named':
        h = '/* 0x%08X  static initialiser: %s = the name table\'s entry for it */\n' % (va, c_str(p['text']))
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    classgen::NameEntry(AVPE_STR(0x%08X, %s), 0x%08X, 0x%X);\n'
                    '    ret_void(0);\n    avpe_verify_exit(0x%08X);\n}\n') % (va, va, p['name'], c_str(p['text']), p['out'], p['value'], va)
    if kind == 'sinit_atexit':
        h = '/* 0x%08X  static initialiser: atexit(0x%08X), a static object\'s destructor */\n' % (va, p)
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    ret(gcall(classgen::CRT_atexit, 0, {0x%08X}), 0);           /* atexit\'s eax (a tail call) */\n'
                    '    avpe_verify_exit(0x%08X);\n}\n') % (va, va, p, va)
    if kind == 'sinit_message':
        h = '/* 0x%08X  static initialiser: message type %s (0x%X) = MessageTypeEntry::Add(...) */\n' % (va, p['text'], p['id'])
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    classgen::MessageRegister(0x%X, 0x%08X, 0x%X, AVPE_STR(0x%08X, %s), 0x%08X);\n'
                    '    ret_void(0);\n    avpe_verify_exit(0x%08X);\n}\n') % (
            va, va, p['id'], p['create'], p['size'], p['name'], c_str(p['text']), p['g'], va)
    if kind == 'message_create':
        h = '/* 0x%08X  %s::Create (generated: tools/classgen.py) */\n' % (va, c)
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    ret(classgen::MessageCreate(0x%X, 0x%X, %d), 0);\n    avpe_verify_exit(0x%08X);\n}\n') % (
            va, va, p['size'], p['id'], p['flags'], va)
    if kind == 'sinit_register':
        h = '/* 0x%08X  static initialiser: %sclass id = GObject::AddClassId(%s, Create, 0x%X, ...) */\n' % (
            va, (c + ' ') if c else '', c_str(p['text']), p['flags'])
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    m32(0x%08X) = gcall(GObject_AddClassId, 16, {AVPE_STR(0x%08X, %s), 0x%08X, 0x%X, 0x%08X});\n'
                    '    ret_void(0);\n    avpe_verify_exit(0x%08X);\n}\n') % (
            va, va, p['g'], p['name'], c_str(p['text']), p['create'], p['flags'], p['out'], va)
    if kind == 'sinit_crc':
        h = '/* 0x%08X  static initialiser: CRC(%s) */\n' % (va, c_str(p['text']))
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    m32(0x%08X) = classgen::CRC(AVPE_STR(0x%08X, %s));\n'
                    '    ret_void(0);\n    avpe_verify_exit(0x%08X);\n}\n') % (va, va, p['g'], p['s'], c_str(p['text']), va)
    if kind == 'GetParentClassId' and not c:
        h = '/* 0x%08X  returns %s\'s class id (generated: tools/classgen.py; no name in the symbol table: a GetParentClassId by its shape) */\n' % (va, class_of_id[p])
    else:
        h = '/* 0x%08X  %s::%s (generated: tools/classgen.py) */\n' % (va, c, kind)
    if kind == 'SpecifyEditable':
        rows = ['    {AVPE_STR(0x%08X, %s), %d, %d, 0x%X, AVPE_STR(0x%08X, %s)},' % (
            nva, c_str(name), size, flags, off, tva, c_str(tname or '')) for nva, name, size, flags, off, tva, tname in p['fields']]
        par = ('gcall(0x%08X, 4, {pp});   /* %s */' % (p['parent'], db.get(p['parent'], {}).get('name', '') or 'parent'))\
            if p['parent'] else '0;'
        return h + ('static const classgen::Editable ed_%08X[] = {\n%s\n};\n'
                    'void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    uint32_t pp = arg(0);                     /* EditableInfo *& */\n'
                    '    uint32_t n = %s\n'
                    '    n += classgen::SpecifyEditable(pp, ed_%08X);\n'
                    '    ret(n, 4);\n    avpe_verify_exit(0x%08X);\n}\n') % (va, '\n'.join(rows), va, va, par, va, va)
    if kind == 'GetParentClassId':
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    ret(m32(0x%08X), 0);\n    avpe_verify_exit(0x%08X);\n}\n') % (va, va, p, va)
    if kind == 'IsDerivedFromClassId':
        ids = ', '.join('0x%08X' % i for i in p[0])
        return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                    '    uint32_t id = arg(0);\n    g_eax = id;\n'
                    '    ret_al(classgen::IsDerivedFromClassId(id, {%s}, 0x%08X%s), 4);\n'
                    '    avpe_verify_exit(0x%08X);\n}\n') % (va, va, ids, p[1], ', true' if p[2] else '', va)
    if p['kind'] == 'inline':
        cons = 'classgen::InlineObject{0x%08X}' % p['vt']
    else:
        rs = [r.upper() for r in p['ctor_regs']]
        regs = 'AVPE_REGS%d(%s)' % (len(rs), ', '.join(rs))
        if p['kind'] == 'ctor':
            cons = 'classgen::CallConstructor{0x%08X, %s%s}' % (p['ctor'], regs, ', true' if p.get('stack') else '')
        elif p.get('byte'):
            cons = 'classgen::BaseThenVtable{0x%08X, %s, 0x%08X, 0x%X, %d}' % (p['ctor'], regs, p['vt'], p['byte'][0], p['byte'][1])
        else:
            cons = 'classgen::BaseThenVtable{0x%08X, %s, 0x%08X}' % (p['ctor'], regs, p['vt'])
    return h + ('void sub_%08X(void)\n{\n    avpe_verify_enter(0x%08X);\n'
                '    uint32_t r = classgen::Create(reg(ECX), reg(EDX), HGOBJECT{arg(0)}, arg(1), arg(2), 0x%X, %s);\n'
                '    avpe_probe_note_va(0x%08X, r);\n    ret(r, 12);\n    avpe_verify_exit(0x%08X);\n}\n') % (va, va, p['size'], cons, va, va)


BEGIN, END = '/* AVPE-CLASSGEN BEGIN', 'AVPE-CLASSGEN END */'
total = 0
probes = []
for f in os.listdir(os.path.join(D, 'src')):                 # files that lost all generated entries
    path = os.path.join(D, 'src', f)
    if f in EXCLUDE:
        continue
    if f.endswith('.cpp') and f not in {k.replace('/', '_') for k in gen}:
        src = open(path).read()
        if 'AVPE-CLASSGEN BEGIN' in src:
            open(path, 'w').write(re.sub(r'\n?/\* AVPE-CLASSGEN BEGIN.*?/\* AVPE-CLASSGEN TAIL \*/\n', '\n', src, flags=re.S).rstrip('\n') + '\n')
for fname, items in gen.items():
    if fname.replace('/', '_') in EXCLUDE:
        for va, kind, c, p in sorted(items):                    # untouched, but keep their probes
            if kind in ('GetParentClassId', 'message_create'):
                probes.append('va=%X' % va)
            elif kind == 'IsDerivedFromClassId':
                probes.extend(['va=%X arg=*%X' % (va, i) for i in p[0]] + ['va=%X arg=12345678' % va])
        continue
    path = os.path.join(D, 'src', fname.replace('/', '_'))
    src = open(path).read() if os.path.exists(path) else ''
    src = re.sub(r'\n?/\* AVPE-CLASSGEN BEGIN.*?AVPE-CLASSGEN END \*/\n.*?/\* AVPE-CLASSGEN TAIL \*/\n', '\n', src, flags=re.S)
    parts = ['%s: class boilerplate (Create, IsDerivedFromClassId,' % BEGIN,
             ' * GetParentClassId, SpecifyEditable: the editable-field table, the',
             ' * static initialisers that register the class and compute name CRCs) in',
             ' * the shapes every class compiles to; generated by',
             ' * tools/classgen.py from the machine code, verified like the rest. A',
             ' * function written by hand elsewhere in the file replaces its entry here.',
             ' * ' + END, '#include "avpe/classgen.h"', 'extern "C" {', 'using namespace avpe;', '']
    for va, kind, c, p in sorted(items):
        parts.append(emit(va, kind, c, p))
        total += 1
        if kind in ('GetParentClassId', 'message_create'):
            probes.append('va=%X' % va)
        elif kind == 'IsDerivedFromClassId':
            for i in p[0]:
                probes.append('va=%X arg=*%X' % (va, i))
            probes.append('va=%X arg=12345678' % va)
    parts.append('} // extern "C"\n/* AVPE-CLASSGEN TAIL */\n')
    text = '\n'.join(parts)
    # generated functions a verification sweep sent back (tools/sweep.py
    # --new): written as pending_ so the lifted code keeps running
    text = re.sub(r'void sub_([0-9A-F]{8})\(void\)',
                  lambda m: 'void %s_%s(void)' % ('pending' if int(m.group(1), 16) in gen_pending else 'sub', m.group(1)), text)
    open(path, 'w').write(src.rstrip('\n') + '\n\n' + text)
pf = os.path.join(OUT, 'verify', 'classgen.probes')
os.makedirs(os.path.dirname(pf), exist_ok=True)
open(pf, 'w').write('\n'.join(probes) + '\n')
print('wrote %d functions; %d probes in %s' % (total, len(probes), pf))
