#!/usr/bin/env python3
"""Replay ORIGINAL Xbox code from a guest-state snapshot taken by the native
build (avpe_bridge.c avpe_snapshot): the oracle for lifted-code bugs.

Usage: snap_replay.py <snapshot.bin> <stop_va> [--trace-calls]
Maps guest RAM 0..64 MB from the snapshot, restores the registers and the fs
segment (TIB), and runs original x86 until execution reaches stop_va (the
caller's return address). Any access outside guest RAM, or a call into the
kernel bridge (0xFE000000 synthetic thunks), stops the run and is reported:
nothing is faked silently. --trace-calls logs every call/return with eax.
"""
import struct, sys
from unicorn import *
from unicorn.x86_const import *

snap, stop = sys.argv[1], int(sys.argv[2], 16)
TRACE = '--trace-calls' in sys.argv
raw = open(snap, 'rb').read()
hdr = struct.unpack_from('<12I', raw, 0)
assert hdr[0] in (0x50414E53, 0x32414E53), 'not a snapshot'
HDR = 64 if hdr[0] == 0x32414E53 else 48            # v2 carries the x87 control word
eax, ecx, edx, ebx, esp, ebp, esi, edi, fsbase, ramsize = hdr[1:11]
fpcw = struct.unpack_from('<I', raw, 48)[0] if HDR == 64 else 0x027F
ram = raw[HDR:HDR + ramsize]

uc = Uc(UC_ARCH_X86, UC_MODE_32)
uc.mem_map(0, ramsize)
uc.mem_write(0, ram)
assert ram[0x03C00000:0x04000000].count(0) == 0x400000, 'replay allocation region is in use in the snapshot'
# GDT with flat code/data and an fs segment based at the TIB
GDT = 0xC0000000
uc.mem_map(GDT, 0x1000)
def desc(base, limit, access, flags):
    d = limit & 0xFFFF | (base & 0xFFFFFF) << 16 | access << 40 | ((limit >> 16) & 0xF) << 48 | flags << 52 | (base >> 24) << 56
    return struct.pack('<Q', d)
gdt = desc(0, 0, 0, 0) + desc(0, 0xFFFFF, 0x9A, 0xC) + desc(0, 0xFFFFF, 0x92, 0xC) + desc(fsbase, 0xFFFFF, 0x92, 0xC)
uc.mem_write(GDT, gdt)
uc.reg_write(UC_X86_REG_GDTR, (0, GDT, len(gdt) - 1, 0x0))
# Unicorn ignores FS_BASE in 32-bit mode: load all segments from the GDT
# (cs, ss, ds, es must be loaded too or the fs descriptor never takes effect).
uc.reg_write(UC_X86_REG_CS, 1 << 3); uc.reg_write(UC_X86_REG_SS, 2 << 3)
uc.reg_write(UC_X86_REG_DS, 2 << 3); uc.reg_write(UC_X86_REG_ES, 2 << 3)
uc.reg_write(UC_X86_REG_FS, 3 << 3)
assert struct.unpack('<I', bytes(uc.mem_read(fsbase + 0x18, 4)))[0] == fsbase, 'TIB self pointer mismatch'
for r, v in ((UC_X86_REG_EAX, eax), (UC_X86_REG_ECX, ecx), (UC_X86_REG_EDX, edx), (UC_X86_REG_EBX, ebx),
             (UC_X86_REG_ESP, esp), (UC_X86_REG_EBP, ebp), (UC_X86_REG_ESI, esi), (UC_X86_REG_EDI, edi)):
    uc.reg_write(r, v)
