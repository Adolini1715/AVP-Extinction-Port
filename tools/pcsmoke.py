#!/usr/bin/env python3
"""Smoke test of the PC build through its keyboard and mouse path.

    pcsmoke.py [--script NAME] [--out DIR] [--size WxH] [--seed DIR] [--keep]

Runs the Linux build in PC mode (AVPE_PC=1) on the X display :99 with a fresh
save folder and scripted input (AVPE_PC_INPUT: keys, clicks, pointer moves, as
SDL events, so everything goes through pc_input.cpp exactly as a player's
input does). The script reads the game's log ([MENU] focus, [BRIEFING],
[LEVEL], [PAUSE]) to steer by menu text, takes screenshots of the window at
each step (OUT/NN-name.png), and fails on a crash, a hang (no new frame for
60 s) or a step whose expected menu never shows.

Scripts: front (keyboard through the front end to Marine 1, pause menu, the
mouse over the pause menu, save, quit to the main menu, load);
menus (every main menu page by keyboard and mouse, no mission).
Exit status 0 = every step passed; the log ends with a PASS/FAIL list.
"""
import argparse, os, re, shutil, signal, subprocess, sys, tempfile, time

GAME = os.environ.get('AVPE_SMOKE_GAME', '/home/claude/avpe-recomp/avpe_recomp')
ap = argparse.ArgumentParser()
ap.add_argument('--script', default='front')
ap.add_argument('--out', default='/home/claude/pcsmoke')
ap.add_argument('--size', default='1280x720')
ap.add_argument('--seed', default='')
ap.add_argument('--keep', action='store_true', help='leave the save folder')
ap.add_argument('--level', default='', help='load this level directly (AVPE_LEVEL, e.g. T05/background.tbd)')
args = ap.parse_args()
W, H = (int(v) for v in args.size.split('x'))

os.makedirs(args.out, exist_ok=True)
for f in os.listdir(args.out):
    if f.endswith('.png') or f in ('game.log', 'input.txt'):
        os.remove(os.path.join(args.out, f))
LOG = os.path.join(args.out, 'game.log')
INP = os.path.join(args.out, 'input.txt')
open(INP, 'w').close()
saves = tempfile.mkdtemp(prefix='avpe-pcsmoke-saves.')
if args.seed:
    shutil.copytree(args.seed, saves, dirs_exist_ok=True)

results, shots = [], 0
T0 = time.time()


def note(m):
    print('[%6.1f] %s' % (time.time() - T0, m), flush=True)


