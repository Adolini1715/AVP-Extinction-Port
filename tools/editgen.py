"""SpecifyEditable, read by running it: the field table each class gives the
level editor, recovered exactly so classgen.py can write it as data.

Every SpecifyEditable(EditableInfo *&pp) is straight-line code: it calls its
parent's (which fills the parent's entries and returns their count), then for
each of its own fields fills the next 12-byte entry
    +0 CRC of the field name   +4 size (word)   +7 flags   +8 offset
calls the type registration 0xD6B20 (entry in ebx, a C type name string on
the stack; it sets +6), advances *pp by 12, and finally returns parent count +
its own count. The name's CRC is computed inline, or (GUnit's, the largest
table) for some fields by the out-of-line CRC 0xDA400 (string in edx, CRC in
eax, no stores), which the run executes as it is. analyse(va) runs the function in Unicorn with the parent and
0xD6B20 stubbed, and accepts it only if that is all it did: entries
contiguous, nothing else written, the count right, every CRC traced to a
string the function hashes. Straight-line code means this one run is every
run.
"""
import json, os, re, struct
import capstone
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UC_HOOK_MEM_WRITE, UC_PROT_ALL
from unicorn.x86_const import UC_X86_REG_EAX, UC_X86_REG_EBX, UC_X86_REG_ESP, UC_X86_REG_EIP

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
_info = json.load(open(os.path.join(R, 'xbe_info.json')))
_xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
_FUNCS = {int(f['start'], 16): f for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))}
_abi = json.load(open(os.path.join(R, 'symbols', 'abi.json')))
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
TYPE_REG = 0xD6B20
NAME_CRC = 0xDA400                 # CRC of the string in edx -> eax; reads the table only
STACK, PPCELL, BUF, K, RET = 0x00F00000, 0x00E00000, 0x00E10000, 1000, 0xDEAD0000


def _rd(va, n):
    for s in _info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            o = s['raw'] + va - s['va']
            return _xbe[o:o + n]
    return b''


def cstr(va):
    b = _rd(va, 256)
    return b[:b.index(b'\0')].decode('latin1') if b and b'\0' in b else None


_tab = [struct.unpack_from('<I', _rd(0x26AFA0, 1024), 4 * i)[0] for i in range(256)]


def crc(s):
    c = 0xFFFFFFFF
    for ch in s.encode('latin1'):
        c = _tab[(ch ^ c) & 0xFF] ^ (c >> 8)
    return c


def _uc():
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    lo, hi = 0x00010000, 0x00800000           # the XBE image and its uninitialised data
    uc.mem_map(lo, hi - lo, UC_PROT_ALL)
    for s in _info['sections']:
        uc.mem_write(s['va'], _xbe[s['raw']:s['raw'] + s['rsize']])
    uc.mem_map(STACK - 0x10000, 0x20000, UC_PROT_ALL)
    uc.mem_map(PPCELL, 0x10000, UC_PROT_ALL)
    uc.mem_map(BUF, 0x40000, UC_PROT_ALL)
    return uc, lo, hi


