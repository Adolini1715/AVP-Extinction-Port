#!/usr/bin/env python3
"""Play the campaigns natively, unattended, for coverage.

Boots the native build on a fresh save, goes through the real menus into
Marine mission 1 (nav_m01.sh), then for every mission: closes the briefing,
plays it with random pad input for --play seconds, pauses, enters the cheat
code, turns on Player Invulnerable (if off) and Clear Fog Of War, picks Cheat
Win, continues past the win screen and picks Next Level on the stats screen.
Enable All Levels is picked once (it unlocks every mission in the profile).

Everything is driven from the log: [PAUSE], [CHEAT], [MENU] focus/activate
lines and [LEVEL] loads. The coverage build (avpe_recomp_cov) dumps its
cumulative function set before every level load (AVPE_COVERAGE=<prefix>).

    campaign.py --tag cov1 [--bin avpe_recomp_cov] [--play 240] [--missions 7]
                [--campaigns Marine,Predator,Alien] [--attach [--from-stats]]
After a campaign's last mission (no Next Level) it quits to the main menu and
starts the next campaign's first mission (Start Campaign > <X> Campaign > <X> 1;
every mission is open once Enable All Levels has been picked).
"""
import argparse, os, random, re, subprocess, sys, time

OUT = '/home/claude/avpe-recomp'
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..', 'tools'))

ap = argparse.ArgumentParser()
ap.add_argument('--tag', required=True)
ap.add_argument('--bin', default='avpe_recomp_cov')
ap.add_argument('--play', type=int, default=240, help='seconds of random play per mission')
ap.add_argument('--missions', type=int, default=7)
ap.add_argument('--seed', type=int, default=1)
ap.add_argument('--attach', action='store_true', help='use the running game (log/pad of --tag): in a mission, or on a stats screen with --from-stats')
ap.add_argument('--from-stats', action='store_true', help='attached game is on an end-of-mission stats screen: Quit to the main menu first')
ap.add_argument('--campaigns', default='Marine', help='comma list, played in order (Marine starts through nav_m01.sh on a fresh boot)')
args = ap.parse_args()
rnd = random.Random(args.seed)
LOG = os.path.join(OUT, 'run_%s.log' % args.tag)
PAD = os.path.join(OUT, 'pad_%s.txt' % args.tag)


def note(msg):
    line = '[%s] %s' % (time.strftime('%H:%M:%S'), msg)
    print(line, flush=True)
    with open(os.path.join(OUT, 'campaign_%s.txt' % args.tag), 'a') as f:
        f.write(line + '\n')


class Game:
    def __init__(self):
        self.lines, self.off = [], 0
        self.pad0 = 0       # pad lines written before this driver started (attach mode)

    def alive(self):
        return subprocess.call(['pgrep', '-x', args.bin], stdout=subprocess.DEVNULL) == 0

    def poll(self):
        with open(LOG, errors='ignore') as f:
            f.seek(self.off)
            data = f.read()
            self.off = f.tell()
        if data:
            # keep only the lines the driver reads
            self.lines += [l for l in data.splitlines() if l.startswith(('[MENU]', '[PAUSE]', '[CHEAT]', '[LEVEL]', '[BRIEFING]', '[FRAME]', '[PAD]'))]
        if not self.alive():
            raise SystemExit('game process died; see %s' % LOG)

    def mark(self):
        self.poll()
        return len(self.lines)

    def since(self, m, pat):
        return [l for l in self.lines[m:] if re.search(pat, l)]

    def wait(self, pat, timeout, m=None):
        m = self.mark() if m is None else m
        t0 = time.time()
        while time.time() - t0 < timeout:
            hit = self.since(m, pat)
            if hit:
                return hit[0]
            time.sleep(0.5)
            self.poll()
        return None

    def press(self, spec, gap=1.2):
        """Queue a press, then wait until the game has taken it (the pad
        reads one press at a time, and slowly while menus load) and <gap>."""
        with open(PAD, 'a') as f:
            f.write(spec + '\n')
        self.drain()
        time.sleep(gap)
        self.poll()

    def drain(self, timeout=60):
        want = sum(1 for _ in open(PAD)) - self.pad0     # presses this driver queued
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.poll()
            if sum(1 for l in self.lines if l.startswith('[PAD] press')) >= want:
                return True
            time.sleep(0.1)
        return False

    def focus(self):
        self.poll()
        for l in reversed(self.lines):
            if l.startswith('[MENU] focus'):
                m = re.search(r'item=[0-9A-F]+ "(.*)" \(frame', l)
                return m.group(1) if m else ''
        return ''

    def frame(self):
        for l in reversed(self.lines):
            if l.startswith('[FRAME]'):
                return int(l.split('#')[1])
        return 0

    def goto(self, text, dirs=('down', 'up', 'right', 'left'), max_each=12):
        """Move menu focus to the item showing <text> (exact, or regex if it
        starts with ^). Returns True when focused."""
        ok = (lambda f: re.search(text, f)) if text.startswith('^') else (lambda f: f == text)
        for d in dirs:
            for _ in range(max_each):
                if ok(self.focus()):
                    return True
                m = self.mark()
                self.press(d + ':200')
                if not self.since(m, r'^\[MENU\] focus'):
                    break
        return bool(ok(self.focus()))


