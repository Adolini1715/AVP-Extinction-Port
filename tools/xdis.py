#!/usr/bin/env python3
"""Disassemble original XBE code: xdis.py <va> <end_va|+len>. Maps any section."""
import sys, json, os, capstone
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
info = json.load(open(os.path.join(R, 'xbe_info.json'))); d = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
va = int(sys.argv[1], 16); a2 = sys.argv[2]
n = int(a2[1:], 16) if a2.startswith('+') else int(a2, 16) - va
s = [s for s in info['sections'] if s['va'] <= va < s['va'] + s['vsize']][0]
o = s['raw'] + va - s['va']
md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
for i in md.disasm(d[o:o + n], va): print('%08x  %-6s %s' % (i.address, i.mnemonic, i.op_str))