# x87 state as the game had it (Unicorn starts with control word 0: 24-bit
# precision, every exception unmasked).
uc.reg_write(UC_X86_REG_FPCW, fpcw & 0xFFFF)
# --fp-in: the function takes st(0) from its caller: push the native st(0).
fp_hdr = struct.unpack_from('<3I', raw, 52) if HDR == 64 else (0, 0, 0)
# The x87 TOP as the game had it. The original code itself leaves values on
# the register stack at times (two from about frame 87 to 1002 of M01, the
# same in the all-lifted build), and fnstsw shows TOP: entering with TOP 0
# would make the original's status word differ from the real run's.
# --fp-in2: two arguments on the x87 stack (st(1), st(0): _CIpow), from the snapshot's .fp sidecar
FP_PUSH = 2 if '--fp-in2' in sys.argv else 1 if '--fp-in' in sys.argv else 0
if fp_hdr[0] & 0x100:
    _top = (fp_hdr[0] + FP_PUSH) & 7
    uc.reg_write(UC_X86_REG_FPSW, (uc.reg_read(UC_X86_REG_FPSW) & ~0x3800) | (_top << 11))
if FP_PUSH and fp_hdr[0] & 0x100:
    _vals = [struct.pack('<II', fp_hdr[1], fp_hdr[2])]
    if FP_PUSH == 2:
        _side = open(sys.argv[1] + '.fp', 'rb').read()
        _vals = [_side[8:16], _side[0:8]]               # st(1) first, then st(0) on top
    _code = 0x03FFF000                                  # fld qword [0x03FFFFF0] in the scratch region
    for _v in _vals:
        uc.mem_write(0x03FFFFF0, _v)
        uc.mem_write(_code, b'\xdd\x05\xf0\xff\xff\x03')
        uc.emu_start(_code, _code + 6)
    uc.mem_write(0x03FFFFF0, b'\0' * 8); uc.mem_write(_code, b'\0' * 6)
