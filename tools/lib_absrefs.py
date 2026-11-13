#!/usr/bin/env python3
"""lib_absrefs.py: absolute data references from game code into the Microsoft library sections (what the game reads or writes there directly)."""
# absolute data references from game code (0x11000..0x141000) into the library sections, by instruction operand
import json,struct,collections,capstone
R='/home/claude/avpe'
info=json.load(open(R+'/xbe_info.json')); d=open(R+'/disc/default.xbe','rb').read()
t=[s for s in info['sections'] if s['name'].strip()=='.text'][0]
abi=json.load(open(R+'/symbols/abi.json')); funcs=sorted(int(k,16) for k in abi)
md=capstone.Cs(capstone.CS_ARCH_X86,capstone.CS_MODE_32); md.detail=True
LO,HI=0x170D60,0x205000
refs=collections.defaultdict(lambda: collections.defaultdict(int))
for i,f in enumerate(funcs):
    if not (0x11000<=f<0x141000): continue
    end=funcs[i+1] if i+1<len(funcs) else f+16
    code=d[t['raw']+f-t['va']:t['raw']+end-t['va']]
    for ins in md.disasm(code,f):
        if ins.mnemonic in('call','jmp') : continue
        for op in ins.operands:
            v=None
            if op.type==capstone.x86.X86_OP_MEM and op.mem.base==0 and op.mem.index==0: v=op.mem.disp&0xffffffff; kind='mem'
            elif op.type==capstone.x86.X86_OP_MEM and LO<=(op.mem.disp&0xffffffff)<HI: v=op.mem.disp&0xffffffff; kind='idx'
            elif op.type==capstone.x86.X86_OP_IMM and LO<=op.imm<HI: v=op.imm; kind='imm'
            if v is not None and LO<=v<HI:
                w='w' if (kind!='imm' and op is ins.operands[0] and ins.mnemonic not in('cmp','test','push','fld','fild','fcomp','fcom','fmul','fadd','fsub','fdiv')) else 'r'
                refs[v][kind+w+':%X'%f]+=1
for v in sorted(refs): print('%08X'%v, dict(refs[v]))
