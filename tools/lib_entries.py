#!/usr/bin/env python3
"""lib_entries.py <section> [game_end]: library entry points (D3D, XGRPH, DSOUND, XMV, XPP, WMADEC) called from game code and from other libraries."""
import json,struct,csv,collections,sys
R='/home/claude/avpe'
info=json.load(open(R+'/xbe_info.json')); d=open(R+'/disc/default.xbe','rb').read()
secs=[(s['name'].strip(),s['va'],s['vsize'],s['raw']) for s in info['sections']]
names={}
for r in csv.reader(open(R+'/symbols/xbox_symbols.csv')):
    try: names[int(r[0],16)]=r[1]
    except: pass
def sec(va):
    for n,a,sz,_ in secs:
        if a<=va<a+sz: return n
abi=json.load(open(R+'/symbols/abi.json'))
funcs={int(k,16) for k in abi}
calls=collections.defaultdict(set)
for n,a,sz,raw in secs:
    if n not in ('.text','D3D','XGRPH','DSOUND','WMADEC','XMV','XPP'): continue
    code=d[raw:raw+sz]
    for i in range(len(code)-5):
        if code[i] in (0xE8,0xE9):
            tgt=a+i+5+struct.unpack_from('<i',code,i+1)[0]
            if tgt in funcs: calls[tgt].add(a+i)
GAME_END=int(sys.argv[2],16) if len(sys.argv)>2 else 0x141000
want=sys.argv[1]
out=[]
for tgt,srcs in calls.items():
    if sec(tgt)!=want: continue
    g=[s for s in srcs if s<GAME_END]
    o=[s for s in srcs if s>=GAME_END and sec(s)!=want]
    if g or o: out.append((tgt,len(g),len(o)))
out.sort()
print(want,'entries called from game:',sum(1 for t,g,o in out if g),' from other libs only:',sum(1 for t,g,o in out if not g))
for t,g,o in out: print('%08X game=%d otherlib=%d %s'%(t,g,o,names.get(t,'')))
