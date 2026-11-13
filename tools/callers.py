#!/usr/bin/env python3
"""callers.py <va>...: every call/jmp rel32 to these addresses, and absolute references (vtables, tables)."""
import sys,json,struct
R='/home/claude/avpe'
info=json.load(open(R+'/xbe_info.json')); d=open(R+'/disc/default.xbe','rb').read()
t=[s for s in info['sections'] if s['name'].strip()=='.text'][0]
base=t['va']; raw=t['raw']; n=t['vsize']; code=d[raw:raw+n]
want={int(a,16) for a in sys.argv[1:]}
for i in range(len(code)-5):
    if code[i] in (0xE8,0xE9):
        tgt=base+i+5+struct.unpack_from('<i',code,i+1)[0]
        if tgt in want: print('%08X -> %08X %s'%(base+i,tgt,'call' if code[i]==0xE8 else 'jmp'))
for s in want:
    p=struct.pack('<I',s); j=-1
    while True:
        j=d.find(p,j+1)
        if j<0: break
        print('ref to %08X at file %X'%(s,j))
