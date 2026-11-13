#!/usr/bin/env python3
"""Assign every Xbox function to a source file, as close to Zono's layout as
the evidence allows. Output: symbols/source_map.csv

Evidence, strongest first:
  local    the PS2 symbol table lists each object file's local symbols
           together, ending with that file's __sinit_<File>.cpp, so a local
           FUNC before __sinit_X belongs to X (exact, but few).
  class    a method's class matches a known file name (GAvPPointer ->
           AvPPointer.cpp): longest known stem contained in the class name.
  filled   linkers lay a file's code out contiguously, so functions between
           two functions of the same file belong to it (PS2 addresses, then
           again on Xbox addresses for functions the PS2 names do not cover).
  inferred no recorded file name: the class gives one (GTaskAlien ->
           TaskAlien.cpp). Zono's real file name may differ.
  nearest  game-code function with no evidence: given the file of its nearest
           placed neighbour in Xbox address order (a guess, flagged).
  runtime  above the end of game code (0x141000): Microsoft C runtime and
           system libraries. Replaced by the PC platform layer, not decompiled.
           Game static initialisers placed up there go to their own files.

Known file names: the 156 __sinit_<File>.cpp symbols (files that had static
initialisers). Files without them left no name.
"""
import csv, re, os, collections

R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
SYMS = os.path.join(R, 'ps2', 'syms.txt')
DB = os.path.join(R, 'symbols', 'xbox_symbols.csv')
FUNCS = '/home/claude/xboxrecomp/tools/disasm/output/functions.json'
OUT = os.path.join(R, 'symbols', 'source_map.csv')

ent = []
for line in open(SYMS):
    p = line.split()
    if len(p) < 8 or not p[0].rstrip(':').isdigit():
        continue
    try:
        # weak (inline) symbols print "<processor specific>: 13" as binding
        ent.append((int(p[1], 16), int(p[2]), p[3], 'WEAK' if '<processor' in line else p[4], p[-1]))
    except ValueError:
        pass

# known files, in link order (address order of their __sinit functions)
known = sorted((a, n[8:]) for a, sz, t, b, n in ent if n.startswith('__sinit_') and t == 'FUNC')
files = [n for _, n in known]
stem = {f: f.rsplit('.', 1)[0] for f in files}

# local anchors: locals up to and including __sinit_X belong to X
anchor = {}
pending = []
for a, sz, t, b, n in ent:
    if b != 'LOCAL':
        continue
    if n.startswith('__sinit_'):
        for pa in pending:
            anchor[pa] = (n[8:], 'local')
        pending = []
    elif t == 'FUNC':
        pending.append(a)

def class_of(name):
    m = re.match(r'^(?:__\w+?|\w+?)__(\d+)(\w+)$', name)
    if m:
        return m.group(2)[:int(m.group(1))]
    return None

def file_for_class(c):
    k = c.lower()
    best = None
    for f in files:
        s = stem[f].lower()
        if len(s) >= 4 and s in k and (best is None or len(s) > len(stem[best])):
            best = f
    return best

ps2f = sorted((a, sz, n) for a, sz, t, b, n in ent if t == 'FUNC' and not n.startswith('__sinit_'))
label = {}
for a, sz, n in ps2f:
    if a in anchor:
        label[a] = anchor[a]
        continue
    c = class_of(n)
    if c:
        f = file_for_class(c)
        if f:
            exact = stem[f].lower() in (c.lower(), c[1:].lower())
            label[a] = (f, 'class' if exact else 'class-substr')

def fill(seq, label, how='filled', max_gap=None):
    """seq: address-ordered keys. Between two labels of the same file, fill.
    max_gap: only across that many unlabelled functions (inline methods are
    compiled into many files, so two far-apart members prove little)."""
    last_i, last_f = None, None
    for i, k in enumerate(seq):
        if k in label:
            f = label[k][0]
            if last_f == f and last_i is not None and (max_gap is None or i - last_i - 1 <= max_gap):
                for j in range(last_i + 1, i):
                    if seq[j] not in label:
                        label[seq[j]] = (f, how)
            last_i, last_f = i, f

seq = [a for a, _, _ in ps2f]
fill(seq, label)
# inferred file names from classes for the rest
name_of = {a: n for a, _, n in ps2f}
for a in seq:
    if a not in label:
        c = class_of(name_of[a])
        if c:
            label[a] = ((c[1:] if c[:1] in 'GC' and len(c) > 2 and c[1].isupper() else c) + '.cpp', 'inferred')
