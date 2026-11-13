#!/usr/bin/env python3
"""monkeypad.py MISSION FRAMES [--seed N] [--no-invuln] [--no-win] [--cash N] [--v1]

A long randomised replay pad for coverage ("monkey") runs: reach code the short
scripted pads never do (building, upgrades, abilities, deaths, menus, mission end).

  1. close the briefing (as m01.pad);
  2. pause and enter the cheat code (R L R R L R L L R on the triggers, as
     recomp/cheat.sh), then Options > Cheats: Player Invulnerable (unless
     --no-invuln, so losing and death paths run), Clear Fog Of War, $10000
     (and again every --cash N polls, default 4000, so purchases succeed);
  3. play in action sequences on the default control scheme (notes/Prompts.md:
     A select, B action, RT command list, LT shift, Black special ability,
     White grouping, X go to base, Y go to event, d-pad groups, Back view):
       command:  select (A / box), hold RT, pick a direction, A
       produce:  X (base), A on what is under the pointer, RT, direction, A
       ability:  select, Black, aim, A / B
       assault:  box select, scroll far, B
       groups:   LT/White + d-pad, d-pad recall
       camera, events (Y), view (Back), idle stretches;
     --v1 keeps the first version's single random presses;
  4. near the end (unless --no-win): pause, Cheats, Cheat Win, so the mission
     end screens run.

Deterministic for a given mission, seed and options. Polls run at about one
per frame; times are in polls after the briefing went up.
"""
import random, sys, zlib

a = sys.argv[1:]
m, frames = a[0], int(a[1])
opt = lambda k, d=None: a[a.index(k) + 1] if k in a else d
seed = int(opt('--seed', zlib.crc32(m.encode())))
invuln, win, v1 = '--no-invuln' not in a, '--no-win' not in a, '--v1' in a
cash_every = int(opt('--cash', 4000))
r = random.Random(seed)
out = ['# monkey pad for %s, %d frames, seed %d, invuln %d, win %d, %s (tools/monkeypad.py)' % (
    m, frames, seed, invuln, win, 'v1' if v1 else 'v2')]
t = 60


def ev(spec, hold=5, gap=30):
    global t
    out.append('B+%d %s:%d' % (t, spec, hold))
    t += gap


def open_cheats():
    ev('start', 8, 120)
    for _ in range(2):
        ev('down', 6, 40)              # Resume Bestiary -> Options (the in-mission pause menu)
    ev('a', 6, 90)                     # Options, focus on Cheats


def pick(*moves):
    ev('a', 6, 90)
    for mv in moves:
        ev(mv, 6, 40)
    ev('a', 6, 90)


def close_menus():
    ev('b', 6, 70); ev('b', 6, 120)


ev('start', 5, 90)                     # close the briefing
ev('a', 5, 240)
ev('start', 8, 120)                    # cheat code from the pause menu
for k in 'RLRRLRLLR':
    ev('rt' if k == 'R' else 'lt', 8, 25)
t += 60
for _ in range(2):
    ev('down', 6, 40)
ev('a', 6, 90)
if invuln:
    pick('down', 'down')               # Player Invulnerable
pick('down', 'down', 'down')           # Clear Fog Of War
pick('down')                           # $10000
close_menus()

STICKS = ['lleft', 'lright', 'lup', 'ldown']
CAM = ['rleft', 'rright', 'rup', 'rdown']
DIRS = ['up', 'down', 'left', 'right'] + STICKS
end = frames - (1500 if win else 200)
next_cash = t + cash_every


def v1_step(k):
    global t
    if k < 0.30:
        ev('a+' + r.choice(STICKS), r.randrange(10, 40), r.randrange(15, 45))
    elif k < 0.50:
        ev(r.choice(STICKS), r.randrange(5, 30), r.randrange(10, 30))
        ev(r.choice(['a', 'x', 'y', 'a', 'b']), 5, r.randrange(20, 60))
    elif k < 0.62:
        ev(r.choice(CAM), r.randrange(10, 40), r.randrange(10, 30))
    elif k < 0.80:
        ev(r.choice(['x', 'y', 'b', 'black', 'white', 'up', 'down', 'left', 'right', 'lt', 'rt', 'a+x', 'lt+a']),
           r.randrange(3, 12), r.randrange(15, 60))
    else:
        t += r.randrange(60, 400)


def select():
    if r.random() < 0.6:
        ev('a+' + r.choice(STICKS), r.randrange(10, 40), r.randrange(15, 30))   # box
    else:
        ev(r.choice(STICKS), r.randrange(3, 20), 10)
        ev('a', 5, 20)


def command_list():
    ev('rt', r.randrange(20, 40), 25)         # open (held)
    for _ in range(r.randrange(1, 3)):
        ev(r.choice(DIRS), 6, 20)
    ev('a', 5, r.randrange(30, 90))


def v2_step(k):
    global t
    if k < 0.22:                              # command: select, command list, pick
        select(); command_list()
    elif k < 0.40:                            # produce at base
        ev('x', 5, 60)
        ev('a', 5, 30)
        command_list()
        if r.random() < 0.5:
            command_list()
    elif k < 0.50:                            # special ability
        select()
        ev('black', 6, 30)
        ev(r.choice(STICKS), r.randrange(5, 30), 15)
        ev(r.choice(['a', 'b']), 5, r.randrange(30, 80))
    elif k < 0.62:                            # assault: select, scroll far, action
        select()
        ev(r.choice(STICKS), r.randrange(40, 120), 20)
        ev('b', 5, r.randrange(40, 120))
    elif k < 0.70:                            # groups
        ev(r.choice(['lt+up', 'lt+down', 'lt+left', 'lt+right', 'white']), 6, 30)
        ev(r.choice(['up', 'down', 'left', 'right']), 5, 40)
    elif k < 0.78:                            # camera, events, view
        ev(r.choice(CAM + ['y', 'y', 'back']), r.randrange(5, 30), r.randrange(20, 60))
    elif k < 0.80:                            # pause, look around, back out (never activate: Quit)
        ev('start', 6, 60)
        for _ in range(r.randrange(1, 3)):
            ev(r.choice(['down', 'up', 'right', 'left']), 6, 50)
        ev('b', 6, 50); ev('b', 6, 50); ev('start', 6, 80)
    else:                                     # let the simulation run
        t += r.randrange(60, 500)


while t < end:
    if t >= next_cash:                        # more money for purchases
        open_cheats(); pick('down'); close_menus()
        next_cash = t + cash_every
    (v1_step if v1 else v2_step)(r.random())
if win:
    open_cheats(); pick('right'); close_menus()     # Cheat Win (top right)
print('\n'.join(out))
