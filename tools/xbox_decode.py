#!/usr/bin/env python3
"""Decode a DATX/TEMX/PLAX chunk with Zono's own decoder: the x86 code of
CBWJCompressor::Decompress (0x00132E10) and ::Read (0x00132FF0) from the Xbox
default.xbe, executed in Unicorn. Only the memory allocator (0x000DC4D0) is
stubbed; any other call outside the compressor aborts the run, so nothing is
silently faked. Used as the oracle for tools/bwj.py and for patched chunks.
"""
import json, struct, sys
from unicorn import Uc, UC_ARCH_X86, UC_MODE_32, UC_HOOK_CODE, UcError
from unicorn.x86_const import *

ROOT = '.'
info = json.load(open(f'{ROOT}/xbe_info.json')); xbe = open(f'{ROOT}/disc/default.xbe', 'rb').read()
DECOMPRESS, READ, ALLOC = 0x132E10, 0x132FF0, 0x0DC4D0
STUB_CB, STUB_RET = 0x0F000000, 0x0F000100
HEAP, STACK, OBJ, DST, SRC = 0x10000000, 0x20000000, 0x30000000, 0x40000000, 0x50000000

def run(chunk):
    size = struct.unpack_from('<I', chunk)[0]; stream = chunk[4:]
    uc = Uc(UC_ARCH_X86, UC_MODE_32)
    uc.mem_map(0x10000, (info['hdr']['size_image'] + 0xFFFF) & ~0xFFFF)
    for s in info['sections']:
        uc.mem_write(s['va'], xbe[s['raw']:s['raw'] + s['rsize']])
    for base, sz in ((STUB_CB & ~0xFFF, 0x1000), (HEAP, 0x400000), (STACK, 0x100000), (OBJ, 0x1000),
                     (DST, (size + 0x10FFF) & ~0xFFF), (SRC, (len(stream) + 0x10FFF) & ~0xFFF)):
        uc.mem_map(base, sz)
    uc.mem_write(SRC, stream)
    state = dict(heap=HEAP + 0x100, srcpos=0, calls=[])
    pages = set(range(0x132E10, 0x133620))           # the compressor's own code
    def hook(uc, addr, size_, _):
        esp = uc.reg_read(UC_X86_REG_ESP)
        if addr == ALLOC:                              # stdcall(heap, size, align) -> ptr
            n = struct.unpack('<I', uc.mem_read(esp + 8, 4))[0]
            p = (state['heap'] + 15) & ~15; state['heap'] = p + n + 16
            uc.mem_write(p - 4, struct.pack('<I', 0))
            ret = struct.unpack('<I', uc.mem_read(esp, 4))[0]
            uc.reg_write(UC_X86_REG_EAX, p); uc.reg_write(UC_X86_REG_ESP, esp + 16); uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == STUB_CB:                          # fastcall(ctx, dst, n) -> bytes read
            dst = uc.reg_read(UC_X86_REG_EDX); n = struct.unpack('<I', uc.mem_read(esp + 4, 4))[0]
            take = stream[state['srcpos']:state['srcpos'] + n]; state['srcpos'] += len(take)
            uc.mem_write(dst, take)
            ret = struct.unpack('<I', uc.mem_read(esp, 4))[0]
            uc.reg_write(UC_X86_REG_EAX, len(take)); uc.reg_write(UC_X86_REG_ESP, esp + 8); uc.reg_write(UC_X86_REG_EIP, ret)
        elif addr == STUB_RET:
            uc.emu_stop()
        elif addr not in pages and not (STUB_CB <= addr < STUB_CB + 0x1000):
            state['calls'].append(hex(addr)); uc.emu_stop()
    uc.hook_add(UC_HOOK_CODE, hook)

    def call(fn, regs, args):
        sp = STACK + 0xF0000
        frame = struct.pack(f'<{1 + len(args)}I', STUB_RET, *args)
        sp -= len(frame); uc.mem_write(sp, frame)
        uc.reg_write(UC_X86_REG_ESP, sp)
        for r, v in regs.items(): uc.reg_write(r, v)
        uc.emu_start(fn, 0, count=50_000_000)
        if state['calls']: raise RuntimeError('left the compressor at ' + state['calls'][0])
        return uc.reg_read(UC_X86_REG_EAX)

    call(DECOMPRESS, {UC_X86_REG_ESI: OBJ, UC_X86_REG_EDI: STUB_CB}, [0])
    got = call(READ, {}, [OBJ, DST, size])
    return bytes(uc.mem_read(DST, size)), got

if __name__ == '__main__':
    sys.path.insert(0, 'tools'); import bwj
    exec(open('tools/tbd_fields.py').read().split("if __name__")[0])
    for label, path in (('original', 'disc/tbd/M01/FD090663.tbx'), ('patched', 'mod/FD090663.tbx')):
        d = open(path, 'rb').read(); st, n = blocks(d)[13]['DATX']
        xb, got = run(d[st:st + n])
        mine, _, _ = bwj.decompress(d[st + 6:st + n], 5)
        print(f'{label}: Zono decoder returned {got}, {len(xb)} bytes; identical to tools/bwj.py: {xb == mine}')
        p = xb.find(struct.pack('<II', h('MoverData.MaxRunSpeed'), 4), 0x54, 0xa8)
        print(f'   MarineMover MoverData.MaxRunSpeed = {struct.unpack_from("<f", xb, p + 8)[0]}')