fp_top0 = (uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7
start = struct.unpack('<I', uc.mem_read(esp, 4))[0]   # not used: we enter at the callee
RET_AT_ENTRY = start
entry = int(sys.argv[3], 16) if len(sys.argv) > 3 and not sys.argv[3].startswith('--') else None

state = {'why': None, 'calls': 0, 'insns': 0, 'kcalls': collections.Counter() if False else {}}
# Kernel bridge: thunk slot i lives at 0xFE000000 + 4*i; the ordinal comes from
# the XBE's own import table. Only handlers listed here run; anything else stops.
import json, os
_info = json.load(open(os.path.join(os.path.dirname(__file__), '..', 'xbe_info.json')))
_xbe = open(os.path.join(os.path.dirname(__file__), '..', 'disc', 'default.xbe'), 'rb').read()
_kt = _info['hdr']['kernel_thunk']
_sec = [s for s in _info['sections'] if s['va'] <= _kt < s['va'] + s['vsize']][0]
_o = _sec['raw'] + _kt - _sec['va']; ORD = []
while True:
    v = struct.unpack_from('<I', _xbe, _o + 4 * len(ORD))[0]
    if v == 0: break
    ORD.append(v & 0x7FFFFFFF)
def _ret(uc, nargs, eax):
    sp = uc.reg_read(UC_X86_REG_ESP); ra = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
    uc.reg_write(UC_X86_REG_EAX, eax); uc.reg_write(UC_X86_REG_ESP, sp + 4 + 4 * nargs); uc.reg_write(UC_X86_REG_EIP, ra)
_bump = {'next': 0x03C00000, 'end': 0x04000000}
def _nt_alloc(uc):
    # NtAllocateVirtualMemory(PVOID *Base, ULONG ZeroBits, SIZE_T *Size, ULONG Type, ULONG Protect)
    sp = uc.reg_read(UC_X86_REG_ESP)
    pbase, _, psize, typ, _ = struct.unpack('<5I', bytes(uc.mem_read(sp + 4, 20)))
    base = struct.unpack('<I', bytes(uc.mem_read(pbase, 4)))[0]
    size = struct.unpack('<I', bytes(uc.mem_read(psize, 4)))[0]
    if base == 0:
        size = (size + 0xFFF) & ~0xFFF
        if _bump['next'] + size > _bump['end']: return _ret(uc, 5, 0xC0000017)   # NO_MEMORY
        base = _bump['next']; _bump['next'] += size
        uc.mem_write(pbase, struct.pack('<I', base)); uc.mem_write(psize, struct.pack('<I', size))
    return _ret(uc, 5, 0)                        # commit of an existing reservation: no-op
def _nt_free(uc):
    # Decommitted pages come back zeroed on the console (and under the
    # toolkit's RECOMP_HEAP_RECLAIM); release is a no-op for the replay.
    sp = uc.reg_read(UC_X86_REG_ESP)
    pbase, psize, typ = struct.unpack('<3I', bytes(uc.mem_read(sp + 4, 12)))
    base = struct.unpack('<I', bytes(uc.mem_read(pbase, 4)))[0]
    size = struct.unpack('<I', bytes(uc.mem_read(psize, 4)))[0]
    if typ & 0x4000 and size and base + size <= ramsize:
        uc.mem_write(base, b'\0' * size)
    return _ret(uc, 3, 0)
def _rtl_init_ansi(uc):
    # RtlInitAnsiString(ANSI_STRING *dest, const char *src): Length = strlen, MaximumLength =
    # Length + 1, Buffer = src (NT's; the bridge's bridge_RtlInitAnsiString does the same)
    sp = uc.reg_read(UC_X86_REG_ESP)
    dest, src = struct.unpack('<2I', bytes(uc.mem_read(sp + 4, 8)))
    if dest:
        n = 0
        if src:
            while n < 0xFFFE and uc.mem_read(src + n, 1)[0]:
                n += 1
            uc.mem_write(dest, struct.pack('<HHI', n, n + 1, src))
        else:
            uc.mem_write(dest, struct.pack('<HHI', 0, 0, 0))
    return _ret(uc, 2, 0)
KERNEL = {
    289: _rtl_init_ansi,
    184: _nt_alloc,
    199: lambda uc: _nt_free(uc),     # NtFreeVirtualMemory(Base*, Size*, Type)
    171: lambda uc: _ret(uc, 1, 0),   # MmFreeContiguousMemory(Base): no-op (replay never reallocates)
    277: lambda uc: _ret(uc, 1, 0),   # RtlEnterCriticalSection: single-threaded replay
    294: lambda uc: _ret(uc, 1, 0),   # RtlLeaveCriticalSection
    291: lambda uc: _ret(uc, 1, 0),   # RtlInitializeCriticalSection
    129: lambda uc: _ret(uc, 0, 0),   # KeRaiseIrqlToDpcLevel: the old IRQL (passive) as the bridge returns it
    160: lambda uc: _ret(uc, 0, 0),   # KfRaiseIrql (fastcall cl): old IRQL 0
    161: lambda uc: _ret(uc, 0, 0),   # KfLowerIrql (fastcall cl): the bridge leaves eax 0
    99: lambda uc: _ret(uc, 3, 0),    # KeDelayExecutionThread: STATUS_SUCCESS
}
# The native build's graphics bridges (recomp/avpe_bridge.c), applied here too
# so the comparison is original-vs-lifted code under the same environment.
BRIDGES = {
    0x001767F0: lambda uc: _ret(uc, 2, 0),   # D3D_BlockOnTime: GPU treated as finished
    0x001723B0: lambda uc: _ret(uc, 0, 0),   # D3DDevice_BlockUntilVerticalBlank: no wait
}
# --externs=FILE: calls the native run recorded instead of running them (what
# each returned in eax/ecx/edx, how many bytes it popped, the bytes it
# changed), applied in order when execution reaches the function; and the
# time stamp counter values it read, returned by rdtsc in order.
EXTERNS, TSC = [], None
EXTERN_ALL = bool(__import__('os').environ.get('AVPE_EXTERN_ALL'))
FP_STUB, FP_STUB_VAL = 0x03FFE000, 0x03FFE040   # scratch region: st(0) returns of externs
for a in sys.argv:
    if a.startswith('--externs='):
        _e = json.load(open(a[10:]))
        EXTERNS, TSC = _e['calls'], _e['tsc']
def _extern(uc, e):
    # the original's inputs to the call against those the decompiled code passed
    sp0 = uc.reg_read(UC_X86_REG_ESP); n = 0
    inp = e.get('in') or {}
    what = f"{e['va']:08X}" + (f" ({inp['name']})" if inp.get('name') else '')
    for i, v in enumerate(inp.get('args', [])):
        o = struct.unpack('<I', bytes(uc.mem_read(sp0 + 4 + 4 * i, 4)))[0]
        if o != v and n < 8:
            print(f"[EXTERN] {what} argument {i}: original {o:08X} decompiled {v:08X}"); n += 1
    R = {'eax': UC_X86_REG_EAX, 'ecx': UC_X86_REG_ECX, 'edx': UC_X86_REG_EDX, 'ebx': UC_X86_REG_EBX,
         'esi': UC_X86_REG_ESI, 'edi': UC_X86_REG_EDI, 'ebp': UC_X86_REG_EBP}
    for r, v in inp.get('regs', {}).items():
        o = uc.reg_read(R[r])
        if o != v and n < 8:
            print(f"[EXTERN] {e['va']:08X} {r}: original {o:08X} decompiled {v:08X}"); n += 1
    for ent in inp.get('mem', []):
        if len(ent) == 3:                     # kernel call: (label, address, bytes) at the recorded address
            label, p, hx = ent
            want = bytes.fromhex(hx)
            try: got = bytes(uc.mem_read(p, len(want)))
            except UcError: got = b''
            if got != want and n < 8:
                k = next((j for j in range(min(len(got), len(want))) if got[j] != want[j]), min(len(got), len(want)))
                print(f"[EXTERN] {what} *{label} +{k:X}: original {got[k:k + 4].hex()} decompiled {want[k:k + 4].hex()}"); n += 1
            continue
        i, hx = ent
        p = uc.reg_read(R[i]) if isinstance(i, str) else struct.unpack('<I', bytes(uc.mem_read(sp0 + 4 + 4 * i, 4)))[0]
        want = bytes.fromhex(hx)
        try: got = bytes(uc.mem_read(p, len(want)))
        except UcError: got = b''
        if got != want and n < 8:
            k = next((j for j in range(min(len(got), len(want))) if got[j] != want[j]), min(len(got), len(want)))
            print(f"[EXTERN] {e['va']:08X} *{'argument ' + str(i) if isinstance(i, int) else i} +{k:X}: original {got[k:k + 4].hex()} decompiled {want[k:k + 4].hex()}"); n += 1
    for addr, hx in e['writes']:
        if addr < ramsize:
            uc.mem_write(addr, bytes.fromhex(hx))
    sp = uc.reg_read(UC_X86_REG_ESP); ra = struct.unpack('<I', bytes(uc.mem_read(sp, 4)))[0]
    uc.reg_write(UC_X86_REG_EAX, e['eax']); uc.reg_write(UC_X86_REG_ECX, e['ecx']); uc.reg_write(UC_X86_REG_EDX, e['edx'])
    uc.reg_write(UC_X86_REG_ESP, sp + e['pop']); uc.reg_write(UC_X86_REG_EIP, ra)
    if 'fp_push' in e:
        # the call returned a value in st(0): a stub in the scratch region loads
        # the recorded double (fld qword) and jumps back to the caller
        # (fixed code: Unicorn keeps translated blocks, so the stub never changes;
        # it returns through the return address placed in the last popped slot)
        uc.mem_write(FP_STUB_VAL, struct.pack('<II', *e['fp_push']))
        uc.mem_write(FP_STUB, b'\xdd\x05' + struct.pack('<I', FP_STUB_VAL) + b'\xc3')
        uc.reg_write(UC_X86_REG_ESP, sp + e['pop'] - 4)
        uc.mem_write(sp + e['pop'] - 4, struct.pack('<I', ra))
        uc.reg_write(UC_X86_REG_EIP, FP_STUB)
        state['fp_stub'] = True
import collections
last = collections.deque(maxlen=int(__import__('os').environ.get('LAST', '60')))
# --trace-range=START:END: report which instructions in [START, END) ran
# (the function's own code: which of its paths the replay took)
TR_LO, TR_HI = next(((int(a[14:].split(':')[0], 16), int(a[14:].split(':')[1], 16)) for a in sys.argv
                     if a.startswith('--trace-range=')), (0, 0))
TR = set()
# calls that never return and spin loops (recomp/noreturn.json): the function under test
# that reaches one is compared there, as the bridge ends the probe's capture there
_nr = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'recomp', 'noreturn.json')))
NORET_CALLEES = {int(k, 16) for k in _nr['callees']}
NORET_POINTS = {int(k, 16) for k in _nr['points']}
def on_code(uc, addr, size, _):
    state['insns'] += 1
    if TR_LO <= addr < TR_HI:
        TR.add(addr)
    last.append((addr, size))
    # The caller's return address is reached when the function returns, but
    # also by a nested call made from the same call site (a Create whose
    # constructor creates another object of the same class): only the return
    # that unwinds past the entry frame counts. A --stop-at callee is the first hit.
    if addr == stop and (stop != RET_AT_ENTRY or uc.reg_read(UC_X86_REG_ESP) > esp):
        state['why'] = 'returned to caller'; uc.emu_stop(); return
    if TR_LO and state['insns'] > 1 and (addr in NORET_POINTS and TR_LO <= addr < TR_HI or addr in NORET_CALLEES and
            TR_LO <= struct.unpack('<I', bytes(uc.mem_read(uc.reg_read(UC_X86_REG_ESP), 4)))[0] < TR_HI):
        print(f'reached {addr:08X}, which never returns: compared there')
        state['why'] = 'returned to caller'; uc.emu_stop(); return
    if addr in LOGCALLS:
        state['calls'] += 1
        if state['calls'] == DUMP_AT:
            open(snap + f'.call{DUMP_AT}', 'wb').write(bytes(uc.mem_read(0, ramsize)))
            print(f'[DUMP] original state at logged call #{DUMP_AT} -> {snap}.call{DUMP_AT}')
        sp = uc.reg_read(UC_X86_REG_ESP); w = struct.unpack('<4I', bytes(uc.mem_read(sp, 16)))
        print(f'[CALL] {addr:08X} ret={w[0]:08X} args {w[1]:08X} {w[2]:08X} {w[3]:08X} ecx={uc.reg_read(UC_X86_REG_ECX):08X}')
    if size == 2 and TSC is not None and bytes(uc.mem_read(addr, 2)) == b'\x0f\x31':
        if not TSC:
            state['why'] = 'rdtsc: no more recorded values'; uc.emu_stop(); return
        v = TSC.pop(0)
        uc.reg_write(UC_X86_REG_EAX, v & 0xFFFFFFFF); uc.reg_write(UC_X86_REG_EDX, v >> 32)
        uc.reg_write(UC_X86_REG_EIP, addr + 2); return
    if EXTERN_ALL and EXTERNS and state['insns'] > 1 and TR_LO:
        # every call the decompiled function made was recorded: a callee entered
        # straight from the function under test (its return address in the
        # function) takes the next record for that callee; records before it
        # belong to callees the C++ ran inline (the oracle ran those for real)
        ra = struct.unpack('<I', bytes(uc.mem_read(uc.reg_read(UC_X86_REG_ESP), 4)))[0]
        sp_ = uc.reg_read(UC_X86_REG_ESP)
        if TR_LO <= ra < TR_HI or any(e['va'] == addr and e.get('esp') == sp_ for e in EXTERNS[:8]):   # or a tail jump
            k = next((i for i, e in enumerate(EXTERNS) if e['va'] == addr), None)
            if k is not None:
                del EXTERNS[:k]
                state['kcalls']['extern_%X' % addr] = state['kcalls'].get('extern_%X' % addr, 0) + 1
                _extern(uc, EXTERNS.pop(0)); return
    if EXTERNS and addr == EXTERNS[0]['va'] and state['insns'] > 1:
        state['kcalls']['extern_%X' % addr] = state['kcalls'].get('extern_%X' % addr, 0) + 1
        _extern(uc, EXTERNS.pop(0)); return
    if addr in BRIDGES:
        state['kcalls']['bridge_%X' % addr] = state['kcalls'].get('bridge_%X' % addr, 0) + 1
        BRIDGES[addr](uc); return
    if addr >= 0xFE000000:
        slot = (addr - 0xFE000000) // 4; ordn = ORD[slot] if slot < len(ORD) else -1
        if ordn in KERNEL:
            state['kcalls'][ordn] = state['kcalls'].get(ordn, 0) + 1; KERNEL[ordn](uc); return
        state['why'] = f'kernel call ordinal {ordn} (slot {slot}) not handled'; uc.emu_stop(); return
    if TRACE:
        b = uc.mem_read(addr, 1)[0]
        if b == 0xE8:
            tgt = (addr + 5 + struct.unpack('<i', uc.mem_read(addr + 1, 4))[0]) & 0xffffffff
            print(f'  call 0x{tgt:08X} from 0x{addr:08X}')
