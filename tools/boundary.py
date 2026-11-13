#!/usr/bin/env python3
"""boundary.py [--json OUT]: the library entry points game code calls directly.

Game = functions source_map.csv assigns to a game file; library = the rest
(the Microsoft sections D3D, DSOUND, XGRPH, XPP, XMV, WMADEC, DOLBY and the
779 XAPI/C runtime functions in .text, source_map file "runtime"). Prints,
per library entry point that some game function calls directly, its name,
section and number of game callers, busiest first. This is the surface the
platform layer has to replace: everything else in the libraries is only
reached through it.
"""
import collections, csv, json, os, sys
R = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
f = json.load(open('/home/claude/xboxrecomp/tools/disasm/output/functions.json'))
by = {int(x['start'], 16): x for x in f}
sm = {int(r['xbox'], 16): r for r in csv.DictReader(open(os.path.join(R, 'symbols', 'source_map.csv')))}
game = {a for a in by if a in sm and sm[a]['file'] != 'runtime'}
lib = {a for a in by if a not in game}
edges = collections.defaultdict(set)
for a in game:
    for c in by[a]['calls_to']:
        c = int(c, 16)
        if c in lib:
            edges[c].add(a)
sec = lambda c: 'runtime' if by[c]['section'] == '.text' else by[c]['section']
print('game functions %d, library %d; entry points called by game code: %d' % (len(game), len(lib), len(edges)))
print(' '.join('%s %d' % kv for kv in collections.Counter(sec(c) for c in edges).most_common()))
for c in sorted(edges, key=lambda c: (sec(c), -len(edges[c]))):
    print('%-8s %06X %-48s %4d' % (sec(c), c, by[c]['name'][:48], len(edges[c])))
if '--json' in sys.argv:
    json.dump({'%06X' % c: {'name': by[c]['name'], 'section': sec(c), 'callers': sorted('%06X' % g for g in edges[c])}
               for c in edges}, open(sys.argv[sys.argv.index('--json') + 1], 'w'), indent=0)