def peek_byte(va):
    import importlib, peek
    importlib.reload(peek)
    return peek.rd(va, 1)[0]


g = Game()
if not args.attach:
    subprocess.call(['pkill', '-x', 'avpe_recomp']); subprocess.call(['pkill', '-x', 'avpe_recomp_cov'])
    time.sleep(1)
    sd = os.path.join(OUT, 'rs_%s_%d' % (args.tag, time.time()))
    os.makedirs(sd)
    open(PAD, 'w').close()
    env = dict(os.environ, DISPLAY=':99', AVPE_NO_GPU='1', AVPE_NOVSYNC='1', AVPE_FATAL_EXIT='1',
               AVPE_SAVE_DIR=sd, AVPE_PAD=PAD, AVPE_COVERAGE=os.path.join(OUT, 'cov_%s' % args.tag))
    subprocess.Popen([os.path.join(OUT, args.bin)], cwd=OUT, env=env, stdout=open(LOG, 'w'),
                     stderr=subprocess.STDOUT, start_new_session=True)
    note('started %s, save dir %s' % (args.bin, sd))
    time.sleep(3)
    # boot -> Marine mission 1 through the real menus (stops when the first briefing is closed)
    nav = subprocess.run(['timeout', '900', 'sh', os.path.join(HERE, 'nav_m01.sh'), LOG, PAD], capture_output=True, text=True)
    note('nav: ' + nav.stdout.strip())
    briefing_done = True
else:
    g.off = os.path.getsize(LOG)
    g.pad0 = sum(1 for _ in open(PAD))
    briefing_done = True

STICK = ['lleft', 'lright', 'lup', 'ldown', 'lup+lleft', 'lup+lright', 'ldown+lleft', 'ldown+lright']
OTHER = ['a', 'a', 'a', 'x', 'y', 'b', 'lt', 'rt', 'white', 'black', 'up', 'down', 'left', 'right',
         'rleft', 'rright', 'rup', 'rdown', 'lthumb', 'rthumb', 'a+' + 'lup', 'x+a', 'y+a']


def play(seconds):
    t0 = time.time(); n = 0
    while time.time() - t0 < seconds:
        if rnd.random() < 0.45:
            hold = rnd.choice((150, 300, 600, 1000, 1600)); b = rnd.choice(STICK)
        else:
            hold = rnd.choice((100, 150, 250)); b = rnd.choice(OTHER)
        g.press('%s:%d' % (b, hold), hold / 1000 + 0.1)
        n += 1
    return n


def pause():
    for attempt in range(6):
        for _ in range(3):
            g.press('b:150', 0.6)       # close whatever the random play opened
        m = g.mark()
        g.press('start:300', 3)
        if g.since(m, r'^\[PAUSE\] GPauseMenu::Create|^\[MENU\] focus .*"Resume"'):
            return True
        time.sleep(2)
    return False


def cheats_menu():
    """From the pause menu (focus Resume): enter the code, open Options > Cheats."""
    m = g.mark()
    for k in 'RLRRLRLLR':
        g.press(('rt' if k == 'R' else 'lt') + ':250', 0.7)
    time.sleep(1); g.poll()
    if not g.since(m, 'UNLOCKED'):
        note('cheat code not confirmed')
    if not g.goto('Options', dirs=('down',)):
        note('no Options item (focus %r)' % g.focus()); return False
    g.press('a:250', 2.5)
    if g.focus() != 'Cheats':
        note('Options did not open on Cheats (focus %r)' % g.focus()); return False
    return True