FLOW_ON = any(a.startswith('--flow=') for a in sys.argv)
def on_bad(uc, access, addr, size, value, _):
    if 0xFE000000 <= addr < 0xFE001000:      # fetch from a bridge thunk: map lazily, handled in on_code
        uc.mem_map(0xFE000000, 0x1000); uc.mem_write(0xFE000000, b'\xc3' * 0x1000); return True
    # Writes into the contiguous window at 0x80000000 (locked textures and
    # vertex buffers, the push buffer): the snapshots do not hold it, so a
    # write maps a zeroed scratch page; what lands there is not compared.
    # Reads of window memory the replay has not written still stop it.
    if 0x80000000 <= addr < 0x90000000 and (access == UC_MEM_WRITE_UNMAPPED or (FLOW_ON and access == UC_MEM_READ_UNMAPPED)):
        base = addr & ~0xFFFF
        for pg in range(base, ((addr + size - 1) & ~0xFFFF) + 0x10000, 0x10000):
            try: uc.mem_map(pg, 0x10000)
            except UcError: pass
        state['kcalls']['window_pages'] = state['kcalls'].get('window_pages', 0) + 1
        return True
    if addr == stop and access == UC_MEM_FETCH_UNMAPPED:   # probe marker return address
        state['why'] = 'returned to caller'; return False
    state['why'] = f'unmapped access 0x{addr:08X} at eip 0x{uc.reg_read(UC_X86_REG_EIP):08X}'; return False
