import struct, sys, zlib, re
def h(s): return zlib.crc32(s.encode())^0xffffffff
# class-name dictionary from XBE strings
names={}
for line in open('strings.txt',errors='ignore'):
    for w in re.findall(r'[A-Za-z_][A-Za-z0-9_]{2,}',line):
        for v in (w, w.upper(), w.lower()):
            names[h(v)]=w
def walk(d, o, end, depth, out):
    while o+8<=end:
        cid=d[o:o+4].decode('latin1'); sz=struct.unpack_from('<I',d,o+4)[0]
        if cid in ('RIFF','LIST'):
            form=d[o+8:o+12].decode('latin1')
            out.append(('  '*depth)+f'{cid} {form} size {sz}')
            walk(d,o+12,o+8+sz,depth+1,out)
        else:
            out.append(('  '*depth)+f'{cid} size {sz}')
            if cid=='TYPE':
                n=struct.unpack_from('<I',d,o+8)[0]
                ents=[struct.unpack_from('<II',d,o+12+i*8) for i in range(n)]
                for th,offs in ents[:12]:
                    out.append(('  '*(depth+1))+f'type {th:08X} {names.get(th,"?"):30s} @0x{offs:08X}')
                known=sum(1 for th,_ in ents if th in names)
                out.append(('  '*(depth+1))+f'... {n} entries, {known} named')
        o+=8+sz+(sz&1)
f=sys.argv[1]; d=open(f,'rb').read(); out=[]
walk(d,0,len(d),0,out); print('\n'.join(out[:int(sys.argv[2]) if len(sys.argv)>2 else 60]))
