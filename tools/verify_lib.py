"""Comparison of a decompiled function's end state with the original's, for
verify.py (one call per game run) and verify_multi.py (many functions per run).

The original is replayed in the Unicorn oracle (snap_replay.py) from the
snapshot taken at entry to the decompiled function; its end state is compared
with the snapshot taken at the decompiled function's exit:
  - esp, and eax when the decompiled entry declares a return value (ret: eax,
    ret_al: al; ret_void: nothing; the C++ signature is the claim checked)
  - the x87 stack: how many values the function left on it, and st(0) (a
    float result) bit for bit
  - at a --stop-at callee boundary: the registers the callee reads instead,
    and memory except the function's own frame
  - all of guest memory, except dead stack below esp and memory other threads
    change while the main thread runs the function (worker stacks, from the
    run log; the loader's countdown and busy flag 0x2F6850/0x2F6854; the
    timer word 0x740020). The oracle replays the main thread alone.
"""
import math, glob, json, os, re, struct, subprocess, sys, time
import numpy as np

T = os.path.dirname(os.path.abspath(__file__))
OUT = '/home/claude/avpe-recomp'
VJ = '/home/claude/avpe-decomp/verified.json'
NAMES = 'eax ecx edx ebx esp ebp esi edi'.split()


def header(path):
    raw = open(path, 'rb').read(64)
    h = struct.unpack_from('<16I', raw)
    return h, (64 if h[0] == 0x32414E53 else 48)


_returns_cache = {}


def returns(va):
    """'eax', 'al' or None: what the decompiled guest entry returns."""
    if va in _returns_cache:
        return _returns_cache[va]
    kind = 'eax'
    for f in glob.glob('/home/claude/avpe-decomp/src/*.cpp'):
        src = open(f).read()
        m = re.search(r'^void sub_%08X\(void\)\s*\{([^\n]*)\}[ \t]*$' % va, src, re.M)   # a one-line entry
        if not m:
            m = re.search(r'^void sub_%08X\(void\)\s*\{(.*?)^\}' % va, src, re.M | re.S)
        if m:
            b = m.group(1)
            kind = 'al' if 'ret_al(' in b else 'eax' if re.search(r'\bret\(', b) else \
                'st0' if 'ret_st0(' in b else None
            break
    if kind is None and '%X' % va in os.environ.get('VERIFY_FORCE_EAX', '').upper().split(','):
        kind = 'eax'                                        # audit: compare eax of a "void" entry
    _returns_cache[va] = kind
    return kind


def volatile_ranges(run_log):
    v = [(0x740020, 0x740024), (0x2F6850, 0x2F6858)]
    if run_log and os.path.exists(run_log):
        for m in re.finditer(r'stack top 0x([0-9A-Fa-f]{8})', open(run_log, errors='ignore').read()):
            top = int(m.group(1), 16) + 16
            v.append((top - 512 * 1024, top))
    return v


# Functions the oracle cannot run (they read the controllers, the disc or the
# clock). The native build records what each call to one of them from
# decompiled code returned and changed (AVPE_VERIFY_EXTERN; verify_multi.py),
# and the oracle applies that instead of running it (snap_replay.py --externs).
EXTERNS = {
    0x00136C80: 'CXBOXInput::Poll (controllers)',
    0x001371D0: 'CXBOXInput::GetInput (controllers)',
    0x000DEA10: 'CTbdFile::Load (reads the disc)',
}
# VERIFY_EXTERN=va[,va...] adds more for one run: the Direct3D library entries
# a renderer function calls, whose push-buffer writes land in the contiguous
# window at 0x80000000 that snapshots do not hold (notes/RendAPI.md).
for _x in os.environ.get('VERIFY_EXTERN', '').split(','):
    if _x.strip():
        EXTERNS[int(_x, 16)] = 'VERIFY_EXTERN'