# --watch=va[,va...]: log every original write to these words with eip and the
# return addresses on the guest stack (what the lifted code should also do).
DUMP_AT = next((int(a[15:]) for a in sys.argv if a.startswith('--dump-at-call=')), -1)
LOGCALLS = set()
for a in sys.argv:
    if a.startswith('--log-calls='):
        LOGCALLS |= {int(x, 16) for x in a[12:].split(',')}
WATCH = set()
for a in sys.argv:
    if a.startswith('--watch='):
        WATCH |= {int(x, 16) & ~3 for x in a[8:].split(',')}
def on_write(uc, access, addr, size, value, _):
    if (addr & ~3) in WATCH or ((addr + size - 1) & ~3) in WATCH:
        eip = uc.reg_read(UC_X86_REG_EIP); sp = uc.reg_read(UC_X86_REG_ESP)
        print(f'  [W] {addr:08X} <- {value & 0xffffffff:08X} (size {size}) eip {eip:08X} insn #{state["insns"]}')
if WATCH: uc.hook_add(UC_HOOK_MEM_WRITE, on_write)
uc.hook_add(UC_HOOK_CODE, on_code)
uc.hook_add(UC_HOOK_MEM_UNMAPPED, on_bad)
# The kernel debug service (int 0x2D, OutputDebugStringA 0x14D9B3) returns past the int3 slide
# byte that follows it, as with no debugger attached (the translated code: recomp_debug_service).
# Any other interrupt still stops the run.
def on_intr(uc, intno, _):
    eip = uc.reg_read(UC_X86_REG_EIP)
    if intno == 0x2D and uc.mem_read(eip, 1) == b'\xcc':
        uc.reg_write(UC_X86_REG_EIP, eip + 1)
    else:
        if not state['why']: state['why'] = f'Unhandled CPU exception (UC_ERR_EXCEPTION: interrupt 0x{intno:X}) at eip 0x{eip:08X}'
        uc.emu_stop()