class Game:
    def __init__(self):
        env = dict(os.environ, DISPLAY=':99', AVPE_PC='1', AVPE_PC_SIZE='%dx%d' % (W, H), AVPE_PC_INPUT=INP,
                   AVPE_SAVE_DIR=saves, AVPE_NOVSYNC='1', AVPE_PC_NOGRAB='1', AVPE_NO_SETTINGS='1',
                   AVPE_PC_INPUTLOG='1', AVPE_NOAPU='1', AVPE_PC_MENULOG='1',
                   AVPE_TELEMETRY=os.path.join(args.out, 'telemetry.jsonl'), AVPE_TELEMETRY_EVERY='30', AVPE_QUICKSAVE_LOG='1', AVPE_PC_PANLOG='1')
        if args.level:
            env.update(AVPE_LEVEL=args.level, AVPE_LEVEL_AT='2')
        self.logf = open(LOG, 'w')
        self.p = subprocess.Popen([GAME], stdout=self.logf, stderr=subprocess.STDOUT, env=env,
                                  cwd=os.path.dirname(GAME), start_new_session=True)
        self.off, self.lines, self.focus_seen = 0, [], None
        self.last_frame, self.last_frame_t = 0, time.time()

    def alive(self):
        return self.p.poll() is None

    def poll(self):
        with open(LOG, errors='ignore') as f:
            f.seek(self.off); data = f.read(); self.off = f.tell()
        for l in data.splitlines():
            if l.startswith(('[MENU]', '[PAUSE]', '[LEVEL]', '[BRIEFING]', '[PC]', '[SAVE]', '[LOAD]', '[CRASH]', '[QUICKSAVE]', '[SELECT]', '[MOVIE]',
                             'Segmentation', '*** ', '[FATAL]', '[SIGNAL]', '[PCMENU] wheel', '[PCMENU] camera limit')) or 'D3DDevice_Swap' in l:
                self.lines.append(l)
                m = re.search(r'D3DDevice_Swap #(\d+)', l)
                if m:
                    self.last_frame, self.last_frame_t = int(m.group(1)), time.time()
                if l.startswith('[MENU] focus'):
                    t = self.text(l)
                    if t != self.focus_seen:
                        note('focus: %r' % t); self.focus_seen = t

    @staticmethod
    def text(l):
        m = re.search(r'item=[0-9A-F]+ "(.*)" \(frame', l)
        return m.group(1) if m else ''

    def mark(self):
        self.poll(); return len(self.lines)

    def since(self, m, pat):
        return [l for l in self.lines[m:] if re.search(pat, l)]

    def wait(self, pat, timeout, m=0):
        t0 = time.time()
        while time.time() - t0 < timeout:
            self.poll()
            hit = self.since(m, pat)
            if hit:
                return hit[0]
            if not self.alive():
                return None
            time.sleep(0.25)
        return None

    def send(self, line, settle=1.5):
        with open(INP, 'a') as f:
            f.write(line + '\n')
        time.sleep(settle); self.poll()

    def key(self, k, settle=1.5, hold=150):
        self.send('key %s %d' % (k, hold), settle)

    def click(self, b=0, settle=1.5):
        self.send('click %d 120' % b, settle)

    def moveto(self, x, y, settle=0.6):
        self.send('moveto %d %d' % (x, y), settle)

    def focus(self):
        self.poll()
        for l in reversed(self.lines):
            if l.startswith('[MENU] focus'):
                return self.text(l)
        return ''

    def walk(self, k='Down', n=14):
        seen = [self.focus()]
        for _ in range(n):
            m = self.mark(); self.key(k, 1.2)
            if not self.since(m, r'^\[MENU\] focus'):
                break
            f = self.focus()
            if f in seen:
                break
            seen.append(f)
        return seen

    def goto(self, text, keys=('Down', 'Up')):
        for k in keys:
            for _ in range(14):
                if self.focus() == text:
                    return True
                m = self.mark(); self.key(k, 1.2)
                if not self.since(m, r'^\[MENU\] focus'):
                    break
        return self.focus() == text

    def stop(self):
        try:
            os.killpg(self.p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        self.p.wait()
        self.logf.close()


def shot(name):
    global shots
    shots += 1
    path = os.path.join(args.out, '%02d-%s.png' % (shots, name))
    # the window is centred on the virtual screen: capture it by its id
    wid = subprocess.run(['xwininfo', '-display', ':99', '-name', 'Aliens versus Predator: Extinction'],
                         capture_output=True, text=True).stdout
    m = re.search(r'Window id: (0x[0-9a-f]+)', wid)
    subprocess.call(['import', '-display', ':99', '-window', m.group(1) if m else 'root', path],
                    stderr=subprocess.DEVNULL)
    return path


def check(name, ok, detail=''):
    results.append((name, bool(ok), detail))
    note('%s %s %s' % ('PASS' if ok else 'FAIL', name, detail))
    return ok


def boot(g):
    l = g.wait(r'^\[MENU\] focus', 300)
    if not check('boot to the title', l, g.focus()):
        return False
    time.sleep(4); shot('title')
    return True


def to_mission(g):
    """Keyboard only: Enter through title, profile, campaign, Marine 1."""
    m = g.mark()
    g.key('Return', 3)
    check('Enter leaves the title', g.since(m, r'^\[MENU\] focus') and g.focus() not in ('', 'Press START'), g.focus())
    shot('after-title')
    for _ in range(8):
        if g.since(0, r'\[LEVEL\] load #\d+ .M01/'):
            break
        before = g.focus()
        g.key('Return', 3)
        if g.focus() == before and g.focus() not in ('Marine 1', 'Select'):
            g.key('Return', 3)
    if not check('Enter reaches Marine 1', g.wait(r'\[LEVEL\] load #\d+ .M01/', 120), g.focus()):
        return False
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up', 300)):
        return False
    time.sleep(3); shot('briefing')
    m = g.mark(); g.key('Return', 3)
    if not check('Enter closes the briefing', g.wait(r'\[BRIEFING\] closed', 60, m)):
        return False
    time.sleep(3); shot('intro')
    # mission intro (a camera fly-over): Esc skips it
    m = g.mark(); g.key('Escape', 4)
    skipped = g.since(m, r'Esc skips the mission intro')
    # the intro ignores Esc otherwise: Esc opening the pause menu now means it is over
    m = g.mark(); g.key('Escape', 3)
    paused = any(g.text(l) == 'Resume' for l in g.since(m, r'^\[MENU\] focus'))
    check('Esc skips the mission intro', skipped and paused, g.focus())
    if paused:
        g.key('Escape', 3)
    time.sleep(6); shot('mission')
    return True


def pause_menu(g):
    for _ in range(3):
        m = g.mark(); g.key('Escape', 3)
        hits = [g.text(l) for l in g.since(m, r'^\[MENU\] focus')]
        if any(t and 'Skip' not in t for t in hits):
            break
    labels = g.walk('Down')
    shot('pause')
    check('Esc opens the pause menu', 'Resume' in labels and 'Save' in labels, ' | '.join(labels))
    m = g.mark(); g.key('Escape', 3)
    check('Esc in the pause menu resumes', g.since(m, r'pause handler registered') and not g.since(m, r'Esc skips'), g.focus())
    for _ in range(3):
        m = g.mark(); g.key('Escape', 3)
        if any(g.text(l) == 'Resume' for l in g.since(m, r'^\[MENU\] focus')):
            break
    g.walk('Down')
    return labels


def mouse_hover(g, label):
    """Sweep the pointer down the screen centre; True when the focus reaches <label>, then click it."""
    for y in range(int(H * 0.15), int(H * 0.95), max(8, H // 60)):
        for x in (W // 2, W // 2 - W // 8, W // 2 + W // 8, W * 3 // 4):   # 4:3: the pause buttons sit right of centre + W/8
            g.moveto(x, y, 0.35)
            if g.focus() == label:
                return (x, y)
    return None


def front(g):
    if not boot(g) or not to_mission(g):
        return
    labels = pause_menu(g)
    if 'Resume' not in labels:
        return
    # mouse: hover finds an item, click activates
    g.goto('Resume', ('Up',))
    pos = mouse_hover(g, 'Options')
    check('mouse hover moves the focus', pos, str(pos))
    if pos:
        m = g.mark(); g.click(0, 3); shot('options-by-mouse')
        check('click opens Options', g.since(m, r'^\[MENU\] (activate|focus)') and g.focus() != 'Options', g.focus())
        m = g.mark(); g.click(1, 3)
        check('right click goes back', g.focus() in labels, g.focus())
    # save by keyboard
    g.goto('Save')
    m = g.mark(); g.key('Return', 3); shot('save-page')
    pg = g.walk('Down', 8)
    note('save page: %s' % ' | '.join(pg))
    g.goto(pg[0], ('Up',))
    m = g.mark(); g.key('Return', 4); shot('save-1')
    for _ in range(4):           # confirm or name prompts
        f = g.focus()
        if f in labels and f != 'Save':
            break
        g.key('Return', 3)
    shot('save-done')
    saved = [os.path.join(r, f) for r, _, fs in os.walk(saves) for f in fs if not f.startswith('Partition')]
    check('save writes a file', any('UDATA' in p for p in saved), ' '.join(os.path.relpath(p, saves) for p in saved)[:300])
    # back to the pause menu, Quit
    for _ in range(4):
        if g.focus() in labels:
            break
        g.key('Escape', 2)
    g.goto('Quit')
    m = g.mark(); g.key('Return', 3); shot('quit-confirm')
    note('quit confirmation: %s' % ' | '.join(g.walk('Up', 4) + g.walk('Down', 4)))
    g.goto('Yes', ('Up', 'Down', 'Left', 'Right'))
    g.key('Return', 3)
    l = g.wait(r'(?i)\[LEVEL\] load #\d+ .(attract|mainmenu|.*menu)', 90, m) or g.wait(r'focus.*"(Start Campaign|Load Game|Load)"', 60, m)
    shot('back-at-menu')
    check('Quit returns to the main menu', l, g.focus())
    # load from the main menu
    labels2 = g.walk('Down')
    note('main menu: %s' % ' | '.join(labels2))
    tgt = next((t for t in labels2 if re.match(r'(?i)load', t)), None)
    if tgt is None:
        g.goto('Start Campaign'); m = g.mark(); g.key('Return', 3)
        labels2 = g.walk('Down'); note('campaign menu: %s' % ' | '.join(labels2))
        tgt = next((t for t in labels2 if re.match(r'(?i)load|continue', t)), None)
    if not check('a Load item is in the menus', tgt, ' | '.join(labels2)):
        return
    g.goto(tgt)
    m = g.mark(); g.key('Return', 3); shot('load-page')
    for _ in range(4):
        if g.since(m, r'\[LEVEL\] load #\d+ .M01/'):
            break
        g.key('Return', 3)
    l = g.wait(r'\[LEVEL\] load #\d+ .M01/', 120, m)
    check('Load starts Marine 1 from the save', l)
    time.sleep(15); shot('loaded')


def menus(g):
    if not boot(g):
        return
    g.key('Return', 3)
    labels = g.walk('Down')
    note('first menu: %s' % ' | '.join(labels))
    prof = [t for t in labels if t not in ('New Profile', 'Delete Profile', 'Select', '')]
    if 'New Profile' in labels and prof:            # a seeded profile: pick it to reach the main menu
        g.goto(prof[0]); g.key('Return', 3)
        for _ in range(3):
            if g.focus() in ('Start Campaign', 'Load Game'):
                break
            g.key('Return', 3)
        labels = g.walk('Down')
        note('main menu: %s' % ' | '.join(labels))
    shot('menu')
    for t in labels:
        if not t or t == 'Select':
            continue
        g.goto(t)
        m = g.mark(); g.key('Return', 3); shot('page-' + re.sub(r'\W+', '_', t)[:20])
        sub = g.walk('Down', 10)
        note('%s: %s' % (t, ' | '.join(sub)))
        g.key('Escape', 3)
        check('Esc backs out of %s' % t, g.focus() in labels, g.focus())


def zoom(g):
    """Wheel zoom in a mission: full out, five single notches in, full in (AVPE_PC_MENULOG lines)."""
    if not boot(g) or not to_mission(g):
        return
    g.moveto(W // 2, H // 2)                                     # to_mission skipped the intro: in play
    def arrivals(m):
        return [float(re.search(r'arrived at ([0-9.]+)', l).group(1)) for l in g.since(m, r'wheel zoom arrived')]
    m = g.mark(); g.send('wheel -20', 3)
    out = arrivals(m)
    check('wheel out stops at the far limit', out and abs(out[-1] - 52) < 0.05, str(out))
    steps = []
    for _ in range(5):
        m = g.mark(); g.send('wheel 1', 1.5)
        a = arrivals(m); steps.append(a[-1] if a else None)
    note('one notch at a time: %s' % steps)
    ok = all(s is not None for s in steps) and all(abs(steps[i] - 52 * 0.88 ** (i + 1)) < 0.05 for i in range(5))
    check('each notch in is 12%% of the distance', ok, str(steps))
    shot('zoomed')
    m = g.mark(); g.send('wheel 40', 3)
    inn = arrivals(m)
    check('wheel in stops at the near limit', inn and abs(inn[-1] - 10) < 0.05, str(inn))
    lims = g.since(0, r'camera limit:')
    check('the limits never had to pull the camera back', not lims, lims[-1] if lims else '')
    shot('near')


def wheel(g):
    """Alt held in a mission: the order panel (pad RT) must be drawn."""
    if not boot(g) or not to_mission(g):
        return
    g.moveto(W // 2, H // 2)
    shot('no-alt')
    g.send('keydown Left_Alt', 2)
    shot('alt-held'); time.sleep(1); shot('alt-held2')
    g.send('keyup Left_Alt', 2)
    shot('alt-released')


def wheel2(g):
    """What keeps the order panel from showing while Alt is held?"""
    if not boot(g) or not to_mission(g):
        return
    g.moveto(W // 2, H // 2)
    def trial(name, during):
        time.sleep(2)
        g.send('keydown Left_Alt', 0.1)
        during()
        shot('alt-' + name)
        g.send('keyup Left_Alt', 1.5)
    trial('still', lambda: time.sleep(2))
    def taps():
        for _ in range(10):
            g.send('key F12 40', 0.2)
    trial('f12-taps', taps)
    def moves():
        for i in range(20):
            g.send('move %d 0' % (6 if i % 2 else -6), 0.1)
    trial('mouse-moving', moves)
    trial('short', lambda: time.sleep(0.3))
    def other():
        for _ in range(10):
            g.send('key Left_Ctrl 40', 0.2)
    trial('ctrl-taps', other)


def levelwheel(g):
    """--level: straight into a level, then Alt held (the order panel)."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    m = g.mark()
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up', 300, m)):
        return
    time.sleep(3); shot('briefing')
    m = g.mark(); g.key('Return', 3)
    check('briefing closed', g.wait(r'\[BRIEFING\] closed', 60, m))
    time.sleep(3)
    m = g.mark(); g.key('Escape', 4)
    time.sleep(4); shot('play')
    g.moveto(W // 2, H // 2)
    for i in range(3):
        g.send('keydown Left_Alt', 2); shot('alt-%d' % i); g.send('keyup Left_Alt', 3)
    check('level played', g.alive())


def audit(g):
    """--level: every button prompt on screen for a while (tutorial texts), then Q and Alt panels."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    m = g.mark()
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up', 300, m)):
        return
    time.sleep(3); shot('briefing')
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(2); shot('intro')
    g.key('Escape', 4)
    g.moveto(W // 2, H // 2)
    for i in range(8):
        time.sleep(6); shot('play-%d' % i)
    g.key('Q', 3); shot('q-panel'); time.sleep(2); shot('q-panel2')
    g.key('Q', 3)
    g.send('keydown Left_Alt', 2); shot('alt-panel'); g.send('keyup Left_Alt', 2)
    texts = sorted(set(l[len('[PC] prompt text: '):] for l in open(LOG, errors='ignore') if l.startswith('[PC] prompt text: ')))
    note('texts with button glyphs: %d' % len(texts))
    for t in texts:
        note('  ' + t)
    check('level played', g.alive())


def brief(g):
    """--level: the mission's briefing page only (text layout checks)."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up', 300, 0)):
        return
    time.sleep(3); shot('briefing')


def hudprobe(g):
    """--level, HUDPROBE_STEPS=<file>: blocks of AVPE_PC_HUDPROBE lines, each
    headed "# name"; each block is written to the probe file in play and the
    screen captured (pc_hud.cpp applies the deltas to the live HUD objects)."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)               # Esc skips the intro (pc_input.cpp)
    check('intro skipped', g.wait(r'Esc skips the mission intro', 20, m))
    g.moveto(W // 2, H // 3); time.sleep(10); shot('before')
    blocks = open(os.environ['HUDPROBE_STEPS']).read().split('# ')[1:]
    for b in blocks:
        name, body = b.split('\n', 1)
        with open(os.environ['AVPE_PC_HUDPROBE'], 'w') as f:
            f.write(body)
        time.sleep(6); shot(name.strip())
    check('probe ran', g.alive())


def hudclick(g):
    """--level, HUDCLICKS="fx,fy;fx,fy": the mission's HUD, then a left click at each point
    (fractions of the window) with a picture after each (the minimap moves the camera)."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    check('intro skipped', g.wait(r'Esc skips the mission intro', 20, m))
    g.moveto(W // 2, H // 3); time.sleep(8); shot('mission')
    for i, pt in enumerate(os.environ.get('HUDCLICKS', '').split(';')):
        if not pt.strip():
            continue
        fx, fy = (float(v) for v in pt.split(','))
        g.moveto(int(W * fx), int(H * fy)); g.click(0, 1); g.moveto(W // 2, H // 3); time.sleep(4)
        shot('click%d' % i)
    check('clicks ran', g.alive())


def dragbox(g):
    """--level: a drag box over the army (the game's pointer driven to game coordinates), then Ctrl+A."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    check('intro skipped', g.wait(r'Esc skips the mission intro', 20, m))
    time.sleep(6)
    x0, y0, x1, y1 = (float(v) for v in os.environ.get('DRAGBOX', '120,80,460,330').split(','))
    for _ in range(3):
        g.send('pointer %d %d' % (x0, y0), 0.4)
    m = g.mark()
    g.send('mousedown 0', 0.3)
    for i in range(1, 9):
        g.send('pointer %d %d' % (x0 + (x1 - x0) * i / 8, y0 + (y1 - y0) * i / 8), 0.15)
    g.send('pointer %d %d' % (x1, y1), 0.5)
    shot('box-held')
    g.send('mouseup 0', 1.5)
    l = g.wait(r'\[SELECT\] box selected', 10, m)
    check('the box selects units', l and int(l.split()[3]) > 0, l or 'no [SELECT] line')
    time.sleep(1); shot('box-selected')
    m = g.mark()
    for _ in range(2):
        g.send('pointer %d %d' % (320, 200), 0.3)
    g.send('click 0 80', 1.5)
    l = g.wait(r'\[SELECT\]', 3, m)
    check('a click still goes to the game (no box)', not l, l or '')
    m = g.mark(); g.key('Left_Ctrl+A', 2)
    l = g.wait(r'\[SELECT\] all on screen', 30, m)
    check('Ctrl+A selects everything on screen', bool(l) and int(l.split()[4]) > 0, l or 'no line')
    time.sleep(1); shot('select-all')


def padmouse(g):
    """--level: the pad's stick turns the pointer into the pad cursor (centred); the mouse must take it back."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    def where():
        m = g.mark(); g.send('where 0', 0.6)
        l = g.wait(r'\[PC\] pointer at', 5, m)
        if not l:
            return None
        xy, t = l.split('pointer at ')[1].split(' type ')
        x, y = (float(v) for v in xy.split(','))
        return x, y, int(t.split()[0])
    for _ in range(3):
        g.send('pointer 150 120', 0.4)
    a = where(); note('mouse first: %s' % (a,))
    check('the mouse has the pointer', a and a[2] in (0, 2) and abs(a[0] - 150) < 12, str(a))
    g.send('stick 1500 -1200', 0.8)                         # a worn stick at rest
    d = where(); g.send('stick 0 0', 0.5); note('a drifting stick: %s' % (d,))
    check('a drifting stick leaves the mouse the pointer', d and d[2] in (0, 2), str(d))
    g.send('stick 0 20000', 0.5); g.send('stick 0 0', 1.0)
    b = where(); note('after the stick: %s' % (b,))
    check('the stick gives the pad cursor', b and b[2] == 1, str(b))
    shot('pad-cursor')
    m = g.mark()
    for _ in range(3):
        g.send('pointer 470 330', 0.4)
    c = where(); note('mouse again: %s' % (c,))
    check('the mouse takes the pointer back', bool(g.wait(r'mouse takes the pointer back', 5, m)) and c and c[2] == 2, str(c))
    check('the pointer follows the mouse', c and abs(c[0] - 470) < 15 and abs(c[1] - 330) < 15, str(c))
    shot('mouse-back')


def wasd(g):
    """--level: keys scroll at once (no ease-in), W+A goes diagonally, the level's rates come back."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    def cam():
        m = g.mark(); g.send('cam 0', 0.4)
        l = g.wait(r'\[PC\] camera at', 5, m)
        a = l.split('camera at ')[1]
        x, y = (float(v) for v in a.split(' vel ')[0].split(','))
        vx, vy = (float(v) for v in a.split(' vel ')[1].split(' acc ')[0].split(','))
        acc = float(a.split(' acc ')[1].split()[0])
        return x, y, vx, vy, acc
    c0 = cam(); note('rest: %s' % (c0,))
    g.send('keydown D', 0.12)
    c1 = cam(); note('D after a moment: %s' % (c1,))
    g.send('keyup D', 0.6)
    c2 = cam(); note('released: %s' % (c2,))
    check('a key scrolls at full speed at once', abs(c1[2]) > 1.0 or abs(c1[3]) > 1.0, str(c1))
    check('it stops on release', abs(c2[2]) < 0.01 and abs(c2[3]) < 0.01, str(c2))
    check("the level's rates come back", abs(c2[4] - c0[4]) < 0.01, '%s vs %s' % (c2[4], c0[4]))
    def held(keys):
        for k in keys: g.send('keydown ' + k, 0.05)
        time.sleep(0.4); c = cam()
        for k in keys: g.send('keyup ' + k, 0.05)
        time.sleep(0.6)
        return c
    w = held(['W']); a = held(['A']); wa = held(['W', 'A']); note('W %s A %s W+A %s' % (w, a, wa))
    import math
    ex, ey = (w[2] + a[2]) * 0.7071, (w[3] + a[3]) * 0.7071
    check('W+A goes diagonally (W and A combined)', abs(wa[2] - ex) < 0.6 and abs(wa[3] - ey) < 0.6, 'got %.2f,%.2f want %.2f,%.2f' % (wa[2], wa[3], ex, ey))
    check('a diagonal is no faster', abs(math.hypot(wa[2], wa[3]) - math.hypot(w[2], w[3])) < 0.6, '')


def holdleft(g):
    """--level: holding the left button must not move the pointer (the tester saw it snap to the centre)."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    for _ in range(3):
        g.send('pointer 150 120', 0.4)
    m = g.mark()
    g.send('mousedown 0', 0.05)
    for i in range(10):
        g.send('where 0', 0.1)
    shot('held')
    g.send('mouseup 0', 0.5)
    g.send('where 0', 0.5)
    pts = [l.split('pointer at ')[1] for l in g.since(m, r'\[PC\] pointer at')]
    note('pointer while held: %s' % ' | '.join(pts))
    xs = [tuple(float(v) for v in p.split(' type ')[0].split(',')) for p in pts]
    check('the pointer stays put while the button is held', xs and all(abs(x - 150) < 6 and abs(y - 120) < 6 for x, y in xs), ' | '.join(pts))


def rightdrag(g):
    """--level (AVPE_PC_INPUTLOG=1): right-drag pans like grabbing the map, no order; a right click still orders."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    def cam():
        m = g.mark(); g.send('cam 0', 0.4)
        a = g.wait(r'\[PC\] camera at', 5, m).split('camera at ')[1]
        x, y = (float(v) for v in a.split(' vel ')[0].split(','))
        vx, vy = (float(v) for v in a.split(' vel ')[1].split(' acc ')[0].split(','))
        fov = float(a.split(' fov ')[1].split()[0]); dist = float(a.split(' dist ')[1].split()[0])
        return x, y, vx, vy, fov, dist
    g.send('keydown D', 0.3); d = cam(); g.send('keyup D', 0.8)
    for _ in range(3):
        g.send('pointer 300 240', 0.4)
    c0 = cam(); note('before: %s, D velocity %.2f,%.2f' % (c0, d[2], d[3]))
    m = g.mark()
    g.send('mousedown 1', 0.1)
    for _ in range(10):
        g.send('move -12 0', 0.05)
    g.send('where 0', 0.3)
    shot('panned')
    g.send('mouseup 1', 0.6)
    c1 = cam(); note('after a left drag: %s' % (c1,))
    dx, dy = c1[0] - c0[0], c1[1] - c0[1]
    check('the drag pans', bool(g.since(m, r'right-drag pans')) and (dx * dx + dy * dy) > 0.5, '%.2f,%.2f' % (dx, dy))
    check('dragging left moves the view right (like D)', dx * d[2] + dy * d[3] > 0, '%.2f,%.2f vs %.2f,%.2f' % (dx, dy, d[2], d[3]))
    w = [l.split('pointer at ')[1] for l in g.since(m, r'\[PC\] pointer at')]
    check('the pointer stays during the pan', w and w[0].startswith('300,240'), ' '.join(w))
    check('no order during the drag', not g.since(m, r'pad from keys: .*B FF'), '')
    m = g.mark(); g.send('click 1 80', 0.8)
    check('a right click still orders (B on release)', bool(g.since(m, r'pad from keys: .*B FF')), '')


def rightdrag_angles(g):
    """--level: right-drags in several directions; the view must move the way the mouse moved
    (angle for angle, not snapped to the four axes), and smoothly (no frames without motion)."""
    import math
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    def cam():
        m = g.mark(); g.send('cam 0', 0.4)
        a = g.wait(r'\[PC\] camera at', 5, m).split('camera at ')[1]
        return tuple(float(v) for v in a.split(' vel ')[0].split(','))
    for _ in range(3):
        g.send('pointer 320 240', 0.4)
    res = []
    for mx, my in ((10, 0), (10, 3), (10, 6), (10, 10), (6, 10), (3, 10), (0, 10), (-10, 4)):
        c0 = cam()
        g.send('mousedown 1', 0.1)
        for _ in range(12):
            g.send('move %d %d' % (mx, my), 0.04)
        g.send('mouseup 1', 0.5)
        c1 = cam()
        res.append(((mx, my), (c1[0] - c0[0], c1[1] - c0[1])))
    # the angle between mouse directions must equal the angle between view motions
    def ang(v): return math.degrees(math.atan2(v[1], v[0]))
    base_m, base_v = ang((res[0][0][0], -res[0][0][1])), ang(res[0][1])
    worst = 0
    for (mv, vv) in res:
        dm = (ang((mv[0], -mv[1])) - base_m + 540) % 360 - 180
        dv = (ang(vv) - base_v + 540) % 360 - 180
        err = abs(((dv - dm) + 540) % 360 - 180)
        worst = max(worst, min(err, abs(err - 360)))
        note('mouse %s -> view %.2f,%.2f  mouse angle %+.1f view angle %+.1f' % (mv, vv[0], vv[1], dm, dv))
    check('the view follows the drag direction (within 5 degrees)', worst < 5, 'worst %.1f' % worst)


def rightdrag_gain(g):
    """--level: is the right-drag pan one to one? For a pointer at the top, middle and bottom of
    the screen, compare how far the ground under the pointer moves when the pointer moves N
    pixels (no button) with how far the view moves for a right drag of the same N pixels."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    def cam():
        m = g.mark(); g.send('cam 0', 0.4)
        a = g.wait(r'\[PC\] camera at', 5, m).split('camera at ')[1]
        return tuple(float(v) for v in a.split(' vel ')[0].split(','))
    def world():
        m = g.mark(); g.send('where 0', 0.4)
        a = g.wait(r'\[PC\] pointer at .* world', 5, m)
        sp = a.split('pointer at ')[1].split(' ')[0]; w = a.split('world ')[1].split(' ')[0]
        return tuple(float(v) for v in sp.split(',')), tuple(float(v) for v in w.split(','))
    N = 8
    for py in (140, 240, 340):
        for (mx, my) in ((N, 0), (0, N)):
            for _ in range(3): g.send('pointer 320 %d' % py, 0.4)
            time.sleep(0.5)
            s0, w0 = world()
            for _ in range(5): g.send('move %d %d' % (mx, my), 0.05)
            time.sleep(0.5)
            s1, w1 = world()
            for _ in range(3): g.send('pointer 320 %d' % py, 0.4)
            time.sleep(0.5)
            c0 = cam()
            g.send('mousedown 1', 0.1)
            for _ in range(5): g.send('move %d %d' % (mx, my), 0.05)
            g.send('mouseup 1', 0.6)
            c1 = cam()
            dw = (w1[0] - w0[0], w1[1] - w0[1]); dc = (c1[0] - c0[0], c1[1] - c0[1])
            note('pointer y %d move %s: pointer %.0f,%.0f->%.0f,%.0f ground %+.2f,%+.2f  drag view %+.2f,%+.2f  ratio %.2f' % (
                py, (mx, my), s0[0], s0[1], s1[0], s1[1], dw[0], dw[1], dc[0], dc[1],
                (dc[0] * -dw[0] + dc[1] * -dw[1]) / max(1e-6, dw[0] ** 2 + dw[1] ** 2)))


def rightdrag_circle(g):
    """--level: a right drag round a circle. The pan steps must point all round the circle too
    (playtest: 'only 4 directions'): the share of steps within 10 degrees of an axis is reported."""
    import math
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    for _ in range(3): g.send('pointer 320 240', 0.4)
    R = float(os.environ.get('CIRCLE_R', '3'))
    m = g.mark()
    g.send('mousedown 1', 0.1)
    fx = fy = 0.0
    for i in range(240):
        a = 2 * math.pi * i / 120
        fx += R * math.cos(a); fy += R * math.sin(a)
        ix, iy = int(fx), int(fy); fx -= ix; fy -= iy
        if ix or iy: g.send('move %d %d' % (ix, iy), 0.012)
        else: time.sleep(0.012)
    g.send('mouseup 1', 0.5)
    steps = [tuple(float(v) for v in l.split('pan step ')[1].split(' ')[0].split(',')) for l in g.since(m, r'pan step')]
    steps = [s for s in steps if abs(s[0]) + abs(s[1]) > 1e-3]
    near = 0; bins = [0] * 12
    for sx, sy in steps:
        d = math.degrees(math.atan2(sy, sx)) % 360
        bins[int(d // 30)] += 1
        if min(d % 90, 90 - d % 90) < 10: near += 1
    note('%d pan steps; by 30-degree sector: %s; within 10 degrees of an axis: %.0f%% (even spread: 22%%)' % (len(steps), bins, 100.0 * near / max(1, len(steps))))
    check('pan steps point all round', len(steps) > 50 and min(bins) > 0 and near < 0.45 * len(steps), str(bins))


def rightdrag_jitter(g):
    """--level, AVPE_PC_PANLOG=1: a slow shallow diagonal right-drag sent as whole mouse counts,
    the way a real mouse reports one; the per-frame pan steps must keep one direction."""
    import math
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)
    for _ in range(3):
        g.send('pointer 320 240', 0.4)
    m = g.mark()
    g.send('mousedown 1', 0.1)
    for i in range(160):                          # slope 1/3: two sideways counts, then one diagonal
        g.send('move 3 0' if i < 4 else ('move 1 1' if i % 3 == 2 else 'move 1 0'), 0.012)
    g.send('mouseup 1', 0.6)
    steps = []
    for l in g.since(m, r'\[PC\] pan step'):
        a = l.split('pan step ')[1].split()[0]
        x, y = (float(v) for v in a.split(','))
        steps.append(math.degrees(math.atan2(y, x)))
    steps = steps[5:-5] if len(steps) > 12 else steps
    if not steps:
        check('pan steps logged', False); return
    mean = sum(steps) / len(steps)
    spread = max(abs(a - mean) for a in steps)
    avg = sum(abs(a - mean) for a in steps) / len(steps)
    note('%d steps, mean angle %.1f, mean deviation %.1f, largest %.1f degrees' % (len(steps), mean, avg, spread))
    # here the game runs at about 35 frames a second and the script sends one count per frame, so
    # the zig-zag is about 6 times slower than a 1000 Hz mouse at 240 frames a second: unsmoothed
    # it is 20 degrees on average, smoothed (40 ms) about half; on a real PC the filter takes most of it
    check('the pan keeps one direction (mean deviation under 12 degrees)', avg < 12, 'mean %.1f avg %.1f max %.1f' % (mean, avg, spread))


def groups(g):
    """--level: box A -> Ctrl+1; box B -> Shift+Ctrl+1 (group 1 = A + B); deselect; 1 twice recalls and centres."""
    if not g.wait(r'replaced with', 400):
        check('level loads', False); return
    if not check('briefing up', g.wait(r'\[BRIEFING\] mission goals up \(frame [1-9]', 400, 0)):
        return
    m = g.mark(); g.key('Return', 3)
    g.wait(r'\[BRIEFING\] closed', 60, m)
    time.sleep(10)
    m = g.mark(); g.key('Escape', 4)
    g.wait(r'Esc skips the mission intro', 20, m)
    time.sleep(6)

    def box(x0, y0, x1, y1):
        for _ in range(3):
            g.send('pointer %d %d' % (x0, y0), 0.4)
        m = g.mark()
        g.send('mousedown 0', 0.3)
        for i in range(1, 7):
            g.send('pointer %d %d' % (x0 + (x1 - x0) * i / 6, y0 + (y1 - y0) * i / 6), 0.15)
        g.send('mouseup 0', 2)
        g.poll()
        ls = [l for l in g.since(m, r'\[SELECT\] (box selected|selection now)')]
        return int(ls[-1].split()[3]) if ls else -1           # the game's own drag box (AVPE_PC_SELLOG)
    a = box(100, 60, 300, 330)
    shot('after-a')
    m = g.mark(); g.key('Left_Ctrl+1', 3)
    shot('after-ctrl1')
    b = box(305, 100, 520, 260)
    shot('after-b')
    check('two boxes select units', a > 0 and b > 0, 'A %d, B %d' % (a, b))
    m = g.mark(); g.key('Left_Shift+Left_Ctrl+1', 3)
    l = g.wait(r'\[SELECT\] group 1 now', 30, m)
    k = int(l.split()[4]) if l else -1
    check('Shift+Ctrl+1 adds B to group 1 (the union)', a < k <= a + b, l or 'no line')
    for _ in range(2):
        g.send('pointer 600 40', 0.4)
    g.send('click 0 80', 2)
    m = g.mark(); g.key('1', 0.15, hold=60); g.key('1', 3, hold=60)
    l = g.wait(r'\[SELECT\] centred on the group', 20, m)
    n = int(l.split()[5].lstrip('(')) if l else -1
    check('1 twice recalls group 1 and centres on it', n == k, l or 'no line')
    shot('group-centred')


def pausekeys(g):
    """Marine 1: Esc > Controls (Keyboard & Mouse map, key pages, rebinding) and Esc > Options > PC Settings, pages of the game's own pause menu."""
    if not boot(g) or not to_mission(g):
        return
    time.sleep(4)
    labels = pause_menu(g)
    g.goto('Options'); g.key('Return', 3)
    opts = g.walk('Down', 10); note('options: %s' % ' | '.join(opts)); shot('options')
    if os.environ.get('PK_PROBE_OPTS'):
        open(os.environ['AVPE_PC_HUDPROBE'], 'w').write(os.environ['PK_PROBE_OPTS'].replace(';', '\n') + '\n')
        time.sleep(2); shot('probe-options')
    check('PC Settings on the pause Options page', 'PC Settings' in opts, ' | '.join(opts))
    if 'PC Settings' in opts:
        g.goto('PC Settings'); g.key('Return', 3)
        rows = g.walk('Down', 30); note('pc settings: %s' % ' | '.join(rows)); shot('pcsettings')
        fc = next((r for r in rows if r.startswith('Frame Cap')), None)
        if fc and g.goto(fc, ('Up', 'Down')):
            g.key('Return', 2.0); g.key('Down', 1.2); g.key('Up', 1.2)
            check('Frame Cap changes when pressed in the pause menu', g.focus().startswith('Frame Cap') and g.focus() != fc, '%s -> %s' % (fc, g.focus()))
            shot('pcsettings-changed')
        g.key('Escape', 3)
    g.key('Escape', 3)
    check('Controls on the pause menu', 'Controls' in labels, ' | '.join(labels))
    if 'Controls' in labels:
        g.goto('Controls'); g.key('Return', 3); shot('controls-page')
        check('Controls opens the key list', g.focus() == 'Scroll Up: W', g.focus())
        gk = g.walk('Down', 20); g.key('Right', 1.2); gk += g.walk('Up', 20); gk += g.walk('Down', 20)   # (two columns: down the left, then the right)
        note('key bindings: %s' % ' | '.join(gk)); shot('keys')
        check('every key and the mouse on one page', 'Scroll Up: W' in gk and 'Quicksave: F5' in gk and 'Reset All Keys' in gk and len(set(gk)) >= 25, '%d | %s' % (len(set(gk)), ' | '.join(gk)))
        g.key('Right', 1.2)                                  # (the game keys are in the right column)
        if g.goto('Quicksave: F5', ('Up', 'Down')):
            m = g.mark(); g.key('Return', 1.0); shot('capture')
            check('Enter starts the capture', g.wait(r'\[PC\] key capture Quicksave', 5, m))
            g.key('F6', 2); shot('rebound')
            l = g.wait(r'\[PC\] key QUICKSAVE -> F6', 5, m)
            check('Quicksave rebound to F6 in the pause menu', l, l or 'none')
            g.key('Down', 1.2); g.key('Up', 1.2)
            check('the row shows F6', g.focus() == 'Quicksave: F6', g.focus())
            m = g.mark(); g.key('Return', 1.0); g.key('Escape', 2)
            check('Esc cancels a capture', g.since(m, r'key capture cancelled') and g.focus().startswith('Quicksave'), g.focus())
            g.goto('Quickload: F9'); m = g.mark(); g.key('Return', 1.0); g.key('F6', 2)
            check('a taken key swaps', g.since(m, r'QUICKSAVE -> F9 \(swapped\)'), ' / '.join(g.since(m, r'\[PC\] key')))
            shot('swapped')
            g.goto('Reset All Keys'); m = g.mark(); g.key('Return', 2)
            ini = open(os.path.join(os.path.dirname(GAME), 'avpe.ini')).read()
            check('Reset All Keys', g.since(m, r'keys reset') and 'AVPE_KEY_' not in ini, '')
            g.goto('Quickload: F9', ('Up', 'Down')); shot('after-reset')   # (Picture in the window, not focused: the pointer has to find it)
            pos = mouse_hover(g, 'Picture: F12')
            if check('mouse finds a key row', pos, str(pos)):
                m = g.mark(); g.click(0, 1.0); g.key('F7', 2); shot('mouse-rebound')   # (F11 is the fullscreen key)
                check('a click starts the capture', g.wait(r'\[PC\] key PICTURE -> F7', 5, m), ' / '.join(g.since(m, r'\[PC\] key')))
                g.key('Return', 1.0); g.key('Delete', 2)
                check('Delete puts the default back', g.since(m, r'PICTURE -> F12'), '')
            g.key('Left', 1.2); g.key('Left', 1.2)          # (the right column, then the left: Left again is Swap Mouse)
            check('Left from the left column is Swap Mouse', g.focus() == 'Swap Buttons: Off', g.focus())
            m = g.mark(); g.key('Return', 2); shot('swapped-mouse')
            check('Swap Buttons swaps', g.since(m, r'mouse buttons swapped'), '')
            m = g.mark(); g.key('Return', 2)
            check('and back', g.since(m, r'mouse buttons normal'), '')
            g.key('Right', 1.2); g.goto('Scroll Up: W', ('Up', 'Down'))
            check('scrolls back to the top', g.focus() == 'Scroll Up: W', g.focus()); shot('top-again')
        g.key('Escape', 3)
        check('Esc backs out to the pause menu (Controls is the key list)', g.focus() in labels, g.focus())


def frontcontrols(g):
    """The main menu's Options > Controls page (the game's own controls screen)."""
    if not boot(g):
        return
    for _ in range(6):
        if g.focus() == 'Start Campaign':
            break
        g.key('Return', 3)
    labels = g.walk('Down', 10)
    note('menu: %s' % ' | '.join(labels)); shot('main-menu')
    g.goto('Options'); g.key('Return', 3)
    opts = g.walk('Down', 10); note('options: %s' % ' | '.join(opts)); shot('options')
    if os.environ.get('PK_PROBE_OPTS'):
        open(os.environ['AVPE_PC_HUDPROBE'], 'w').write(os.environ['PK_PROBE_OPTS'].replace(';', '\n') + '\n')
        time.sleep(2); shot('probe-options')
    if check('Controls on Options', 'Controls' in opts, ' | '.join(opts)):
        g.goto('Controls'); g.key('Return', 4); shot('controls')
        if os.environ.get('PK_PROBE'):
            open(os.environ['AVPE_PC_HUDPROBE'], 'w').write(os.environ['PK_PROBE'].replace(';', '\n') + '\n')
            time.sleep(2); shot('probe')
        note('controls page: %s' % ' | '.join(g.walk('Down', 10))); shot('controls2')
        g.key('Right', 2); shot('controls-right')


def movieskip(g):
    """AVPE_MOVIES=1: keys during the logos and the intro skip the movie playing and never reach the menu under it."""
    m = g.mark()
    if not g.wait(r'\[MOVIE\] .*: \d+x\d+', 120):
        check('a movie starts', False, 'no [MOVIE] line (movies off?)')
        return
    for k in ('space', 'Return', 'space', 'Return'):        # one per movie: EA, Fox, Zono, the intro
        time.sleep(1.5)
        g.key(k, 0.3)
    skipped = g.since(m, r'skipped by the player')
    note('movie lines: ' + ' / '.join(l.strip() for l in g.since(m, r'\[MOVIE\]'))[:900])
    check('keys skip the movies', len(skipped) >= 2, str(len(skipped)))
    l = g.wait(r'^\[MENU\] focus', 200, m)
    time.sleep(3)
    check('the title comes up, no game started', l and not g.since(m, r'New Profile|Start Campaign|Marine'), g.focus())
    shot('after-movies')


def moviemash(g):
    """AVPE_MOVIES=1: space tapped over and over through the logos and the intro (playtest: the
    game froze). The title must come up and frames must keep coming."""
    m = g.mark()
    if not g.wait(r'\[MOVIE\] .*: \d+x\d+', 120):
        check('a movie starts', False, 'no [MOVIE] line (movies off?)')
        return
    t0 = time.time()
    while time.time() - t0 < 25:
        g.key('space', 0.08)
        time.sleep(0.12)
    note('movie lines: ' + ' / '.join(l.strip() for l in g.since(m, r'\[MOVIE\]'))[:1200])
    f0 = g.last_frame; time.sleep(8)
    check('frames keep coming after the mashing', g.last_frame > f0 + 30, '%d -> %d' % (f0, g.last_frame))
    l = g.wait(r'^\[MENU\] focus', 60, m)
    check('the title or menu comes up', bool(l), g.focus())
    shot('after-mash')


def moviestall(g):
    """AVPE_MOVIES=1: the playtest freeze. The logos play for a while, then space is tapped as
    the intro starts. The main menu must load (a hang here sat on a black screen for good)."""
    m = g.mark()
    if not g.wait(r'\[MOVIE\] .*Zono.*: \d+x\d+', 120):
        check('the logos start', False); return
    time.sleep(1.0)
    g.key('space', 0.08)
    gap = float(os.environ.get('GAP', '-1'))
    if gap >= 0:                                    # a second press in the gap between the Zono logo and the intro
        time.sleep(gap); g.key('space', 0.08)
    if not g.wait(r'\[MOVIE\] .*Intro.*: \d+x\d+', 60, m):
        check('the intro starts', False); return
    for _ in range(4):
        g.key('space', 0.06); time.sleep(0.05)
    note('movie lines: ' + ' / '.join(l.strip() for l in g.since(m, r'\[MOVIE\]|SOAK\] hitch'))[:1500])
    check('the main menu loads', bool(g.wait(r"\[LEVEL\] load #2 'mainmenu", 40, m)))


def keylist(g):
    """Main menu > Options > Controls with the keyboard: one scrolling page of every key, then Reset."""
    if not boot(g):
        return
    for _ in range(6):
        if g.focus() == 'Start Campaign':
            break
        g.key('Return', 3)
    g.goto('Options'); g.key('Return', 3)
    g.goto('Controls'); g.key('Return', 3); shot('top')
    seen = g.walk('Down', 20); shot('left-down')
    g.key('Right', 1.0); seen.append(g.focus()); shot('right')
    seen += g.walk('Up', 20); shot('right-top')
    seen += g.walk('Down', 20); shot('right-bottom')
    note('rows: %s' % ' | '.join(seen))
    check('every key row and Reset in two columns', len(set(seen)) >= 25 and 'Reset All Keys' in seen, '%d rows' % len(set(seen)))
    g.key('Left', 1.0)
    g.walk('Up', 20)
    check('back to the top', g.focus() == seen[0], g.focus()); shot('back-top')
    pos = mouse_hover(g, 'Scroll Up: W') if 'Scroll Up: W' in seen else None
    check('the mouse finds a row', pos, str(pos))


def pcsettings(g):
    """Main menu > Options > PC Settings: pressing a row changes its value (and the text), saved to avpe.ini."""
    if not boot(g):
        return
    for _ in range(6):
        if g.focus() == 'Start Campaign':
            break
        g.key('Return', 3)
    g.goto('Options'); g.key('Return', 3)
    g.goto('PC Settings'); g.key('Return', 3)
    rows = g.walk('Down', 10); note('pc settings: %s' % ' | '.join(rows)); shot('pcsettings')
    for prefix in ('Widescreen', 'Frame Cap', 'Mouse Speed', 'Prompts', 'Display', 'Res'):
        row = next((r for r in rows if r.startswith(prefix)), None)
        if not check('row %s is there' % prefix, row, ' | '.join(rows)):
            continue
        g.goto(row, ('Down', 'Up'))
        m = g.mark(); g.key('Return', 2.0)
        # the row's text is re-read from the focus line after the change (the last row: up, then down)
        g.key('Up', 1.2); g.key('Down', 1.2)               # (the text is read again from the focus line)
        for _ in range(3):
            if g.focus().startswith(prefix.split(' ')[0]): break
            g.key('Up', 1.2)
        now = g.focus()
        check('%s changes when pressed' % prefix, now != row and now.startswith(prefix.split(' ')[0]), '%s -> %s' % (row, now))
        shot('changed-%s' % prefix.split(' ')[0].lower())
        rows = [now if r == row else r for r in rows]
    # Display: Window, Borderless, Exclusive in turn; F11 leaves the fullscreen and comes back to the same kind
    d = next((r for r in rows if r.startswith('Display')), None)
    if d and g.goto(d, ('Up', 'Down')):
        seen = [d]
        for _ in range(3):
            g.key('Return', 2.5); g.key('Up', 1.2); g.key('Down', 1.2)
            for _ in range(3):
                if g.focus().startswith('Display'): break
                g.key('Down', 1.2)
            seen.append(g.focus())
            if g.focus() == 'Display: Exclusive':
                m = g.mark(); g.key('F11', 2.5); g.key('F11', 2.5)
                check('F11 from Exclusive: window, then Exclusive again', g.since(m, r'display: window') and g.since(m, r'display: exclusive fullscreen'), '')
                shot('exclusive')
        note('display cycle: %s' % ' -> '.join(seen))
        check('Display cycles through Window, Borderless and Exclusive', {'Display: Window', 'Display: Borderless', 'Display: Exclusive'} <= set(seen), ' -> '.join(seen))
    ini = os.path.join(os.getcwd(), 'avpe.ini')
    note('avpe.ini in the working directory: %s' % (open(ini).read().replace('\n', ' ; ')[:300] if os.path.exists(ini) else 'none'))


def quitgame(g):
    """The main menu's Quit Game closes the game (exit 0, the session closed)."""
    if not boot(g):
        return
    for _ in range(6):
        if g.focus() == 'Start Campaign':
            break
        g.key('Return', 3)
    labels = g.walk('Down', 10)
    note('menu: %s' % ' | '.join(labels)); shot('main-menu')
    check('Quit Game on the main menu', 'Quit Game' in labels, ' | '.join(labels))
    g.goto('Quit Game'); g.key('Return', 0)
    for _ in range(50):
        if not g.alive():
            break
        time.sleep(0.2)
    check('Quit Game closes the game', not g.alive() and g.p.poll() == 0, 'exit %s' % g.p.poll())


def uplink(g):
    """Marine 1: the Dropship Uplink by its HUD button (its prompts), then Q closes it."""
    if not boot(g) or not to_mission(g):
        return
    def open_uplink():                                       # the Uplink button over the money panel (game coordinates, wide HUD)
        for _ in range(3):
            g.send('pointer 514 380', 0.4)
        g.click(0, 3)
    open_uplink()
    shot('uplink')
    labels = g.walk('Down', 8)
    note('uplink: %s' % ' | '.join(labels))
    shot('uplink2')
    m = g.mark(); g.key('Q', 3); shot('uplink-closed')
    check('Uplink opens by its HUD button', len(labels) > 1, ' | '.join(labels))
    open_uplink()                                            # open it again: Esc closes it, no pause
    again = g.walk('Down', 3)
    m = g.mark(); g.key('Escape', 3); shot('uplink-esc')
    check('Esc closes the Uplink', bool(g.wait(r'Esc closes the Uplink', 5, m)), ' | '.join(again))
    paused = [g.text(l) for l in g.since(m, r'^\[MENU\] focus')]
    check('and does not pause', 'Resume' not in paused, ' | '.join(t or '' for t in paused))
    m = g.mark(); g.key('Escape', 3); shot('esc-again')
    paused = [g.text(l) for l in g.since(m, r'^\[MENU\] focus')]
    check('a second Esc pauses', 'Resume' in paused, ' | '.join(t or '' for t in paused))


def quicksave(g):
    """Marine 1 through the front end (a real profile): F5 saves, play on, F9 loads it back."""
    if not boot(g) or not to_mission(g):
        return
    time.sleep(20); shot('before-save')
    m = g.mark(); g.key('F5', 4)
    l = g.wait(r'\[QUICKSAVE\] [A-Z]', 60, m)
    check('F5 quicksaves', l and 'Quicksaved' in l, l or 'no [QUICKSAVE] line')
    time.sleep(3); shot('saved')
    m = g.mark(); g.key('F5', 4)
    l = g.wait(r'\[QUICKSAVE\] [A-Z]', 60, m)
    check('F5 again overwrites the same slot', l and 'Quicksaved' in l, l or '')
    g.key('D', 2, hold=2500); time.sleep(25); shot('later')
    m = g.mark(); g.key('F9', 4)
    l = g.wait(r'\[QUICKSAVE\] [A-Z]', 60, m)
    check('F9 starts the quickload', l and 'Quickloading' in l, l or 'no [QUICKSAVE] line')
    l = g.wait(r'\[LEVEL\] load #\d+ .M01/', 180, m)
    check('the mission loads again', l, l or '')
    # as after the game's own Load: the briefing comes up again; Enter closes it
    b = g.wait(r'\[BRIEFING\] mission goals up', 300, m)
    check('the briefing comes up (as the game\'s own Load)', b, b or '')
    m2 = g.mark(); g.key('Return', 3)
    check('Enter closes it', g.wait(r'\[BRIEFING\] closed', 60, m2))
    time.sleep(20); shot('after-load')
    m = g.mark(); g.key('Escape', 3)
    paused = any(g.text(x) == 'Resume' for x in g.since(m, r'^\[MENU\] focus'))
    check('the loaded mission plays (Esc pauses it)', paused, g.focus())
    if paused:
        g.key('Escape', 3)


def autosave(g):
    """Marine 1 through the front end; with AVPE_AUTOSAVE_FIRST=5 AVPE_AUTOSAVE_SECS=15 (set by the
    caller) four autosaves come, named Autosave 1, 2, 3, 1; a pause holds the clock."""
    if not boot(g) or not to_mission(g):
        return
    m = 0
    got = []
    t0 = time.time()
    while len(got) < 4 and time.time() - t0 < 150:
        l = g.wait(r'\[QUICKSAVE\] Autosaved', 40, m)
        if not l:
            break
        got.append(l)
        m = g.mark()
    names = [l.split('(')[1].split(')')[0] for l in got if '(' in l]
    check('four autosaves in turn', names == ['Autosave 1', 'Autosave 2', 'Autosave 3', 'Autosave 1'], ', '.join(names))
    m = g.mark(); g.key('Escape', 3)
    time.sleep(25)
    check('none while paused', not g.since(m, r'\[QUICKSAVE\] Autosaved'))
    g.key('Escape', 3)
    shot('after')


g = Game()
try:
    {'front': front, 'menus': menus, 'zoom': zoom, 'wheel': wheel, 'wheel2': wheel2, 'levelwheel': levelwheel, 'audit': audit, 'brief': brief, 'hudprobe': hudprobe, 'hudclick': hudclick, 'uplink': uplink, 'quicksave': quicksave, 'autosave': autosave, 'dragbox': dragbox, 'groups': groups, 'pausekeys': pausekeys, 'frontcontrols': frontcontrols, 'quitgame': quitgame, 'pcsettings': pcsettings, 'movieskip': movieskip, 'keylist': keylist, 'padmouse': padmouse, 'wasd': wasd, 'holdleft': holdleft, 'rightdrag': rightdrag, 'rightdrag_angles': rightdrag_angles, 'rightdrag_jitter': rightdrag_jitter, 'rightdrag_gain': rightdrag_gain, 'moviemash': moviemash, 'moviestall': moviestall, 'rightdrag_circle': rightdrag_circle}[args.script](g)
    if args.script != 'quitgame':
        check('no crash', g.alive(), 'exit %s' % g.p.poll())
    check('frames advancing', time.time() - g.last_frame_t < 60, 'last frame %d' % g.last_frame)
finally:
    g.stop()
    if not args.keep:
        shutil.rmtree(saves, ignore_errors=True)
    else:
        note('saves kept in %s' % saves)
print()
for n, ok, d in results:
    print('%s  %s  %s' % ('PASS' if ok else 'FAIL', n, d))
sys.exit(0 if results and all(ok for _, ok, _ in results) else 1)
