/* Deterministic replay (notes/replay.md). Included at the end of
 * avpe_bridge.c; everything is off unless AVPE_REPLAY is set.
 *
 *   AVPE_REPLAY=fixed          virtual clock: QueryPerformanceCounter (0x141090)
 *                              returns a counter that the game thread advances
 *                              by AVPE_REPLAY_STEP ticks per call (default
 *                              733333 = 1 ms at the Xbox's 733 MHz); other
 *                              threads read it without advancing it.
 *   AVPE_REPLAY_PAD=<file>     scripted pad keyed on the game thread's pad poll
 *                              count, not wall time. Lines:
 *                                <poll> <buttons>[:<hold polls>]
 *                                B+<n> <buttons>[:<hold polls>]   (n polls after
 *                                                  the mission briefing went up)
 *                              buttons as AVPE_PAD (a+b, lleft, start ...);
 *                              default hold 5 polls; '#' starts a comment.
 *   AVPE_REPLAY_OUT=<file>     every AVPE_REPLAY_EVERY frames (default 50), at
 *                              D3DDevice_Swap: one record of 64-bit hashes of
 *                              each 4 KB page of guest RAM (tools/replay_cmp.py).
 *                              Hashed as zeros: the game thread's dead stack
 *                              (AVPE_REPLAY_STACK, default F70000, up to esp)
 *                              and AVPE_REPLAY_EXCLUDE ranges, "lo-hi,..." hex,
 *                              hi exclusive (default: 740020-740024 KeTickCount,
 *                              host real time; 2E01E2-2E01E6, 4 bytes the pad
 *                              poll 0x136C80 copies from past its XINPUT_STATE,
 *                              uninitialised stack in the original too).
 *   AVPE_REPLAY_FRAMES=<n>     exit(0) after frame n.
 *   AVPE_REPLAY_SNAP=<frame>:<path>  full RAM snapshot at that frame.
 */
#include <pthread.h>
void avpe_coverage_dump(const char *tag);
static int s_rp_on = -1;
static uint64_t s_rp_tsc = 0x100000000ull, s_rp_step = 733333;
static pthread_t s_rp_thread; static int s_rp_have_thread;
uint32_t g_avpe_pad_polls, g_avpe_briefing_poll;
static int replay_on(void)
{
    if (s_rp_on < 0) {
        s_rp_on = getenv("AVPE_REPLAY") != NULL;
        const char *s = getenv("AVPE_REPLAY_STEP");
        if (s) s_rp_step = strtoull(s, NULL, 0);
        if (s_rp_on) fprintf(stderr, "[REPLAY] fixed clock, %llu ticks per QueryPerformanceCounter\n", (unsigned long long)s_rp_step);
    }
    return s_rp_on;
}
static int replay_game_thread(void)
{
    if (!s_rp_have_thread) { s_rp_thread = pthread_self(); s_rp_have_thread = 1; }
    return pthread_equal(pthread_self(), s_rp_thread);
}
/* QueryPerformanceCounter body: the counter at [esp+4], returns 1, ret 4 */
static void replay_qpc(void)
{
    uint32_t p = MEM32(g_esp + 4);
    uint64_t v;
    if (replay_on()) {
        if (replay_game_thread()) { s_rp_tsc += s_rp_step; v = s_rp_tsc; }
        else v = __atomic_load_n(&s_rp_tsc, __ATOMIC_RELAXED);
        /* the original (0x141090) is one rdtsc: report the value like the kernel's
         * timestamp read does, so the oracle's rdtsc returns what the game saw */
        extern void (*xbox_tsc_observer)(uint64_t);
        if (xbox_tsc_observer) xbox_tsc_observer(v);
    } else {
        extern uint64_t xbox_ReadTimeStampCounter(void);
        v = xbox_ReadTimeStampCounter();
    }
    MEM32(p) = (uint32_t)v; MEM32(p + 4) = (uint32_t)(v >> 32);
    g_ecx = p; g_edx = (uint32_t)(v >> 32); g_eax = 1;
    g_esp += 8;
}

