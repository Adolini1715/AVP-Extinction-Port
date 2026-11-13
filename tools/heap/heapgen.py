# heap scenario generator: lines of ops -> probe file.  ops: a <slot> <units> [flags] | f <slot> | raw <text>
import sys
n=0; out=[]
def slot(k): return '@S+%X'%(0xE00+4*int(k,16))
for ln in open(sys.argv[1]):
    t=ln.split('#')[0].split()
    if not t: continue
    n+=1; tag='poke=@S+F00:%X'%n
    if t[0]=='a':
        units=int(t[2],16); size=units*16-16-(int(t[4],16) if len(t)>4 else 0); fl=t[3] if len(t)>3 else '0'
        out.append('va=145432 %s arg=*30BEB4 arg=%s arg=%X save=%s'%(tag,fl,size,slot(t[1])))
    elif t[0]=='f':
        out.append('va=145BAD %s arg=*30BEB4 arg=%s arg=[%s]+0'%(tag,t[2] if len(t)>2 else '0',slot(t[1])))
    elif t[0]=='raw':
        out.append(' '.join(t[1:]).replace('TAG',tag))
open(sys.argv[2],'w').write('\n'.join(out)+'\n'); print(len(out),'probes')
