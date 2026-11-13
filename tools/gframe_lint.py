#!/usr/bin/env python3
"""Flag use of an outer GuestFrame's va()/at<> while a later (inner) GuestFrame is alive.
Usage: gframe_lint.py [src dir or files]"""
import re, sys, glob, os
FR = re.compile(r'\b(?:GuestFrame|CallFrame)\s+(\w+)\s*(?=[\(;{])')
USE = re.compile(r'\b(\w+)\s*\.\s*(va\s*\(|at\s*<)')
VAR = re.compile(r'\b(?:GuestFrame|CallFrame)\s*(?:&|\*)\s*(\w+)')
def strip(s):
    out=[];i=0;n=len(s)
    while i<n:
        c=s[i]
        if s.startswith('/*',i):
            j=s.find('*/',i+2); j=n if j<0 else j+2
            out.append(re.sub(r'[^\n]',' ',s[i:j])); i=j
        elif s.startswith('//',i):
            j=s.find('\n',i); j=n if j<0 else j
            out.append(' '*(j-i)); i=j
        elif c in '"\'':
            j=i+1
            while j<n and s[j]!=c:
                j+= 2 if s[j]=='\\' else 1
            out.append(re.sub(r'[^\n]',' ',s[i:j+1])); i=j+1
        else: out.append(c); i+=1
    return ''.join(out)
def scan(path):
    raw=open(path,errors='replace').read(); s=strip(raw)
    line=1; depth=0; stack=[]  # frames: (name,depth,line,idx)
    hits=[];hand=[]
    lines=raw.split('\n')
    def fva(ln):
        for k in range(ln-1,-1,-1):
            m=re.search(r'/\*\s*(0x[0-9A-Fa-f]{5,8})',lines[k])
            if m: return m.group(1)
        return '?'
    # tokenise by position
    pos=0;n=len(s);ln=1
    events=[]
    for m in re.finditer(r'[{}]|\bGuestFrame\b|\bCallFrame\b|\b\w+\s*\.\s*(?:va\s*\(|at\s*<)|\[[^\]\n]*\]\s*\(',s):
        events.append(m)
    cur=0;lastpos=0
    fid=0
    for m in events:
        ln+=s.count('\n',lastpos,m.start()); lastpos=m.start()
        t=m.group(0)
        if t=='{': depth+=1
        elif t=='}':
            depth-=1
            stack=[f for f in stack if f[1]<=depth]
            if depth<=0: stack=[]; depth=0
        elif t in('GuestFrame','CallFrame'):
            mm=FR.match(s,m.start())
            if mm:
                stack.append([mm.group(1),depth,ln,len(stack)])
            else:
                seg=s[m.start():m.start()+60]
                if VAR.match(seg): hand.append((ln,fva(ln),'frame ref/ptr param or var: '+lines[ln-1].strip()[:90]))
        elif t.startswith('['):
            # lambda: does it capture a frame?
            if stack and re.search(r'&|=',t) : hand.append((ln,fva(ln),'lambda while frames alive: '+lines[ln-1].strip()[:90]))
        else:
            name=re.match(r'\w+',t).group(0)
            idx=[i for i,f in enumerate(stack) if f[0]==name]
            if idx:
                i=idx[-1]
                if i<len(stack)-1:
                    inner=stack[i+1:]
                    hits.append((ln,fva(ln),name,[f[0]+'@'+str(f[2]) for f in inner],lines[ln-1].strip()[:110]))
    return hits,hand
if __name__=='__main__':
    args=sys.argv[1:] or ['/home/claude/avpe-decomp/src']
    files=[]
    for a in args: files+= sorted(glob.glob(a+'/*.cpp')) if os.path.isdir(a) else [a]
    th=0
    for f in files:
        h,hd=scan(f)
        for x in h: print('HIT %s:%d fn=%s %s used while %s alive | %s'%(os.path.basename(f),x[0],x[1],x[2],x[3],x[4])); th+=1
        for x in hd: print('HAND %s:%d fn=%s %s'%(os.path.basename(f),x[0],x[1],x[2]))
    print('total hits',th)