def pick_cheat(text):
    """Options (focus Cheats) -> Cheats -> <text>; the menu closes back to Options."""
    g.press('a:250', 2.5)
    if not g.goto(text, dirs=('down', 'up', 'right', 'down', 'up')):
        note('cheat item %r not found (focus %r)' % (text, g.focus())); return False
    m = g.mark()
    g.press('a:250', 2.5)
    ok = bool(g.since(m, r'^\[CHEAT\] menu picked'))
    if g.focus() != 'Cheats':
        g.goto('Cheats', dirs=('up',))
    return ok


def to_main_menu():
    """From an end-of-mission stats screen: Quit, wait for the main menu."""
    if not g.goto('Quit', dirs=('down', 'up')):
        note('  no Quit on the stats screen (focus %r)' % g.focus()); return False
    m = g.mark()
    g.press('a:250', 3)
    if not g.wait(r"^\[LEVEL\] load #\d+ 'mainmenu\.tbd'", 120, m):
        note('  main menu did not load'); return False
    time.sleep(8)
    return True


def start_campaign(name):
    """Main menu -> Start Campaign -> <name> Campaign -> <name> 1. Returns the
    log mark to wait for the briefing from."""
    m = g.mark()
    for item in ('Start Campaign', '%s Campaign' % name, '%s 1' % name):
        if not g.goto(item, dirs=('down', 'up', 'right', 'left')):
            note('  menu item %r not found (focus %r)' % (item, g.focus())); return None
        g.press('a:250', 3)
    if not g.wait(r"^\[LEVEL\] load", 120, m):
        note('  no level load after %s 1' % name); return None
    return m


all_levels = False


def run_campaign(name, briefing_done, m_next):
    global all_levels
    for mission in range(args.missions):
        if not briefing_done:
            if not g.wait(r'^\[BRIEFING\] mission goals up', 600, m_next):
                note('no briefing; stopping'); return False
            time.sleep(3)
            g.press('start:400', 3)
            g.wait(r'^\[BRIEFING\] closed', 30)
        briefing_done = False
        g.poll()
        lvl = [l for l in g.lines if l.startswith('[LEVEL]')]
        lname = lvl[-1].split("'")[1] if lvl else '?'
        note('%s mission %d: %s, frame %d; playing %ds' % (name, mission + 1, lname, g.frame(), args.play))
        n = play(args.play)
        note('  %d random inputs, frame %d' % (n, g.frame()))
        if not pause():
            note('  could not pause; stopping'); return False
        if not cheats_menu():
            note('  stopping'); return False
        try:
            inv = peek_byte(0x2F6AE1)
        except Exception:
            inv = -1
        if inv == 0:
            note('  Player Invulnerable: %s' % pick_cheat('Player Invulnerable'))
        note('  Clear Fog Of War: %s' % pick_cheat('Clear Fog Of War'))
        if not all_levels:
            all_levels = pick_cheat('Enable All Levels'); note('  Enable All Levels: %s' % all_levels)
        note('  Cheat Win: %s' % pick_cheat('Cheat Win'))
        g.press('b:250', 2); g.press('b:250', 2)            # Options -> pause menu -> resume
        # The win comes 5 game seconds later; its "Press A to Continue" screen
        # only shows up in the trace once A is pressed on it, so press A until
        # the end-of-mission menu loads.
        time.sleep(10)
        m = g.mark(); end = None; t0 = time.time()
        while not end and time.time() - t0 < 240:
            g.press('a:250', 4)
            end = g.since(m, r"^\[LEVEL\] load #\d+ '.*End\.tbd'")
        if not end:
            note('  no end-of-mission menu within 4 minutes; stopping'); return False
        note('  win screen passed: %s' % end[0])
        time.sleep(3)
        if not g.goto('Next Level', dirs=('down', 'up')):
            note('  no Next Level: %s campaign finished' % name)
            return True
        m = m_next = g.mark()
        g.press('a:250', 3)
        nxt = g.wait(r'^\[LEVEL\] load', 120, m)
        note('  next: %s' % nxt)
        if not nxt or 'background' not in nxt:
            note('  unexpected load; stopping'); return False
    return True


camps = [c.strip() for c in args.campaigns.split(',') if c.strip()]
on_stats = args.from_stats
for i, c in enumerate(camps):
    if i == 0 and not args.attach and c == 'Marine':
        ok = run_campaign(c, True, 0)            # nav_m01.sh already closed the first briefing
    elif i == 0 and args.attach and not on_stats:
        ok = run_campaign(c, True, 0)            # attached inside a mission
    else:
        if on_stats and not to_main_menu():
            break
        m = start_campaign(c)
        if m is None:
            break
        ok = run_campaign(c, False, m)
    on_stats = True
    if not ok:
        break
note('done at frame %d' % g.frame())