uc.hook_add(UC_HOOK_INTR, on_intr)
# --flow=FILE: library-to-game data flow (the D3D scout). Every byte the Microsoft library
# sections (D3D .. XPP, 0x170D60..0x205000) write is remembered with the writing function;
# a read of such a byte by game code (below 0x141000), or of anything inside the library
# sections, is appended to FILE as "reader_fn writer_fn region addr size". Game writes
# clear the mark. Unwritten contiguous-window reads are mapped as zeros and reported too.
FLOW = next((a[7:] for a in sys.argv if a.startswith('--flow=')), None)
if FLOW:
    import bisect as _bis
    _fl_funcs = sorted(int(k, 16) for k in json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'symbols', 'abi.json'))))
    def _fl_fn(a):
        i = _bis.bisect(_fl_funcs, a) - 1
        return _fl_funcs[i] if i >= 0 else 0
    _fl_shadow = {}; _fl_out = {}; _fl_wr = {}
    LIB_LO, LIB_HI, GAME_HI = 0x00170D60, 0x00205000, 0x00141000
    def _fl_region(a):
        if LIB_LO <= a < LIB_HI: return 'libdata'
        if 0x00F70000 <= a < 0x00F80000: return 'stack'
        if a >= 0x80000000: return 'window'
        if 0x00205000 <= a < 0x00310000: return 'gamedata'
        return 'heap'
    def _fl_write(uc, access, addr, size, value, _):
        eip = uc.reg_read(UC_X86_REG_EIP)
        if LIB_LO <= eip < LIB_HI:
            f = _fl_fn(eip)
            for i in range(size): _fl_shadow[addr + i] = f
            k = (f, _fl_region(addr)); _fl_wr[k] = _fl_wr.get(k, 0) + size
        elif _fl_shadow:
            for i in range(size): _fl_shadow.pop(addr + i, None)
    def _fl_read(uc, access, addr, size, value, _):
        eip = uc.reg_read(UC_X86_REG_EIP)
        if eip >= GAME_HI: return
        w = next((_fl_shadow[addr + i] for i in range(size) if addr + i in _fl_shadow), None)
        if w is None and not (LIB_LO <= addr < LIB_HI): return
        k = (_fl_fn(eip), w or 0, _fl_region(addr), addr & ~3)
        _fl_out[k] = _fl_out.get(k, 0) + 1
    # the GPU register aperture: zeros, with DMA_GET following DMA_PUT (the runtime's nv2a hook
    # does the same: "everything submitted is consumed")
    uc.mem_map(0xFD000000, 0x01000000)
    def _fl_mmio(uc, access, addr, size, value, _):
        if addr == 0xFD800044: uc.mem_write(0xFD800044, bytes(uc.mem_read(0xFD800040, 4)))
        if addr == 0xFD100410: uc.mem_write(0xFD100410, b'\0\0\0\0')      # the PFB flush bit clears itself
        k = ('mmio', addr); _fl_wr[k] = _fl_wr.get(k, 0) + 1
    uc.hook_add(UC_HOOK_MEM_READ, _fl_mmio, begin=0xFD000000, end=0xFDFFFFFF)
    uc.hook_add(UC_HOOK_MEM_WRITE, _fl_write)
    uc.hook_add(UC_HOOK_MEM_READ, _fl_read)
