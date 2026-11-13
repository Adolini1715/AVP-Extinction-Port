#!/usr/bin/env python3
"""Zono's CBWJCompressor format (DATX/TEMX/PLAX chunks), reimplemented from the
Xbox CompressCore at 0x001331B0 and CalcConstants at 0x001330E0.

Stream of 16-bit little-endian words.
  control word: 16 flags, consumed from bit 15 down
    flag 0 -> next word is a literal, copied to output
    flag 1 -> next word is a token: length = tok & mask, distance = tok >> bits
              (both in 16-bit words); tok == 0 ends the stream
  bits = count of leading zero bits of the 16-bit window size; mask = (1<<bits)-1
"""
import struct

def decompress(data, bits, limit=1 << 26):
    mask = (1 << bits) - 1
    w = struct.unpack(f'<{len(data) // 2}H', data[:len(data) // 2 * 2])
    out = []; i = 0
    while i < len(w):
        ctrl = w[i]; i += 1
        for b in range(15, -1, -1):
            if i >= len(w): return out_bytes(out), i * 2, False
            if not (ctrl >> b) & 1:
                out.append(w[i]); i += 1
            else:
                tok = w[i]; i += 1
                if tok == 0: return out_bytes(out), i * 2, True
                n, dist = tok & mask, tok >> bits
                if dist == 0 or dist > len(out) or n < 2: raise ValueError('bad token')
                s = len(out) - dist
                for k in range(n): out.append(out[s + k])
            if len(out) > limit: raise ValueError('too long')
    return out_bytes(out), i * 2, False

def out_bytes(words): return struct.pack(f'<{len(words)}H', *words)

def compress_zono(raw, window):
    """Port of CompressCore (0x001331B0): greedy, earliest longest match within
    `window` words, match length capped by the mask, by the distance (no overlap)
    and by the end of input; matches shorter than 2 words become literals."""
    bits = 0
    while not (window << bits) & 0x8000: bits += 1
    maxlen = (1 << bits) - 1
    if len(raw) % 2: raw += b'\0'
    w = struct.unpack(f'<{len(raw) // 2}H', raw); n = len(w)
    out = []; ctrl_at = None; mask = 0; pos = 0
    def flag():
        nonlocal ctrl_at, mask
        mask >>= 1
        if mask == 0:
            ctrl_at = len(out); out.append(0); mask = 0x8000
    while True:
        flag()
        if pos >= n:
            out[ctrl_at] |= mask; out.append(0); break
        best, bpos = 0, 0
        for c in range(max(0, pos - window), pos):
            if w[c] != w[pos]: continue
            k = 0
            while c + k < pos and pos + k < n and w[c + k] == w[pos + k]: k += 1
            if k > best:
                best, bpos = k, c
                if best >= maxlen: best = maxlen; break
        if best >= 2:
            out[ctrl_at] |= mask
            out.append(((pos - bpos) << bits) | best); pos += best
        else:
            out.append(w[pos]); pos += 1
    return struct.pack(f'<{len(out)}H', *out)

def compress_optimal(raw, window):
    """Minimum-token parse (dynamic programming). Produces a stream the same
    decoder accepts; used when a patched chunk must fit its original size."""
    bits = 0
    while not (window << bits) & 0x8000: bits += 1
    maxlen = (1 << bits) - 1
    if len(raw) % 2: raw += b'\0'
    w = struct.unpack(f'<{len(raw) // 2}H', raw); n = len(w)
    longest = [(0, 0)] * n
    for pos in range(n):
        best, bpos = 0, 0
        for c in range(max(0, pos - window), pos):
            if w[c] != w[pos]: continue
            k = 0
            while c + k < pos and pos + k < n and k < maxlen and w[c + k] == w[pos + k]: k += 1
            if k > best: best, bpos = k, c
        longest[pos] = (best, bpos)
    INF = 1 << 30; cost = [INF] * (n + 1); cost[n] = 0; choice = [None] * n
    for pos in range(n - 1, -1, -1):
        cost[pos] = cost[pos + 1] + 1; choice[pos] = 1
        k, _ = longest[pos]
        for L in range(2, k + 1):
            if cost[pos + L] + 1 < cost[pos]: cost[pos] = cost[pos + L] + 1; choice[pos] = L
    out = []; ctrl_at = None; mask = 0; pos = 0
    def flag():
        nonlocal ctrl_at, mask
        mask >>= 1
        if mask == 0: ctrl_at = len(out); out.append(0); mask = 0x8000
    while pos < n:
        flag(); L = choice[pos]
        if L == 1: out.append(w[pos]); pos += 1
        else:
            out[ctrl_at] |= mask; out.append(((pos - longest[pos][1]) << bits) | L); pos += L
    flag(); out[ctrl_at] |= mask; out.append(0)
    return struct.pack(f'<{len(out)}H', *out)

def compress(raw, bits):
    """Literal-only encoding: always valid, never smaller. Used for patching."""
    if len(raw) % 2: raw += b'\0'
    w = struct.unpack(f'<{len(raw) // 2}H', raw); out = []
    for i in range(0, len(w), 16):
        grp = w[i:i + 16]; out.append(0); out.extend(grp)
    # terminator: a control word whose first flag is 1, then a zero token
    out.append(0x8000); out.append(0)
    return struct.pack(f'<{len(out)}H', *out)