fill(seq, label)

# carry to Xbox
db = {int(r['xbox'], 16): r for r in csv.DictReader(open(DB))}
import json
funcs = sorted(int(f['start'], 16) for f in json.load(open(FUNCS)) if int(f['start'], 16) < 0x170D60)
funcs_set = set(funcs)
xl = {}
for x in funcs:
    r = db.get(x)
    # tier C (call-graph) names are guesses: not above the end of game code,
    # where they put runtime helpers (0x151FB2, the CRT's fmod) into game files
    if r and (r['tier'] in ('A', 'B') or (r['tier'] == 'C' and x < 0x141000)) and r.get('ps2'):
        pa = int(r['ps2'], 16)
        if pa in label:
            xl[x] = (label[pa][0], label[pa][1])
# Classes the PS2 build does not have (the Xbox platform layer: CXBOXInput,
# GXBOXInputDevice, ...): a function in the vtable(s) of exactly one class
# family member and nowhere else belongs to that class's file. The file is
# the one the class's other functions are in, else named after the class.
_vt = json.load(open(os.path.join(R, 'funcs.json')))['vtables']
_owner = {}
for _va, (_deco, _slots) in _vt.items():
    if not (_deco.startswith('.?AV') and _deco.endswith('@@')) or '@' in _deco[4:-2]:
        continue
    for _x in _slots:
        _owner.setdefault(int(_x, 16), set()).add(_deco[4:-2])
_cls_file = collections.defaultdict(collections.Counter)
for _x, (_f, _h) in xl.items():
    if len(_owner.get(_x, ())) == 1:                 # the class's own functions, not inherited ones
        (_c,) = _owner[_x]
        _cls_file[_c][_f] += 1
for _x, _cs in _owner.items():
    if _x in xl or _x not in funcs_set or len(_cs) != 1 or _x >= 0x141000:
        continue
    (_c,) = _cs
    if _cls_file[_c]:
        xl[_x] = (_cls_file[_c].most_common(1)[0][0], 'vtable')
    else:
        xl[_x] = ((_c[1:] if _c[:1] in 'GC' and len(_c) > 2 and _c[1].isupper() else _c) + '.cpp', 'vtable-inferred')
fill(funcs, xl, 'filled-xbox', max_gap=40)
GAME_END = 0x141000
names = {x: (db[x]['name'] if x in db and db[x]['tier'] in ('A', 'B', 'C') else '') for x in funcs}
for x in funcs:
    if x in xl:
        continue
    n = names[x]
    if x >= GAME_END:
        if n.startswith('__sinit_'):
            xl[x] = (n[8:], 'sinit')
        elif class_of(n) and db[x]['tier'] in ('A', 'B'):
            xl[x] = (xl.get(x, ('', ''))[0] or (class_of(n)[1:] + '.cpp'), 'inferred')
        else:
            xl[x] = ('runtime', 'runtime')
# Game static initialisers above GAME_END. The compiler emits one small
# function per dynamically initialised global (class registrations
# `g_X = RegisterClass("GPauseMenu", Create, ...)` via 0xD7180, CRC constants,
# ...) and lists them in the CRT initialiser table (.CRT$XCU, merged into
# .data), one object file's entries contiguous, in link order. They are game
# source (they become initialised globals in the decomp), so they take the file
# of their class (registrations) or of their table neighbours.
import json, struct
import capstone
_info = json.load(open(os.path.join(R, 'xbe_info.json')))
_xbe = open(os.path.join(R, 'disc', 'default.xbe'), 'rb').read()
def _sec(va):
    for s in _info['sections']:
        if s['va'] <= va < s['va'] + s['rsize']:
            return s
def _rd(va, n):
    s = _sec(va)
    return _xbe[s['raw'] + va - s['va']:s['raw'] + va - s['va'] + n] if s else b''
def _is_code(v):
    s = _sec(v)
    return bool(s) and s['name'] == '.text'
_data = [s for s in _info['sections'] if s['name'] == '.data'][0]
best, run, hits, start = (0, 0, 0), 0, 0, _data['va']   # the run with the most code pointers
for va in range(_data['va'], _data['va'] + _data['rsize'] - 3, 4):
    v = struct.unpack('<I', _rd(va, 4))[0]
    if v == 0 or (_is_code(v) and v >= GAME_END):
        run += 1; hits += v != 0
        if hits > best[2]:
            best = (start, run, hits)
    else:
        run, hits, start = 0, 0, va + 4
