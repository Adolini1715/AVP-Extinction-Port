#!/usr/bin/env python3
"""Dump XBE header, certificate, sections, library versions, kernel imports."""
import struct, sys, json

XOR_ENTRY_RETAIL = 0xA8FC57AB
XOR_ENTRY_DEBUG = 0x94859D4B
XOR_KT_RETAIL = 0x5B6D40B6
XOR_KT_DEBUG = 0xEFB1F152

def u32(b, o): return struct.unpack_from('<I', b, o)[0]
def u16(b, o): return struct.unpack_from('<H', b, o)[0]

def main(path):
    d = open(path, 'rb').read()
    assert d[:4] == b'XBEH', 'not an XBE'
    base = u32(d, 0x104)
    def off(va): return va - base
    hdr = dict(
        base=base, size_headers=u32(d, 0x108), size_image=u32(d, 0x10C),
        size_image_header=u32(d, 0x110), timedate=u32(d, 0x114),
        cert_addr=u32(d, 0x118), num_sections=u32(d, 0x11C), sect_addr=u32(d, 0x120),
        init_flags=u32(d, 0x124), entry_enc=u32(d, 0x128), tls_addr=u32(d, 0x12C),
        stack_commit=u32(d, 0x130), heap_reserve=u32(d, 0x134), heap_commit=u32(d, 0x138),
        pe_base=u32(d, 0x13C), pe_size=u32(d, 0x140), pe_checksum=u32(d, 0x144),
        pe_timedate=u32(d, 0x148), debug_path=u32(d, 0x14C), debug_file=u32(d, 0x150),
        debug_ufile=u32(d, 0x154), kt_enc=u32(d, 0x158), nlib=u32(d, 0x160),
        lib_addr=u32(d, 0x164), kernel_lib=u32(d, 0x168), xapi_lib=u32(d, 0x16C),
    )
    import datetime
    print('Base 0x%08X  image size %d  headers %d' % (base, hdr['size_image'], hdr['size_headers']))
    print('XBE timestamp:', datetime.datetime.utcfromtimestamp(hdr['timedate']))
    print('PE timestamp :', datetime.datetime.utcfromtimestamp(hdr['pe_timedate']))
    for key, xr in (('retail', XOR_ENTRY_RETAIL), ('debug', XOR_ENTRY_DEBUG)):
        e = hdr['entry_enc'] ^ xr
        if base <= e < base + hdr['size_image']:
            print('Entry point 0x%08X (%s key)' % (e, key))
            kt = hdr['kt_enc'] ^ (XOR_KT_RETAIL if key == 'retail' else XOR_KT_DEBUG)
            hdr['kernel_thunk'] = kt; hdr['xbe_kind'] = key
    def cstr(va):
        o = off(va); e = d.index(b'\0', o); return d[o:e].decode('latin1')
    def wstr(va):
        o = off(va); s = []
        while True:
            c = u16(d, o)
            if c == 0: break
            s.append(chr(c)); o += 2
        return ''.join(s)
    if hdr['debug_path']:
        print('Debug path   :', cstr(hdr['debug_path']))
        print('Debug file   :', cstr(hdr['debug_file']))
    # certificate
    c = off(hdr['cert_addr'])
    title_id = u32(d, c + 8)
    title = d[c + 0xC:c + 0xC + 80].decode('utf-16le').split('\0')[0]
    print('Title ID 0x%08X (%s-%03d) "%s"' % (title_id, chr(title_id >> 24) + chr((title_id >> 16) & 0xff), title_id & 0xffff, title))
    print('Cert regions 0x%X  disc %d  version 0x%X' % (u32(d, c + 0xA0), u32(d, c + 0xA4), u32(d, c + 0xAC)))
    # sections
    print('\nSections:')
    so = off(hdr['sect_addr'])
    secs = []
    for i in range(hdr['num_sections']):
        s = so + i * 56
        flags, va, vsize, raw, rsize, name_addr = struct.unpack_from('<6I', d, s)
        name = cstr(name_addr)
        secs.append(dict(name=name, va=va, vsize=vsize, raw=raw, rsize=rsize, flags=flags))
        print('  %-10s va 0x%08X vsize %9d raw 0x%07X rsize %9d flags 0x%02X %s' % (
            name, va, vsize, raw, rsize, flags, 'X' if flags & 4 else ''))
    # libraries
    print('\nLibrary versions (%d):' % hdr['nlib'])
    lo = off(hdr['lib_addr'])
    libs = []
    for i in range(hdr['nlib']):
        e = lo + i * 16
        name = d[e:e + 8].rstrip(b'\0').decode('latin1')
        maj, mnr, bld, flg = struct.unpack_from('<4H', d, e + 8)
        qfe = flg & 0x1FFF; approved = (flg >> 13) & 3; dbg = (flg >> 15) & 1
        libs.append(dict(name=name, ver='%d.%d.%d' % (maj, mnr, bld), qfe=qfe, approved=approved, debug=dbg))
        print('  %-8s %d.%d.%d  qfe %d  approved %d  debug %d' % (name, maj, mnr, bld, qfe, approved, dbg))
    # kernel imports
    if 'kernel_thunk' in hdr:
        ko = None
        for s in secs:
            if s['va'] <= hdr['kernel_thunk'] < s['va'] + s['vsize']:
                ko = s['raw'] + hdr['kernel_thunk'] - s['va']
        ords = []
        while ko is not None:
            v = u32(d, ko)
            if v == 0: break
            ords.append(v & 0x7FFFFFFF); ko += 4
        print('\nKernel imports: %d ordinals' % len(ords))
        print('  ' + ' '.join(str(o) for o in ords))
    json.dump(dict(hdr=hdr, sections=secs, libs=libs), open(sys.argv[2] if len(sys.argv) > 2 else '/dev/null', 'w'), indent=1)

if __name__ == '__main__':
    main(sys.argv[1])
