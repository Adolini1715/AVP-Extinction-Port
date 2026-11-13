import zlib, sys, os
targets = set(os.path.basename(p).split('.')[0].upper() for p in os.popen('find disc/tbd -name "*.tbx"').read().split())
names = ['master.tbd','background.tbd','cursors.tbd','logo.tbd','mainmenu.tbd','MainMenu.tbd','Intro_Movie.tbd',
 'MainMenuAlienEnd.tbd','MainMenuMarineEnd.tbd','MainMenuPredatorEnd.tbd','GamepadXBOX.tbd','Gamepad.tbd','GamepadPS2.tbd']
def variants(n):
    out=set()
    for v in (n, n.lower(), n.upper()):
        for w in (v, v.replace('.tbd',''), 'tbd/'+v, 'tbd\\'+v, '\\tbd\\'+v, 'D:\\tbd\\'+v):
            out.add(w)
    return out
def fnv1(s,b=False):
    h=0x811c9dc5
    for c in s:
        if b: h^=c; h=(h*0x01000193)&0xffffffff
        else: h=(h*0x01000193)&0xffffffff; h^=c
    return h
def djb(s):
    h=5381
    for c in s: h=((h*33)+c)&0xffffffff
    return h
def sdbm(s):
    h=0
    for c in s: h=(c+(h<<6)+(h<<16)-h)&0xffffffff
    return h
fns={'crc32':lambda s:zlib.crc32(s),'crc32inv':lambda s:zlib.crc32(s)^0xffffffff,'fnv1':fnv1,'fnv1a':lambda s:fnv1(s,True),'djb':djb,'sdbm':sdbm}
hits=0
for n in names:
    for v in variants(n):
        for fn,f in fns.items():
            h='%08X'%f(v.encode())
            if h in targets: print('HIT',fn,repr(v),h); hits+=1
print('targets',len(targets),'hits',hits)