# What the decompiled code hands an extern is checked against what the
# original hands it at the same call (the recorded result alone would hide a
# wrong argument): the stack arguments (callee pops / 4, abi.json), the input
# registers abi.json lists, and for the entries below the bytes a pointer
# argument points to, {va: {arg index: size, or f(args) -> size}}.
EXTERN_PTRS = {
    0x001728A0: {0: 24},                                     # D3DDevice_SetViewport(const D3DVIEWPORT8 *)
    0x001721B0: {1: 768},                                    # D3DDevice_SetGammaRamp(flags, const D3DGAMMARAMP *: 3 x 256 bytes)
    0x001717B0: {'edx': 16},                                 # SetVertexShaderConstant1(ecx reg, edx const float[4])
    0x001718F0: {'edx': lambda a: 4 * a[0]},                 # SetVertexShaderConstant(ecx reg, edx data, dwords)
}
_abi_cache = None

# Kernel calls (recorded as externs at 0xFE000000 + 4 * import slot): the
# arguments are checked like an extern's (the count from symbols/kernel_abi.json,
# the bytes each ordinal pops), and the input structures they point to, by
# ordinal: [(label, address, size)] read from the native run's memory at the
# call. Output-only pointers (status blocks, handles, read buffers) are left
# out: their bytes before the call are not an input. The oracle compares the
# same addresses (argument values are compared first, so a different pointer
# already fails).
_KABI = None
_KORD = None


def _kslots():
    global _KABI, _KORD
    if _KABI is None:
        _KABI = json.load(open(os.path.join(T, '..', 'symbols', 'kernel_abi.json')))
        info = json.load(open(os.path.join(T, '..', 'xbe_info.json')))
        xbe = open(os.path.join(T, '..', 'disc', 'default.xbe'), 'rb').read()
        kt = info['hdr']['kernel_thunk']
        sec = [s for s in info['sections'] if s['va'] <= kt < s['va'] + s['vsize']][0]
        o = sec['raw'] + kt - sec['va']; _KORD = []
        while True:
            v = struct.unpack_from('<I', xbe, o + 4 * len(_KORD))[0]
            if v == 0:
                break
            _KORD.append(v & 0x7FFFFFFF)
    return _KABI, _KORD


def _ansi(rd, p, label):
    """A counted ANSI string (STRING: u16 Length, u16 MaximumLength, char *Buffer) and its Length bytes."""
    if not p:
        return []
    out = [(label, p, 8)]
    ln, buf = struct.unpack('<H2xI', rd(p, 8))
    if buf and ln:
        out.append((label + '.Buffer', buf, ln))
    return out


def _oattr(rd, p, label):
    """Xbox OBJECT_ATTRIBUTES (RootDirectory, STRING *ObjectName, Attributes: 12 bytes) and its name."""
    if not p:
        return []
    return [(label, p, 12)] + _ansi(rd, struct.unpack('<I', rd(p + 4, 4))[0], label + '.ObjectName')


def _opt(p, n, label):
    return [(label, p, n)] if p else []


def _cstr(rd, p, label):
    if not p:
        return []
    s = rd(p, 512); n = s.find(b'\0')
    return [(label, p, (n if n >= 0 else 511) + 1)]


