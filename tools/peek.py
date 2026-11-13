#!/usr/bin/env python3
"""Read guest memory of the running native build (avpe_recomp) through
/proc/<pid>/mem.  peek.py <va> [len] [--dwords] [--str]"""
import os, re, struct, subprocess, sys
def _pid():
    for name in ('avpe_recomp', 'avpe_recomp_cov'):
        try:
            return int(subprocess.check_output(['pgrep', '-x', name]).split()[0])
        except subprocess.CalledProcessError:
            pass
    raise SystemExit('native build not running')
pid = _pid()
exe = os.readlink('/proc/%d/exe' % pid).replace(' (deleted)', '')
sym = [l.split() for l in subprocess.check_output(['nm', exe]).decode().splitlines() if l.endswith(' g_xbox_mem_offset')][0]
off_sym = int(sym[0], 16)
base = None
for l in open('/proc/%d/maps' % pid):
    f = l.split()
    if len(f) < 6: continue
    a, perm, offs, p = f[0], f[1], f[2], f[5:]
    if p and p[0].replace(' (deleted)', '') == exe and int(offs, 16) == 0:
        base = int(a.split('-')[0], 16); break
mem = open('/proc/%d/mem' % pid, 'rb', 0)
def host(a, n):
    mem.seek(a); return mem.read(n)
gofs = struct.unpack('<Q', host(base + off_sym, 8))[0]
def rd(va, n):
    return host(gofs + va, n)
if __name__ == '__main__':
    va = int(sys.argv[1], 16); n = int(sys.argv[2], 0) if len(sys.argv) > 2 and not sys.argv[2].startswith('--') else 64
    d = rd(va, n)
    if '--str' in sys.argv:
        print(d.split(b'\0')[0].decode('latin1'))
    elif '--dwords' in sys.argv:
        for i in range(0, n, 16):
            print('%08X  %s' % (va + i, ' '.join('%08X' % w for w in struct.unpack_from('<%dI' % (min(16, n - i) // 4), d, i))))
    else:
        for i in range(0, n, 16):
            c = d[i:i + 16]
            print('%08X  %-48s %s' % (va + i, c.hex(' '), ''.join(chr(b) if 32 <= b < 127 else '.' for b in c)))