try:
    uc.emu_start(entry, 0, count=int(os.environ.get('FLOW_CAP', '8000000')) if FLOW else 400_000_000)
    if FLOW and not state['why']: state['why'] = 'flow cap reached at eip 0x%08X' % uc.reg_read(UC_X86_REG_EIP)
except UcError as e:
    if not state['why']: state['why'] = f'{e} at eip 0x{uc.reg_read(UC_X86_REG_EIP):08X}'
import capstone
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
if '--last' in sys.argv:
    for a, sz in last:
        try:
            i = next(_md.disasm(bytes(uc.mem_read(a, sz)), a)); print(f'   {a:08X}  {i.mnemonic} {i.op_str}')
        except Exception: print(f'   {a:08X}  ?')
if FLOW:
    with open(FLOW, 'a') as _f:
        for (rf, wf, reg, a), n in sorted(_fl_out.items()):
            _f.write('R %08X %08X %s %08X %d\n' % (rf, wf, reg, a, n))
        for (wf, reg), n in sorted(_fl_wr.items(), key=str):
            _f.write(('M %08X %d\n' % (reg, n)) if wf == 'mmio' else ('W %08X %s %d\n' % (wf, reg, n)))
        _f.write('E %s insns %d why %s\n' % (snap, state['insns'], state['why']))