/* ---- scripted pad ---- */
typedef struct { int rel; uint32_t at, hold; uint16_t buttons; uint8_t analog[8]; int16_t s[4]; } rp_ev;
static rp_ev *s_rp_ev; static int s_rp_nev = -1;
static void replay_pad_load(void)
{
    s_rp_nev = 0;
    const char *path = getenv("AVPE_REPLAY_PAD");
    FILE *f = path ? fopen(path, "r") : NULL;
    if (!f) return;
    char line[160];
    while (fgets(line, sizeof line, f)) {
        char *h = strchr(line, '#'); if (h) *h = 0;
        char when[32], spec[120];
        if (sscanf(line, "%31s %119s", when, spec) != 2) continue;
        rp_ev e; memset(&e, 0, sizeof e); e.hold = 5;
        if (when[0] == 'B' && when[1] == '+') { e.rel = 1; e.at = (uint32_t)atol(when + 2); }
        else e.at = (uint32_t)atol(when);
        char *c = strchr(spec, ':'); if (c) { *c = 0; e.hold = (uint32_t)atol(c + 1); }
        static const char *dig[] = { "up", "down", "left", "right", "start", "back", "lthumb", "rthumb" };
        static const char *ana[] = { "a", "b", "x", "y", "black", "white", "lt", "rt" };
        for (char *t = strtok(spec, "+"); t; t = strtok(NULL, "+")) {
            for (int i = 0; i < 8; i++) if (!strcmp(t, dig[i])) e.buttons |= 1u << i;
            for (int i = 0; i < 8; i++) if (!strcmp(t, ana[i])) e.analog[i] = 0xFF;
            if (!strcmp(t, "lleft")) e.s[0] = -32768; if (!strcmp(t, "lright")) e.s[0] = 32767;
            if (!strcmp(t, "lup")) e.s[1] = 32767;    if (!strcmp(t, "ldown")) e.s[1] = -32768;
            if (!strcmp(t, "rleft")) e.s[2] = -32768; if (!strcmp(t, "rright")) e.s[2] = 32767;
            if (!strcmp(t, "rup")) e.s[3] = 32767;    if (!strcmp(t, "rdown")) e.s[3] = -32768;
        }
        s_rp_ev = realloc(s_rp_ev, sizeof(rp_ev) * (s_rp_nev + 1));
        s_rp_ev[s_rp_nev++] = e;
    }
    fclose(f);
    fprintf(stderr, "[REPLAY] %d pad events from %s\n", s_rp_nev, path);
}
/* Called from XInputGetState: returns 1 when the replay script owns the pad
 * (state filled in), 0 to leave the live AVPE_PAD path in charge. */
static int replay_pad(uint16_t *buttons, uint8_t analog[8], int16_t sticks[4])
{
    if (s_rp_nev < 0) replay_pad_load();
    if (!s_rp_nev || !replay_game_thread()) return 0;
    uint32_t n = g_avpe_pad_polls++;
    *buttons = 0; memset(analog, 0, 8); memset(sticks, 0, 8);
    for (int i = 0; i < s_rp_nev; i++) {
        rp_ev *e = &s_rp_ev[i];
        if (e->rel && !g_avpe_briefing_poll) continue;
        uint32_t at = e->rel ? g_avpe_briefing_poll + e->at : e->at;
        if (n < at || n >= at + e->hold) continue;
        *buttons = e->buttons; memcpy(analog, e->analog, 8); memcpy(sticks, e->s, 8);
        if (n == at) fprintf(stderr, "[REPLAY] pad event %d at poll %u (frame %u)\n", i, n, g_avpe_frames);
        break;
    }
    return 1;
}