KERNEL_PTRS = {
    'NtCreateFile': lambda a, rd: _oattr(rd, a[2], 'ObjectAttributes') + _opt(a[4], 8, 'AllocationSize'),
    'NtOpenFile': lambda a, rd: _oattr(rd, a[2], 'ObjectAttributes'),
    'NtDeleteFile': lambda a, rd: _oattr(rd, a[0], 'ObjectAttributes'),
    'NtQueryFullAttributesFile': lambda a, rd: _oattr(rd, a[0], 'ObjectAttributes'),
    'NtCreateDirectoryObject': lambda a, rd: _oattr(rd, a[1], 'ObjectAttributes'),
    'NtOpenSymbolicLinkObject': lambda a, rd: _oattr(rd, a[1], 'ObjectAttributes'),
    'IoCreateSymbolicLink': lambda a, rd: _ansi(rd, a[0], 'SymbolicLinkName') + _ansi(rd, a[1], 'DeviceName'),
    'IoDeleteSymbolicLink': lambda a, rd: _ansi(rd, a[0], 'SymbolicLinkName'),
    'NtReadFile': lambda a, rd: _opt(a[7], 8, 'ByteOffset'),
    'NtWriteFile': lambda a, rd: _opt(a[5], a[6], 'Buffer') + _opt(a[7], 8, 'ByteOffset'),
    'NtSetInformationFile': lambda a, rd: _opt(a[2], a[3], 'FileInformation'),
    'NtQueryDirectoryFile': lambda a, rd: _ansi(rd, a[8], 'FileName'),
    'NtWaitForSingleObject': lambda a, rd: _opt(a[2], 8, 'Timeout'),
    'NtWaitForSingleObjectEx': lambda a, rd: _opt(a[3], 8, 'Timeout'),
    'KeDelayExecutionThread': lambda a, rd: _opt(a[2], 8, 'Interval'),
    'RtlInitAnsiString': lambda a, rd: _cstr(rd, a[1], 'SourceString'),
}


# Outputs a kernel call writes, by ordinal: [(address, size)] from the call's
# arguments (and, for reads, the byte count the status block reports). The
# native run records only the bytes a call changed, so an output equal to what
# its slot already held was not replayed and the oracle kept its own (stale
# stack) bytes there; these are replayed in full.
def _iosb_info(rd, p):
    return struct.unpack('<I', rd(p + 4, 4))[0] if p else 0


KERNEL_OUTS = {
    'NtCreateFile': lambda a, rd: [(a[0], 4), (a[3], 8)],
    'NtOpenFile': lambda a, rd: [(a[0], 4), (a[3], 8)],
    'NtReadFile': lambda a, rd: [(a[4], 8), (a[5], min(a[6], _iosb_info(rd, a[4])))],
    'NtWriteFile': lambda a, rd: [(a[4], 8)],
    'NtQueryInformationFile': lambda a, rd: [(a[1], 8), (a[2], a[3])],
    'NtSetInformationFile': lambda a, rd: [(a[1], 8)],
    'NtQueryVolumeInformationFile': lambda a, rd: [(a[1], 8), (a[2], a[3])],
    'NtQueryDirectoryFile': lambda a, rd: [(a[4], 8), (a[5], min(a[6], _iosb_info(rd, a[4])))],
    'NtQueryFullAttributesFile': lambda a, rd: [(a[1], 0x38)],
    'KeQuerySystemTime': lambda a, rd: [(a[0], 8)],
    'RtlInitAnsiString': lambda a, rd: [(a[0], 8)],
}


def kernel_outputs(va, args, img):
    """[addr, hex] of the outputs a kernel call wrote (from the image after it returned)."""
    kabi, kord = _kslots()
    slot = (va - 0xFE000000) // 4
    ordn = kord[slot] if slot < len(kord) else -1
    name = kabi.get(str(ordn), [0, ''])[1]
    f = KERNEL_OUTS.get(name)
    if not f or len(args) < kabi.get(str(ordn), [0])[0] // 4:
        return []
    rd = lambda p, n: bytes(img[p:p + n]) if 0 < p and p + n <= len(img) else b'\0' * n
    out = []
    for p, n in f(args, rd):
        if p and 0 < n <= 0x100000 and p + n <= len(img):
            out.append([p, bytes(img[p:p + n]).hex()])
    return out


