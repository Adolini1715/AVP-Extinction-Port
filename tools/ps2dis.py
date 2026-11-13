#!/usr/bin/env python3
"""ps2dis.py <mangled name | 0xADDR 0xSIZE>: disassemble a function of the PS2
release (ps2/SLUS_201.47, symbols intact) with the names of the globals and
callees it references (lui/offset pairs, jal). The PS2 build shows the source
structure the Xbox compiler inlined or reordered, and names globals."""
import sys,struct,re,capstone
R='/home/claude/avpe/ps2/'
elf=open(R+'SLUS_201.47','rb').read()
syms={};byname={}
for l in open(R+'syms.txt'):
    p=l.split()
    if len(p)>=8 and re.match(r'[0-9a-f]{8}$',p[1]):
        a=int(p[1],16); syms.setdefault(a,p[7]); byname[p[7]]=(a,int(p[2],0))
# program headers
e_phoff=struct.unpack_from('<I',elf,0x1c)[0]; n=struct.unpack_from('<H',elf,0x2c)[0]
segs=[struct.unpack_from('<8I',elf,e_phoff+32*i) for i in range(n)]
def rd(va,sz):
    for s in segs:
        if s[2]<=va<s[2]+s[4]: return elf[s[1]+va-s[2]:s[1]+va-s[2]+sz]
t=sys.argv[1]
a,sz=(int(t,16),int(sys.argv[2],16)) if t.startswith('0x') else byname[t]
md=capstone.Cs(capstone.CS_ARCH_MIPS,capstone.CS_MODE_MIPS64+capstone.CS_MODE_LITTLE_ENDIAN)
md.skipdata=True
hi={}
for i in md.disasm(rd(a,sz),a):
    note=''
    m=re.match(r'\$(\w+), (0x[0-9a-f]+)$',i.op_str)
    if i.mnemonic=='lui' and m: hi[m.group(1)]=int(m.group(2),16)<<16
    m2=re.match(r'\$(\w+), (-?0x[0-9a-f]+|-?\d+)\(\$(\w+)\)$',i.op_str)
    if m2 and m2.group(3) in hi:
        v=(hi[m2.group(3)]+int(m2.group(2),0))&0xffffffff; note='  ; %08x %s'%(v,syms.get(v,''))
    m3=re.match(r'\$(\w+), \$(\w+), (-?0x[0-9a-f]+|-?\d+)$',i.op_str)
    if i.mnemonic in('addiu','daddiu') and m3 and m3.group(2) in hi:
        v=(hi[m3.group(2)]+int(m3.group(3),0))&0xffffffff; note='  ; %08x %s'%(v,syms.get(v,''))
    if i.mnemonic=='jal':
        v=int(i.op_str,16); note='  ; '+syms.get(v,'')
    print('%08x  %-8s %s%s'%(i.address,i.mnemonic,i.op_str,note))
