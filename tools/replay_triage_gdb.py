# gdb script for replay_triage.py (not run directly). Env: TRIAGE_WORDS (comma
# separated guest VAs, at most 4), TRIAGE_OUT (log file). Logs every change of
# those guest words with the game frame and the call chain, then lets the run
# finish (AVPE_REPLAY_FRAMES ends it).
import gdb, os, re
words = [int(w, 16) for w in os.environ['TRIAGE_WORDS'].split(',')]
out = open(os.environ['TRIAGE_OUT'], 'w')
gdb.execute('set disable-randomization off'); gdb.execute('set pagination off'); gdb.execute('set confirm off')
for sig in ('SIGUSR1', 'SIGUSR2', 'SIG34', 'SIG35', 'SIG36', 'SIGPIPE', 'SIGALRM'):
    gdb.execute('handle %s nostop noprint pass' % sig)
def name_of(fr):
    n = fr.name()
    if n: return n
    try:
        s = gdb.execute('info symbol 0x%x' % fr.pc(), to_string=True)
        return s.split(' in ')[0].split(' + ')[0].strip()
    except gdb.error:
        return '0x%x' % fr.pc()
class W(gdb.Breakpoint):
    def __init__(self, va, off):
        self.va = va; self.last = None
        gdb.Breakpoint.__init__(self, '*(unsigned int*)0x%x' % (va + off), gdb.BP_WATCHPOINT, gdb.WP_WRITE, internal=True)
    def stop(self):
        v = int(gdb.parse_and_eval('*(unsigned int*)0x%x' % (self.va + OFF))) & 0xFFFFFFFF
        if v == self.last: return False
        old, self.last = self.last, v
        fr = gdb.newest_frame(); chain = []
        while fr is not None and len(chain) < 24:
            chain.append(name_of(fr)); fr = fr.older()
        frame = int(gdb.parse_and_eval('g_avpe_frames'))
        out.write('W %08X frame %d %s -> %08X | %s\n' % (self.va, frame, '%08X' % old if old is not None else '--------', v, ' < '.join(chain)))
        out.flush()
        return False
class Setup(gdb.Breakpoint):
    def stop(self): return True
# wait for the guest memory to exist: first QueryPerformanceCounter
Setup('sub_00141090', internal=True)
gdb.execute('run')
OFF = int(gdb.parse_and_eval('g_xbox_mem_offset'))
for b in list(gdb.breakpoints()): b.delete()
ws = [W(w, OFF) for w in words]
for w in ws: w.last = int(gdb.parse_and_eval('*(unsigned int*)0x%x' % (w.va + OFF))) & 0xFFFFFFFF
out.write('base offset %x\n' % OFF); out.flush()
gdb.execute('continue')
out.write('end\n'); out.close()
gdb.execute('quit')