print(f"stopped: {state['why']} after {state['insns']} instructions; kernel calls handled: {state['kcalls']}")
if TR_HI:
    print('range-trace: ' + ','.join('%X' % a for a in sorted(TR)))
print(f"replay allocations used {(_bump['next'] - 0x03C00000) // 1024} KB")
print('regs ' + ' '.join('%s=%08X' % (n, uc.reg_read(r)) for n, r in (('eax', UC_X86_REG_EAX), ('ecx', UC_X86_REG_ECX),
      ('edx', UC_X86_REG_EDX), ('ebx', UC_X86_REG_EBX), ('esp', UC_X86_REG_ESP), ('ebp', UC_X86_REG_EBP),
      ('esi', UC_X86_REG_ESI), ('edi', UC_X86_REG_EDI))))
def _f80(v):
    """80-bit x87 register value (int) -> float (exact when representable)."""
    if isinstance(v, tuple): v = v[0] | v[1] << 64
    mant, ex, sign = v & ((1 << 64) - 1), (v >> 64) & 0x7FFF, v >> 79 & 1
    if ex == 0x7FFF: return float('nan') if mant << 1 & ((1 << 64) - 1) else (-1) ** sign * float('inf')
    import fractions
    return (-1) ** sign * float(fractions.Fraction(mant) * fractions.Fraction(2) ** (ex - 16383 - 63))
_top = (uc.reg_read(UC_X86_REG_FPSW) >> 11) & 7
_pushed = (fp_top0 - _top) & 7
_st0 = uc.reg_read([UC_X86_REG_FP0, UC_X86_REG_FP1, UC_X86_REG_FP2, UC_X86_REG_FP3, UC_X86_REG_FP4,
                    UC_X86_REG_FP5, UC_X86_REG_FP6, UC_X86_REG_FP7][_top])    # physical register (mantissa, sign|exponent)
print(f"fp pushed={_pushed} st0={struct.pack('<d', _f80(_st0)).hex() if _pushed or FP_PUSH else '-'}")
print(f"eax=0x{uc.reg_read(UC_X86_REG_EAX):08X} esp=0x{uc.reg_read(UC_X86_REG_ESP):08X}")
if state.get('fp_stub'):                              # the extern st(0) stub is the oracle's own scratch
    uc.mem_write(FP_STUB, b'\0' * 0x48)
open(snap + '.after', 'wb').write(bytes(uc.mem_read(0, ramsize)))
