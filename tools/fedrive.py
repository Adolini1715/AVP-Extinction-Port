#!/usr/bin/env python3
"""Drive the game through menus by their text while verify_multi captures it.

    fedrive.py --script NAME [--vas VA[:n],VA,...] [--seed DIR] [--timeout S]

Natural-call verification compares each call with the oracle run from that
call's own entry state, so the run does not have to be deterministic: a live
driver can steer it. This starts verify_multi (the functions in --vas, or none
with --explore-only) with a fresh private save dir (a copy of --seed when
given) and a pad file, then runs the named script, which reads the game's log
([MENU] focus/activate, [LEVEL], [BRIEFING], [PAUSE]) and queues pad presses
by menu text, as recomp/campaign.py does. When the script ends the game is
stopped and verify_multi checks what was captured.

Scripts (SCRIPTS below): explore_pause (lists the pause menu), save_load
(save in Marine 1, quit, load it from the main menu).
Every focused item is printed as "focus: <text>" so new scripts can be written
from a run's output.
"""
import argparse, os, re, shutil, subprocess, sys, tempfile, threading, time

OUT = '/home/claude/avpe-recomp'
AVPE = '/home/claude/avpe'
LOG = OUT + '/verify/last_multi_run.log'
# file, clock, save-container and sound calls the oracle cannot run (as verify_fe.sh)
EXTERNS = '141090,14E0C0,14DE5D,143AE8,1329C0,132AC0,132A50,132C40,132C60,132C90,13ED70,13ECC0,13EBB0,1432B8,132780,1326F0,12DF90,E1620,E1520,13F0E0'

ap = argparse.ArgumentParser()
ap.add_argument('--script', required=True)
ap.add_argument('--vas', default='')
ap.add_argument('--seed', default='')
ap.add_argument('--timeout', type=int, default=900)
ap.add_argument('--extern', default='')
args = ap.parse_args()


def note(msg):
    print('[%s] %s' % (time.strftime('%H:%M:%S'), msg), flush=True)


class Game:
    KEEP = ('[MENU]', '[PAUSE]', '[CHEAT]', '[LEVEL]', '[BRIEFING]', '[FRAME]', '[PAD]', '[SAVE]', '[LOAD]')

    def __init__(self, pad):
        self.pad, self.lines, self.off, self.queued, self.seen_focus = pad, [], 0, 0, None

    def poll(self):
        try:
            with open(LOG, errors='ignore') as f:
                f.seek(self.off); data = f.read(); self.off = f.tell()
        except FileNotFoundError:
            return
        for l in data.splitlines():
            if l.startswith(self.KEEP):
                self.lines.append(l)
                if l.startswith('[MENU] focus'):
                    t = self.text(l)
                    if t != self.seen_focus:
                        note('focus: %s' % t); self.seen_focus = t

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
            time.sleep(0.3)
        return None

    def press(self, spec, gap=1.2):
        """Queue a press, wait until the game has read it, then <gap> seconds."""
        with open(self.pad, 'a') as f:
            f.write(spec + '\n')
        self.queued += 1
        t0 = time.time()
        while time.time() - t0 < 60:
            self.poll()
            if sum(1 for l in self.lines if l.startswith('[PAD] press')) >= self.queued:
                break
            time.sleep(0.1)
        time.sleep(gap); self.poll()

    def focus(self):
        self.poll()
        for l in reversed(self.lines):
            if l.startswith('[MENU] focus'):
                return self.text(l)
        return ''

    def goto(self, text, dirs=('down', 'up', 'right', 'left'), max_each=12):
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

    def walk(self, d='down', n=14):
        """Press <d> until focus stops changing or comes back round; returns the items seen."""
        seen = [self.focus()]
        for _ in range(n):
            m = self.mark(); self.press(d + ':200')
            if not self.since(m, r'^\[MENU\] focus'):
                break
            f = self.focus()
            if f in seen:
                break
            seen.append(f)
        return seen


# ---- scripts --------------------------------------------------------------

def to_m01(g):
    """Boot on a fresh disk to Marine mission 1 (as recomp/nav_m01.sh), briefing closed."""
    if not g.wait(r'mainmenu\.tbd|\[MENU\] focus', 240):
        note('no main menu'); return False
    time.sleep(6)
    g.press('start:300', 5)
    for _ in range(6):
        g.press('a:300', 4)
    if not g.wait(r'\[BRIEFING\] mission goals up', 300):
        note('no briefing'); return False
    time.sleep(3); g.press('start:400', 3)
    return bool(g.wait(r'\[BRIEFING\] closed', 120))


def skip_intro(g):
    """The mission intro after the briefing shows 'Press to Skip Intro'; start skips it."""
    for _ in range(4):
        if 'Skip' not in g.focus():
            return True
        g.press('start:300', 3)
    return 'Skip' not in g.focus()


def pause(g):
    """Open the pause menu: a focused item with real text (not the intro prompt or a blank)."""
    for _ in range(3):
        m = g.mark(); g.press('start:300', 3)
        for l in g.since(m, r'^\[MENU\] focus'):
            t = g.text(l)
            if t and 'Skip' not in t:
                return True
    return False


def explore_pause(g):
    if not to_m01(g):
        return
    time.sleep(5)
    if not skip_intro(g):
        note('could not skip the intro'); return
    time.sleep(5)
    if not pause(g):
        note('could not pause'); return
    note('pause menu: %s' % ' | '.join(g.walk('down')))


SCRIPTS = {'explore_pause': explore_pause}

# ---- run ---------------------------------------------------------------------
sd = tempfile.mkdtemp(prefix='avpe-fedrive-saves.')
if args.seed:
    shutil.copytree(args.seed, sd, dirs_exist_ok=True)
pad = tempfile.mktemp(prefix='avpe-fedrive-pad.')
open(pad, 'w').close()
vas = [v for v in args.vas.split(',') if v] or ['110E0:999999']     # nothing to check: a call that never comes
under_test = {v.split(':')[0].upper().lstrip('0') for v in vas}       # a function being checked is not an extern
ext = [x for x in (EXTERNS + (',' + args.extern if args.extern else '')).split(',') if x.upper().lstrip('0') not in under_test]
env = dict(os.environ, AVPE_SAVE_DIR=sd, VERIFY_EXTERN=','.join(ext))
if not args.vas:
    env['AVPE_NO_RECORD'] = '1'                                   # exploring only: the placeholder check records nothing
if os.path.exists(LOG):
    os.remove(LOG)
lock = open('/tmp/avpe-pipeline.lock', 'w')
import fcntl
fcntl.flock(lock, fcntl.LOCK_EX)
vm = subprocess.Popen(['python3', AVPE + '/tools/verify_multi.py'] + vas + ['--pad', pad, '--timeout', str(args.timeout)],
                      cwd=AVPE, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
g = Game(pad)
try:
    SCRIPTS[args.script](g)
finally:
    note('script done; stopping the game')
    time.sleep(2)
    subprocess.call(['pkill', '-x', 'avpe_recomp'])
    out = vm.communicate()[0]
    fcntl.flock(lock, fcntl.LOCK_UN)
    print(out)
    shutil.rmtree(sd, ignore_errors=True)
    os.remove(pad)
