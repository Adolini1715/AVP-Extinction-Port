import struct,sys,glob,os
fn=sorted(glob.glob('/home/claude/avpe-recomp/verify/multi/*_after.bin'),key=os.path.getmtime)[-1]
f=open(fn,'rb'); H=os.path.getsize(fn)-0x4000000
def r32(a): f.seek(H+a); return struct.unpack('<I',f.read(4))[0]
def r16(a): f.seek(H+a); return struct.unpack('<H',f.read(2))[0]
def r8(a): f.seek(H+a); return f.read(1)[0]
h=r32(0x30BEB4); print(os.path.basename(fn),'heap',hex(h),'free units',hex(r32(h+0x30)))
for i in range(128):
    head=h+0x180+8*i; p=r32(head); c=0; e=[]
    while p!=head and c<40: e.append('%X@%X%s'%(r16(p-8),p-8,'L' if r8(p-8+5)&0x10 else '')); p=r32(p); c+=1
    if c: print(' list',i,e)
for i in range(16):
    s=r32(h+0x60+4*i)
    if s: print(' seg',i,hex(s),'last',hex(r32(s+0x40)))
for k in range(0,0x60,4):
    v=r32(int(sys.argv[1],16)+0xE00+k) if len(sys.argv)>1 else 0
    if v: print('  slot %X = %X (units %X flags %X)'%(k//4,v,r16(v-16),r8(v-16+5)))