xc = [v for v in struct.unpack('<%dI' % best[1], _rd(best[0], 4 * best[1])) if v]
_md = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_32)
cls_file = collections.defaultdict(collections.Counter)
for x, (fl, how) in xl.items():
    c = class_of(names.get(x, ''))
    if c and how != 'runtime':
        cls_file[c][fl] += 1
def _reg_class(v):
    """Class registered by a RegisterClass(name, Create, flags, &entry) call
    (0xD7180): the class of the Create function pushed second-last. The name
    string is the editor's type name ("Pause"), not the C++ class."""
    pushes = []
    for i in _md.disasm(_rd(v, 96), v):
        if i.mnemonic == 'push' and i.op_str.startswith('0x'):
            pushes.append(int(i.op_str, 16))
        if i.mnemonic == 'call':
            if i.op_str == '0xd7180' and len(pushes) >= 2:
                c = class_of(names.get(pushes[-2], ''))
                if c:
                    return c
                s = _rd(pushes[-1], 64).split(b'\0')[0]
                return ('G' + s.decode('latin1')) if s.isascii() and s else None
            return None
        if i.mnemonic in ('ret', 'jmp'):
            return None
    return None
sinit_file = {}
for v in xc:
    if v in xl and xl[v][1] != 'runtime':
        sinit_file[v] = (xl[v][0], xl[v][1])
        continue
    c = _reg_class(v)
    if c and cls_file.get(c):
        sinit_file[v] = (cls_file[c].most_common(1)[0][0], 'sinit-class')
    elif c:
        sinit_file[v] = ((c[1:] if c[:1] in 'GC' and len(c) > 2 and c[1].isupper() else c) + '.cpp', 'sinit-inferred')
for i, v in enumerate(xc):
    if v in sinit_file:
        continue
    p = next((sinit_file[xc[j]][0] for j in range(i - 1, max(-1, i - 60), -1) if xc[j] in sinit_file), None)
    n = next((sinit_file[xc[j]][0] for j in range(i + 1, min(len(xc), i + 60)) if xc[j] in sinit_file), None)
    sinit_file[v] = (p or n or 'unsorted.cpp', 'sinit-filled' if p and p == n else 'sinit-nearest')
for v, fh in sinit_file.items():
    if v in funcs_set:
        xl[v] = fh
print('static initialiser table at 0x%08X: %d entries, %d placed' % (best[0], len(xc), sum(1 for v in sinit_file if v in funcs_set)))

# nearest placed neighbour for the remaining game-code functions
game = [x for x in funcs if x < GAME_END]
prev = None
for i, x in enumerate(game):
    if x in xl and xl[x][1] != 'nearest':
        prev = xl[x][0]
    elif x not in xl:
        nxt = next((xl[y][0] for y in game[i + 1:i + 200] if y in xl and xl[y][1] != 'nearest'), None)
        xl[x] = (prev or nxt or 'unsorted.cpp', 'nearest')

# Entry points the function finder made inside another function's body (a
# jump target taken for a function start, 'tail_jump_alias'): part of the
# enclosing function, not functions Zono wrote. They keep their file but are
# marked 'alias' and left out of game function counts.
_fe = sorted((int(f['start'], 16), int(f['end'], 16)) for f in json.load(open(FUNCS)))
alias_of, oe, os_ = {}, 0, None
for a, e in _fe:
    if a < oe:
        alias_of[a] = os_
    if e > oe:
        oe, os_ = e, a
for a, parent in alias_of.items():
    if a in xl and xl[a][1] != 'runtime':
        xl[a] = (xl.get(parent, xl[a])[0], 'alias')

counts = collections.Counter()
with open(OUT, 'w', newline='') as f:
    w = csv.writer(f)
    w.writerow(['xbox', 'file', 'how', 'name'])
    for x in funcs:
        r = db.get(x)
        nm = r['name'] if r and r['tier'] in ('A', 'B', 'C') else ''
        fl, how = xl.get(x, ('unsorted.cpp', 'nearest'))
        counts[how] += 1
        w.writerow(['0x%08X' % x, fl, how, nm])
nfiles = len(set(v[0] for v in xl.values() if v[1] != 'runtime'))
print('known file names:', len(files))
print('Xbox .text functions:', len(funcs), dict(counts))
print('game source files:', nfiles, '(with Zono names:', len(set(v[0] for v in xl.values()) & set(files)), ')')