/* ---- state hashes at frame boundaries ---- */
static uint64_t rp_page_hash(const uint64_t *p)
{
    uint64_t h = 0x9E3779B97F4A7C15ull;
    for (int i = 0; i < 512; i++) { h ^= p[i]; h *= 0x100000001B3ull; h ^= h >> 29; }
    return h;
}
static uint32_t s_rp_ex[64][2]; static int s_rp_nex = -1;
static void replay_ex_load(void)
{
    const char *e = getenv("AVPE_REPLAY_EXCLUDE");
    if (!e) e = "740020-740024,2E01E2-2E01E6";
    s_rp_nex = 1;                                   /* [0] is the dead stack, set each frame */
    while (*e && s_rp_nex < 64) {
        char *q; uint32_t lo = (uint32_t)strtoul(e, &q, 16), hi = lo + 4;
        if (*q == '-') hi = (uint32_t)strtoul(q + 1, &q, 16);
        if (hi > lo) { s_rp_ex[s_rp_nex][0] = lo; s_rp_ex[s_rp_nex][1] = hi; s_rp_nex++; }
        e = *q == ',' ? q + 1 : q;
        if (q == e && *e != ',' && *q) break;
    }
}
static uint64_t rp_page(uint32_t va)
{
    static uint64_t buf[512]; int copied = 0;
    for (int r = 0; r < s_rp_nex; r++) {
        uint32_t lo = s_rp_ex[r][0], hi = s_rp_ex[r][1];
        if (hi <= va || lo >= va + 4096) continue;
        if (!copied) { memcpy(buf, (const void *)XBOX_PTR(va), 4096); copied = 1; }
        uint32_t a = lo > va ? lo : va, b = hi < va + 4096 ? hi : va + 4096;
        memset((uint8_t *)buf + (a - va), 0, b - a);
    }
    return rp_page_hash(copied ? buf : (const uint64_t *)XBOX_PTR(va));
}
static void replay_frame(void)
{
    static FILE *out; static long every = -2, stop = -2, snapf = -2; static const char *snapp;
    if (every == -2) {
        const char *e = getenv("AVPE_REPLAY_EVERY"); every = e ? atol(e) : 50;
        e = getenv("AVPE_REPLAY_FRAMES"); stop = e ? atol(e) : 0;
        e = getenv("AVPE_REPLAY_SNAP"); snapf = e ? atol(e) : 0; snapp = e ? strchr(e, ':') : NULL;
        if (snapp) snapp++;
        e = getenv("AVPE_REPLAY_OUT"); if (e) out = fopen(e, "wb");
    }
    if (!replay_on()) return;
    replay_game_thread();
    if (out && every > 0 && g_avpe_frames % every == 0) {
        static uint64_t h[0x04000000u / 4096];
        if (s_rp_nex < 0) replay_ex_load();
        { const char *st = getenv("AVPE_REPLAY_STACK"); uint32_t lo = st ? (uint32_t)strtoul(st, NULL, 16) : 0x00F70000u;
          s_rp_ex[0][0] = lo; s_rp_ex[0][1] = g_esp > lo && g_esp - lo < 0x100000u ? g_esp : lo; }
        for (uint32_t i = 0; i < 0x04000000u / 4096; i++) h[i] = rp_page(i * 4096u);
        uint32_t hdr[8] = { 0x48535052u /* RPSH */, g_avpe_frames, 0x04000000u / 4096, g_avpe_pad_polls,
                            (uint32_t)s_rp_tsc, (uint32_t)(s_rp_tsc >> 32), g_avpe_briefing_poll, 0 };
        fwrite(hdr, 4, 8, out); fwrite(h, 8, 0x04000000u / 4096, out); fflush(out);
    }
    if (snapp && (long)g_avpe_frames == snapf) avpe_snapshot(snapp);
    if (stop > 0 && (long)g_avpe_frames >= stop) {
        if (out) fclose(out);
        fprintf(stderr, "[REPLAY] done at frame %u (polls %u, clock %llX)\n", g_avpe_frames, g_avpe_pad_polls, (unsigned long long)s_rp_tsc);
        fflush(stderr);
        avpe_coverage_dump("exit");      /* coverage build only (weak no-op otherwise) */
        _exit(0);
    }
}