def analyse(va):
    """-> dict(parent, parent_pops, fields=[(name_va, name, size, flags, offset, type_va, type)]) or (None, reason)"""
    f = _FUNCS.get(va)
    if not f:
        return None, 'unknown function'
    ins = list(_md.disasm(_rd(va, f['size']), va))
    if not ins or ins[0].mnemonic == 'jmp':
        return None, 'thunk'
    calls = [int(i.op_str, 16) for i in ins if i.mnemonic == 'call' and re.fullmatch(r'0x[0-9a-f]+', i.op_str)]
    if any(i.mnemonic == 'call' and not re.fullmatch(r'0x[0-9a-f]+', i.op_str) for i in ins):
        return None, 'indirect call'
    others = [c for c in calls if c not in (TYPE_REG, NAME_CRC)]
    if len(others) > 1:
        return None, 'calls more than its parent'
    parent = others[0] if others else None
    if parent is not None and calls.index(parent) != 0:
        return None, 'parent not called first'
    ppops = _abi.get('0x%08X' % parent, {}).get('pops') if parent else None
    if parent and ppops != 4:
        return None, 'parent convention'
    strings = [int(i.op_str.split(', ')[1], 16) for k, i in enumerate(ins[:-1])
               if i.mnemonic == 'mov' and re.fullmatch(r'e[a-d]x, 0x[0-9a-f]+', i.op_str)
               and re.fullmatch(r'cl, 0x[0-9a-f]+', ins[k + 1].op_str)]
    # strings hashed by the out-of-line CRC: the edx it is given
    for k, i in enumerate(ins):
        if i.mnemonic == 'call' and i.op_str == '0x%x' % NAME_CRC:
            ld = [j for j in ins[max(0, k - 4):k] if j.mnemonic == 'mov' and re.fullmatch(r'edx, 0x[0-9a-f]+', j.op_str)]
            if not ld:
                return None, 'CRC call without a string'
            strings.append(int(ld[-1].op_str.split(', ')[1], 16))
    by_crc = {}
    for s in strings:
        t = cstr(s)
        if t:
            by_crc.setdefault(crc(t), (s, t))

    uc, lo, hi = _uc()
    uc.mem_write(PPCELL, struct.pack('<I', BUF))
    sp = STACK
    sp -= 4; uc.mem_write(sp, struct.pack('<I', PPCELL))
    sp -= 4; uc.mem_write(sp, struct.pack('<I', RET))
    uc.reg_write(UC_X86_REG_ESP, sp)
    uc.reg_write(UC_X86_REG_EAX, PPCELL)
    regs, bad = [], []
    state = {'done': False}

    def on_code(u, addr, size, _):
        if addr == RET:
            state['done'] = True; u.emu_stop(); return
        if addr in (parent, TYPE_REG):
            esp = u.reg_read(UC_X86_REG_ESP)
            ret, a0 = struct.unpack('<II', bytes(u.mem_read(esp, 8)))
            if addr == parent:
                if a0 != PPCELL:
                    bad.append('parent not given pp')
                u.reg_write(UC_X86_REG_EAX, K)
            else:
                ebx = u.reg_read(UC_X86_REG_EBX)
                regs.append((ebx, a0, bytes(u.mem_read(ebx, 12))))
                u.reg_write(UC_X86_REG_EAX, 0)
            u.reg_write(UC_X86_REG_ESP, esp + 8)
            u.reg_write(UC_X86_REG_EIP, ret)

    def on_write(u, access, addr, size, value, _):
        if BUF <= addr < BUF + 0x40000 or PPCELL <= addr < PPCELL + 4 or STACK - 0x10000 <= addr < STACK + 0x10000:
            return
        bad.append('writes 0x%08X' % addr)

    uc.mem_map(RET & ~0xFFF, 0x1000, UC_PROT_ALL)
    uc.hook_add(UC_HOOK_CODE, on_code)
    uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
    try:
        uc.emu_start(va, 0, count=200000)
    except Exception as e:
        return None, 'emulation: %s' % e
    if not state['done']:
        return None, 'did not return'
    if bad:
        return None, bad[0]
    n = len(regs)
    eax = uc.reg_read(UC_X86_REG_EAX)
    pp = struct.unpack('<I', bytes(uc.mem_read(PPCELL, 4)))[0]
    if eax != (K if parent else 0) + n:
        return None, 'count %d for %d fields' % (eax - (K if parent else 0), n)
    if pp != BUF + 12 * n:
        return None, 'pp advanced %d' % (pp - BUF)
    if uc.reg_read(UC_X86_REG_ESP) != STACK:
        return None, 'stack'
    fields = []
    for k, (ebx, tva, e) in enumerate(regs):
        if ebx != BUF + 12 * k:
            return None, 'entry %d not contiguous' % k
        c, size, off = struct.unpack_from('<I', e, 0)[0], struct.unpack_from('<H', e, 4)[0], struct.unpack_from('<I', e, 8)[0]
        if e[6]:                                      # byte 5 is the size's high byte (arrays)
            return None, 'writes entry byte 6'
        if c not in by_crc:
            return None, 'name CRC %08X not from a string it hashes' % c
        fields.append((by_crc[c][0], by_crc[c][1], size, e[7], off, tva, cstr(tva)))
    # nothing written after the last entry
    tail = bytes(uc.mem_read(BUF + 12 * n, 64))
    if any(tail):
        return None, 'writes past the entries'
    return {'parent': parent, 'fields': fields}, None