def kernel_inputs(va, hdr, img):
    """extern_inputs for a kernel call (va 0xFE000000 + 4 * slot)."""
    kabi, kord = _kslots()
    slot = (va - 0xFE000000) // 4
    ordn = kord[slot] if slot < len(kord) else -1
    pops, name = kabi.get(str(ordn), [0, 'ordinal%d' % ordn])
    esp = hdr[5]
    args = [struct.unpack_from('<I', img, esp + 4 + 4 * i)[0] for i in range(pops // 4)]
    rd = lambda p, n: bytes(img[p:p + n]) if 0 < p and p + n <= len(img) else b'\0' * n
    mem = []
    f = KERNEL_PTRS.get(name)
    if f:
        for label, p, n in f(args, rd):
            if p and 0 < n <= 0x100000 and p + n <= len(img):
                mem.append([label, p, bytes(img[p:p + n]).hex()])
    return {'args': args, 'regs': {}, 'mem': mem, 'name': name}


def extern_inputs(va, hdr, img):
    """The inputs of an extern call at its entry (native run: esp at the return address)."""
    global _abi_cache
    if va >= 0xFE000000:
        return kernel_inputs(va, hdr, img)
    if _abi_cache is None:
        _abi_cache = json.load(open(os.path.join(T, '..', 'symbols', 'abi.json')))
    a = _abi_cache.get('0x%08X' % va, {})
    esp = hdr[5]
    args = [struct.unpack_from('<I', img, esp + 4 + 4 * i)[0] for i in range(a.get('pops', 0) // 4)]
    regs = {r: hdr[1 + NAMES.index(r)] for r in a.get('regs', []) if r in NAMES}
    mem = []
    for i, size in EXTERN_PTRS.get(va, {}).items():        # i: argument index, or a register name
        if isinstance(i, str) or i < len(args):
            n = size(args) if callable(size) else size
            p = hdr[1 + NAMES.index(i)] if isinstance(i, str) else args[i]
            if p and 0 < n <= 0x10000 and p + n <= len(img):
                mem.append([i, bytes(img[p:p + n]).hex()])
    return {'args': args, 'regs': regs, 'mem': mem}


# Functions that take their argument in st(0) (the _CI maths entries, _ftol): a natural
# call's oracle run gets the native st(0) pushed (snap_replay.py --fp-in), as probes with
# st0= do. Without it the original ran on whatever its x87 stack held (an acos natural call
# showed the original computing acos(0)).
FP_IN = {0x0014EFA4, 0x0014F01C, 0x00151EDC, 0x0014F7E4, 0x00151FB2, 0x00151FBC}
FP_IN2 = {0x0014F7E4, 0x00151FB2}                               # _CIpow: st(1) ** st(0)
for _x in os.environ.get('VERIFY_FP_IN', '').split(','):
    if _x.strip():
        FP_IN.add(int(_x, 16))


def replay(va, before, probe=False, stop_at=None, fp_in=False):
    """Run the original from the entry snapshot until it returns (or reaches
    stop_at). Returns the replay's text output; end memory is before + '.after'."""
    h, hs = header(before)
    with open(before, 'rb') as f:
        f.seek(hs + h[5]); ret_addr = struct.unpack('<I', f.read(4))[0]
    if probe:
        ret_addr = 0xDEC0DE00
    if stop_at:
        ret_addr = stop_at
    cmd = [sys.executable, os.path.join(T, 'snap_replay.py'), before, '%X' % ret_addr, '%X' % va]
    if fp_in == 2 or (not probe and va in FP_IN2):
        cmd.append('--fp-in2')                      # two x87 arguments (st(1), st(0)) from the .fp sidecar
    elif fp_in or (not probe and va in FP_IN):
        cmd.append('--fp-in')                       # the function takes st(0): the native one is pushed
    if os.path.exists(before + '.ext.json'):
        cmd.append('--externs=' + before + '.ext.json')
    import bblocks                                  # which instructions of the function ran (path coverage)
    cmd.append('--trace-range=%X:%X' % (va, max(bblocks.end(va), va + _fsize(va))))
    cmd += os.environ.get('AVPE_SNAP_EXTRA', '').split()    # debugging: e.g. --last (tools/probeone.py)
    return subprocess.run(cmd, capture_output=True, text=True).stdout


VP = '/home/claude/avpe-decomp/verified_paths.json'


def record_paths(va, how, status, rep, bad=None):
    """verified_paths.json: for every check run, the original's basic-block
    starts (bblocks.py) its oracle replay executed inside the function.
    {va: {'blocks': total, 'checks': {how: {'status', 'hit', 'date'}}}}"""
    import bblocks
    line = next((l for l in rep.splitlines() if l.startswith('range-trace: ')), None)
    if line is None:
        return
    ex = [int(x, 16) for x in line[13:].split(',') if x]
    hit = bblocks.hit(va, ex)
    vp = json.load(open(VP)) if os.path.exists(VP) else {}
    e = vp.setdefault('0x%08X' % va, {'checks': {}})
    e['blocks'] = len(bblocks.blocks(va))
    e['checks'][how] = {'status': status, 'hit': ['%X' % b for b in hit], 'date': time.strftime('%Y-%m-%d')}
    if bad:
        e['checks'][how]['diff'] = bad[:6]
    tmp = VP + '.tmp'
    json.dump(vp, open(tmp, 'w'), separators=(',', ':'))
    os.replace(tmp, VP)


_fsizes = {}


def _fsize(va):
    if not _fsizes:
        for f in json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json')):
            _fsizes[int(f['start'], 16)] = f['size']
    return _fsizes.get(va, 1)


_tail_cache = {}
_starts = []
_targets = None


def _direct_targets():
    """Every address a call rel32 in .text goes to."""
    global _targets
    if _targets is None:
        import struct
        R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
        info = json.load(open(os.path.join(R, 'xbe_info.json')))
        d = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
        t = [x for x in info['sections'] if x['name'] == '.text'][0]
        code = d[t['raw']:t['raw'] + t['vsize']]
        _targets = set()
        for o in range(len(code) - 5):
            if code[o] == 0xE8:
                _targets.add((t['va'] + o + 5 + struct.unpack_from('<i', code, o + 1)[0]) & 0xFFFFFFFF)
    return _targets


def tail_result(va):
    """True when the original function ends 'call X; pop..; ret': whatever X
    returned in eax is this function's result, whether or not Zono's
    declaration says so (GMenuItem::SetText returns ReformatText's value and
    the bestiary relies on it). A decompiled entry that returns nothing is
    then checked on eax anyway."""
    if va in _tail_cache:
        return _tail_cache[va]
    if va >= 0x161000 and va not in _direct_targets():
        _tail_cache[va] = False                             # a static initializer: the CRT ignores eax
        return False
    import capstone
    R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
    info = json.load(open(os.path.join(R, 'xbe_info.json')))
    d = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
    sec = [s for s in info['sections'] if s['va'] <= va < s['va'] + s['vsize']][0]
    o = sec['raw'] + va - sec['va']
    global _starts
    if not _starts:
        _starts = sorted(int(k, 16) for k in json.load(open(os.path.join(R, 'symbols', 'abi.json'))))
    import bisect
    i = bisect.bisect_right(_starts, va)
    end = _starts[i] if i < len(_starts) else va + 0x2000    # the function ends where the next begins
    md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
    prev, result = None, False
    for ins in md.disasm(d[o:o + min(end - va, 0x4000)], va):
        m = ins.mnemonic
        if m == 'call':
            prev = 'call'
            continue
        if m == 'pop' or (m == 'add' and ins.op_str.startswith('esp')):
            continue
        if m == 'ret':
            if prev == 'call':
                result = True
                break
            prev = None
            continue
        if m in ('int3',):
            break
        prev = None
    _tail_cache[va] = result
    return result


def compare(va, before, after, rep, stop_at=None, run_log=None):
    """-> (bad, notes): differences that fail the check, and informational ones."""
    hb, _ = header(before)
    hl, hsl = header(after)
    bad, notes = [], []
    oe = [int(x.split('=')[1], 16) for x in rep.strip().splitlines()[-1].split()]   # eax, esp from replay
    esp = hl[5]
    if oe[1] != esp:
        bad.append('esp: original %08X decompiled %08X' % (oe[1], esp))
    if not stop_at and oe[0] != hl[1]:
        kind = returns(va)
        msg = 'eax: original %08X decompiled %08X' % (oe[0], hl[1])
        if kind is None and tail_result(va):
            bad.append(msg + ' (the original returns through a call, so its eax is that call\'s result and'
                       ' callers may use it: the entry must return it too, e.g. GMenuItem::SetText)')
        elif kind == 'eax' or (kind == 'al' and (oe[0] & 0xFF) != (hl[1] & 0xFF)):
            bad.append(msg)
        else:
            notes.append(msg + ' (not compared: the function returns %s)' % ('al' if kind == 'al' else 'nothing'))
    # x87: how many values the function left on the register stack, and st(0)
    m = re.search(r'^fp pushed=(\d+) st0=(\S+)', rep, re.M)
    if m and not stop_at and hb[13] & 0x100 and hl[13] & 0x100:
        opushed, ost0 = int(m.group(1)), m.group(2)
        lpushed = ((hb[13] & 7) - (hl[13] & 7)) & 7
        lst0 = struct.pack('<II', hl[14], hl[15]).hex()
        if opushed != lpushed:
            bad.append('x87 stack: original left %d value(s), decompiled %d' % (opushed, lpushed))
        elif (opushed or (va in FP_IN and ost0 != '-')) and ost0 != lst0 and not all(math.isnan(struct.unpack('<d', bytes.fromhex(x))[0]) for x in (ost0, lst0)):
            # both NaN: the payload bits differ between x87 and the host, nothing in the game can see them
            bad.append('st0: original %r decompiled %r' % (struct.unpack('<d', bytes.fromhex(ost0))[0],
                                                           struct.unpack('<d', bytes.fromhex(lst0))[0]))
    if stop_at:
        abi = json.load(open(os.path.join(T, '..', 'symbols', 'abi.json')))
        callee_in = set(abi.get('0x%08X' % stop_at, {}).get('regs', []))
        notes.append('callee %08X reads registers: %s' % (stop_at, ', '.join(sorted(callee_in)) or 'none'))
        for line in rep.splitlines():
            if line.startswith('regs '):
                for kv in line[5:].split():
                    k, v = kv.split('='); v = int(v, 16); j = NAMES.index(k)
                    if k in callee_in and v != hl[1 + j]:
                        bad.append('%s: original %08X decompiled %08X' % (k, v, hl[1 + j]))
    O = np.fromfile(before + '.after', dtype='<u4')
    L = np.fromfile(after, dtype='<u4', offset=hsl)
    n = min(len(O), len(L))
    idx = np.nonzero(O[:n] != L[:n])[0] * 4
    entry_esp = hb[5]
    nargs = hl[11] if stop_at else 0
    vol = volatile_ranges(run_log)
    for off in idx.tolist():
        if esp - 0x10000 <= off < esp:
            if os.environ.get('VERIFY_DEADSTACK') and esp - int(os.environ['VERIFY_DEADSTACK'], 0) <= off:
                pass                                  # dead stack compared on request
            else:
                continue                              # dead stack below esp
        if stop_at and (off == esp or esp + 4 + 4 * nargs <= off < entry_esp):
            continue                                  # return slot / own frame at a call boundary
        if any(a <= off < b for a, b in vol):
            continue
        bad.append('mem %08X: original %08X decompiled %08X' % (off, O[off // 4], L[off // 4]))
        if len(bad) > 40:
            break
    return bad, notes


def instructions(rep):
    stopped = next((l for l in rep.splitlines() if l.startswith('stopped:')), '')
    return stopped.split(' after ')[1].split(' instructions')[0] if ' after ' in stopped else '?'


def record(va, how, rep):
    """Add a passing check to verified.json (one entry per function and check)."""
    vj = json.load(open(VJ)) if os.path.exists(VJ) else {}
    lst = vj.setdefault('0x%08X' % va, [])
    if not any(e['check'] == how for e in lst):
        lst.append({'check': how, 'original_instructions': instructions(rep), 'date': time.strftime('%Y-%m-%d')})
    tmp = VJ + '.tmp'
    json.dump(vj, open(tmp, 'w'), indent=1)
    os.replace(tmp, VJ)
