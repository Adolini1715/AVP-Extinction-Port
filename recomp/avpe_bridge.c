/*
 * AvP: Extinction - hand-written overrides for the recompiled build.
 *
 * Every function defined here replaces the generated body of the same name
 * (tools.recomp --exclude-manual reads this file). A wrapper that calls
 * sub_X_gen keeps the lifted body and runs around it.
 *
 * Guest calling convention reminder: on entry g_esp points at the return
 * address the caller pushed; stdcall arguments follow at g_esp+4, +8, ...
 * A replacement (not a wrapper) must pop them itself: g_esp += 4 + 4*argc.
 */
#include <stdio.h>
#include <stdint.h>
#include <string.h>
#include <stdlib.h>
#include "recomp_types.h"

#ifndef _WIN32
#include <execinfo.h>
#endif

/* AVPE_WATCH=<hex guest va>: poll a guest word at every allocator call and
 * every presented frame; report each change with the host call stack. Finds
 * writes a hardware watchpoint cannot see (file reads land through the kernel). */
extern uint32_t g_avpe_frames;
static void avpe_watch_poll(const char *where)
{
    static int init; static uint32_t va, last; static unsigned changes;
    if (!init) { const char *w = getenv("AVPE_WATCH"); va = w ? (uint32_t)strtoul(w, NULL, 16) : 0; if (va) last = MEM32(va); init = 1; }
    if (!va) return;
    uint32_t v = MEM32(va);
    if (v == last) return;
    fprintf(stderr, "[WATCH] frame %u at %s: [%08X] %08X -> %08X\n", g_avpe_frames, where, va, last, v);
    if (++changes > 200) { last = v; return; }
#ifndef _WIN32
    void *bt[12]; int n = backtrace(bt, 12); backtrace_symbols_fd(bt + 1, n - 1, 2);
#endif
    last = v;
}

static int trace_on(void) { static int v = -1; if (v < 0) v = getenv("AVPE_TRACE") != NULL; return v; }

static void dump_guest_text(const char *tag, uint32_t va, uint32_t len)
{
    char buf[161]; uint32_t n = len < 160 ? len : 160;
    if (!va) { fprintf(stderr, "%s <null>\n", tag); return; }
    for (uint32_t i = 0; i < n; i++) { char c = (char)MEM8(va + i); buf[i] = (c == '\n' || c == '\r') ? ' ' : c; }
    buf[n] = 0;
    fprintf(stderr, "%s %.160s%s\n", tag, buf, len > 160 ? "..." : "");
}

/* Header words 13-15: the x87 stack top (word 13, | 0x100 as "present")
 * and st(0) as a double (words 14-15), for float results and arguments. */
static void avpe_fp_header(uint32_t *hdr)
{
    double v = g_fp_stack[g_fp_top & 7]; uint64_t b; memcpy(&b, &v, 8);
    hdr[13] = 0x100u | (uint32_t)(g_fp_top & 7); hdr[14] = (uint32_t)b; hdr[15] = (uint32_t)(b >> 32);
}
/* Snapshot of guest state for the Unicorn oracle (tools/snap_replay.py):
 * header (magic, 8 GPRs, fs base) then guest RAM 0..64 MB verbatim. */
extern RECOMP_TLS uint32_t g_ebp;
static uint32_t g_avpe_snap_extra;
static void avpe_snapshot(const char *path)
{
    FILE *f = fopen(path, "wb");
    if (!f) return;
    /* v2 header (16 words): magic, 8 GPRs, fs base, RAM size, extra (callee
     * argument count at a verify boundary), x87 control word, 3 spare. */
    extern RECOMP_TLS uint16_t g_fp_control_word;
    uint32_t hdr[16] = { 0x32414E53u /* SNA2 */, g_eax, g_ecx, g_edx, g_ebx, g_esp, g_ebp, g_esi, g_edi, 0x4000u,
                         0x04000000u, g_avpe_snap_extra, g_fp_control_word, 0, 0, 0 };
    avpe_fp_header(hdr);
    fwrite(hdr, 4, 16, f);
    fwrite((const void *)XBOX_PTR(0), 1, 0x04000000u, f);
    fclose(f);
    {   /* sidecar: the whole x87 stack, st(0)..st(7) as doubles, for functions that take two
         * or more arguments on it (_CIpow: tools/snap_replay.py --fp-in2) */
        char fp[1024]; snprintf(fp, sizeof fp, "%s.fp", path);
        FILE *g = fopen(fp, "wb");
        if (g) { for (int i = 0; i < 8; i++) fwrite(&g_fp_stack[(g_fp_top + i) & 7], 8, 1, g); fclose(g); }
    }
    fprintf(stderr, "[SNAP] wrote %s (esp=%08X)\n", path, g_esp);
}

/* Deterministic replay: fixed clock, scripted pad, state hashes (AVPE_REPLAY). */
#include <unistd.h>
extern uint32_t g_avpe_frames;
#include "avpe_replay.c"      /* recomp/, on the include path */

/* A wrapper of a game function calls the decompiled original (avpe_dec_X) when
 * there is one, else the lifted body (no verify brackets: the C++ has its own). */
#define AVPE_DEC_ELSE_GEN(V) do { extern void avpe_dec_##V(void) __attribute__((weak)); \
    if (avpe_dec_##V) avpe_dec_##V(); else sub_##V##_gen(); } while (0)
/* xdk_QueryPerformanceCounter(LARGE_INTEGER *): the virtual clock under AVPE_REPLAY, else rdtsc */
void sub_00141090(void) { replay_qpc(); }
/* xdk_CreateThread(attr, stack, start, param, flags, id), stdcall 6: under
 * AVPE_REPLAY two short-lived threads whose create/exit race the main thread
 * (thread stacks and heap shift by a page when an exit lands late) are not
 * started: the load watchdog (0xDE9B0, real-time Sleep loop on 0x2F6850) and,
 * with the bridge's skipped movies, the XMV worker (0x120DE0), whose
 * "finished" result is written here at once. Both return a null handle. */
extern void sub_00142EF2_gen(void);
void sub_00142EF2(void)
{
    uint32_t start = MEM32(g_esp + 12), param = MEM32(g_esp + 16);
    if (replay_on() && (start == 0x000DE9B0u || (start == 0x00120DE0u && !getenv("AVPE_REAL_XMV")))) {
        if (start == 0x00120DE0u) { MEM32(param + 0x200) = 0; MEM32(param + 0x204) = 1; }
        fprintf(stderr, "[REPLAY] thread %08X not started (frame %u)\n", start, g_avpe_frames);
        g_eax = 0; g_esp += 4 + 24; return;
    }
    AVPE_DEC_ELSE_GEN(00142EF2);
}
/* xdk_XAutoPowerDownResetTimer: KeSetTimer on the auto power-down timer (fires
 * after minutes idle). Its first call starts the runtime's host timer thread,
 * which allocates its TIB from the guest heap whenever it gets scheduled: one
 * page of the heap moves in about a third of runs. Under AVPE_REPLAY the timer
 * is not set, so that thread (and its real-time DPCs) never starts. */
extern void sub_001464CF_gen(void);
void sub_001464CF(void)
{
    if (replay_on()) { g_eax = 0; g_esp += 4; return; }
    AVPE_DEC_ELSE_GEN(001464CF);
}

/* XGraphics shader assembler wrapper (11 stdcall args, ret 44).
 * a1 file name, a2 source text, a3 length, a4 flags, a6 = out compiled XGBUFFER*. */
extern void sub_0018B1FD_gen(void);
void sub_0018B1FD(void)
{
    uint32_t sp = g_esp;
    uint32_t a1 = MEM32(sp + 4), a2 = MEM32(sp + 8), a3 = MEM32(sp + 12), a4 = MEM32(sp + 16), a6 = MEM32(sp + 24);
    static int n;
    n++;
    if (trace_on() || n <= 3) {
        fprintf(stderr, "[XGASM #%d] file=0x%08X src=0x%08X len=%u flags=0x%X out=0x%08X\n", n, a1, a2, a3, a4, a6);
        dump_guest_text("[XGASM]   src:", a2, a3);
    }
    if (getenv("AVPE_DUMP_XGASM") && a2 && a3 < 1000000) {
        char path[256]; snprintf(path, sizeof path, "%s/xgasm_%02d.vsh", getenv("AVPE_DUMP_XGASM"), n);
        FILE *f = fopen(path, "wb");
        if (f) { for (uint32_t i = 0; i < a3; i++) fputc(MEM8(a2 + i), f); fclose(f); }
    }
    if (getenv("AVPE_SNAPSHOT") && n == 1) avpe_snapshot(getenv("AVPE_SNAPSHOT"));
    AVPE_DEC_ELSE_GEN(0018B1FD);
    if (a6 && MEM32(a6)) {                       /* the PC renderer compiles the text, not the microcode */
        extern void avpe_pc_shader_text(uint32_t microcode, uint32_t src, uint32_t len);
        avpe_pc_shader_text(MEM32(MEM32(a6) + 4), a2, a3);
    }
    if (getenv("AVPE_SNAPSHOT") && n == 1) {
        uint32_t buf = a6 ? MEM32(a6) : 0;
        FILE *f = fopen("/home/claude/avpe-recomp/snap_result_lifted.txt", "w");
        if (f) { fprintf(f, "eax=%08X esp=%08X out=%08X buf=%08X\n", g_eax, g_esp, a6, buf); fclose(f); }
    }
    if (trace_on() || n <= 3 || g_eax) {
        uint32_t buf = a6 ? MEM32(a6) : 0;
        fprintf(stderr, "[XGASM #%d] -> hr=0x%08X compiled=0x%08X", n, g_eax, buf);
        if (buf) fprintf(stderr, " [%08X %08X %08X]", MEM32(buf), MEM32(buf + 4), MEM32(buf + 8));
        fprintf(stderr, "\n");
    }
}

/* D3DDevice_Swap (XDK, identified by XbSymbolDatabase): count presented frames. */
extern void sub_00175CE0_gen(void);
uint32_t g_avpe_frames;
extern int g_avpe_tel_on;                                       /* platform/pc_telemetry.cpp: read-only logger */
extern void avpe_tel_enter(uint32_t va), avpe_tel_exit(uint32_t va), avpe_tel_frame(void), avpe_bot_frame(void);
extern int g_avpe_diff_on;                                      /* platform/pc_difficulty.cpp: optional difficulty layer (AVPE_DIFF_*) */
extern void avpe_diff_enter(uint32_t va), avpe_diff_exit(uint32_t va), avpe_diff_frame(void);
void sub_00175CE0(void)
{
    g_avpe_frames++;
    {   /* AVPE_FPTOP: the x87 top at every Swap when it changes (both builds: lifted shows the original's own behaviour) */
        static int on = -1, last = 0;
        if (on < 0) on = getenv("AVPE_FPTOP") != NULL;
        if (on && (g_fp_top & 7) != last) { last = g_fp_top & 7; fprintf(stderr, "[FPTOP] swap frame %u top %d\n", g_avpe_frames, last); }
    }
    avpe_diff_frame();
    avpe_bot_frame();                                           /* AVPE_BOT: the engine's AI commander plays player 1 */
    { extern void avpe_soak_frame(void); avpe_soak_frame(); }  /* AVPE_SOAK: timed real-time run with frame stats (platform/pc_soak.cpp) */
    avpe_tel_frame();                                           /* read-only telemetry (AVPE_TELEMETRY), before replay_frame may exit */
    replay_frame();
    avpe_watch_poll("Swap");
    { extern void avpe_probe_tick(void); avpe_probe_tick(); }
    { extern void avpe_mission_tick(void); avpe_mission_tick(); }
    /* AVPE_DUMP_SURF=<every N frames>: raw dump of the colour surface as the
     * GPU executor leaves it (AVPE_SURF_VA, W, H, PITCH override the guess). */
    {
        static long every = -2;
        if (every == -2) { const char *e = getenv("AVPE_DUMP_SURF"); every = e ? atol(e) : 0; }
        if (every > 0 && g_avpe_frames % every == 0) {
            const char *e;
            uint32_t va = (e = getenv("AVPE_SURF_VA")) ? (uint32_t)strtoul(e, NULL, 16) : 0x80084000u;
            uint32_t w = (e = getenv("AVPE_SURF_W")) ? (uint32_t)atol(e) : 1280;
            uint32_t h = (e = getenv("AVPE_SURF_H")) ? (uint32_t)atol(e) : 480;
            uint32_t pitch = (e = getenv("AVPE_SURF_PITCH")) ? (uint32_t)atol(e) : 5120;
            char path[128]; snprintf(path, sizeof path, "/home/claude/avpe-recomp/surf/s%05u.raw", g_avpe_frames);
            FILE *f = fopen(path, "wb");
            if (f) {
                uint32_t hdr[4] = { w, h, pitch, va }; fwrite(hdr, 4, 4, f);
                for (uint32_t y = 0; y < h; y++) fwrite((const void *)XBOX_PTR(va + y * pitch), 1, w * 4, f);
                fclose(f);
            }
        }
    }
    if (g_avpe_frames <= 5 || (g_avpe_frames % 100) == 0)
        fprintf(stderr, "[FRAME] D3DDevice_Swap #%u\n", g_avpe_frames);
    { extern void avpe_pc_present(void); avpe_pc_present(); }   /* PC renderer (platform/pc_render.cpp) */
    AVPE_DEC_ELSE_GEN(00175CE0);
}

/* ---- Direct3D call trace (AVPE_D3DTRACE=<frames>): every call the game
 * makes into the XDK graphics library, with registers and the first stack
 * words, for the first N presented frames. What the PC back end must do. */
static int d3dtrace_on(void)                 /* AVPE_D3DTRACE=<frames> or <first>:<frames> */
{
    static long a = -2, n;
    if (a == -2) { const char *e = getenv("AVPE_D3DTRACE"), *c = e ? strchr(e, ':') : NULL;
                   a = c ? atol(e) : 0; n = e ? atol(c ? c + 1 : e) : 0; }
    return n > 0 && g_avpe_frames >= (uint32_t)a && g_avpe_frames < (uint32_t)(a + n);
}
static void d3dtrace(const char *name)
{
    uint32_t s = g_esp;
    fprintf(stderr, "[D3D] f%u %s ecx=%08X edx=%08X eax=%08X args %08X %08X %08X %08X %08X %08X ret=%08X\n", g_avpe_frames, name,
            g_ecx, g_edx, g_eax, MEM32(s + 4), MEM32(s + 8), MEM32(s + 12), MEM32(s + 16), MEM32(s + 20), MEM32(s + 24), MEM32(s));
}
extern void sub_00170F10_gen(void);
void sub_00170F10(void) { if (d3dtrace_on()) d3dtrace("D3DResource_Release"); AVPE_DEC_ELSE_GEN(00170F10); }
extern void sub_00170FF0_gen(void);
void sub_00170FF0(void) { if (d3dtrace_on()) d3dtrace("D3DResource_Register"); AVPE_DEC_ELSE_GEN(00170FF0); }
extern void sub_00171090_gen(void);
void sub_00171090(void) { if (d3dtrace_on()) d3dtrace("D3DDevice_CreateTexture2"); AVPE_DEC_ELSE_GEN(00171090); }
extern void sub_00171C20_gen(void);
void sub_00171C20(void) { if (d3dtrace_on()) d3dtrace("D3DDevice_DeleteVertexShader"); AVPE_DEC_ELSE_GEN(00171C20); }
extern void sub_00171EA0_gen(void);
void sub_00171EA0(void) { if (d3dtrace_on()) d3dtrace("sub_00171EA0"); AVPE_DEC_ELSE_GEN(00171EA0); }
extern void sub_001721B0_gen(void);
void sub_001721B0(void) { if (d3dtrace_on()) d3dtrace("D3DDevice_SetGammaRamp"); AVPE_DEC_ELSE_GEN(001721B0); }
extern void sub_00173400_gen(void);
void sub_00173400(void) { if (d3dtrace_on()) d3dtrace("D3DDevice_CreateIndexBuffer2"); AVPE_DEC_ELSE_GEN(00173400); }
extern void sub_00173440_gen(void);
void sub_00173440(void) { if (d3dtrace_on()) d3dtrace("D3DDevice_CreateVertexBuffer2"); AVPE_DEC_ELSE_GEN(00173440); }
extern void sub_00173490_gen(void);
void sub_00173490(void) { if (d3dtrace_on()) d3dtrace("D3DVertexBuffer_Lock2"); AVPE_DEC_ELSE_GEN(00173490); }
extern void sub_001762C0_gen(void);
void sub_001762C0(void) { if (d3dtrace_on()) d3dtrace("D3D_FreeContiguousMemory?"); AVPE_DEC_ELSE_GEN(001762C0); }
extern void sub_0018B231_gen(void);
void sub_0018B231(void) { if (d3dtrace_on()) d3dtrace("XGSetVertexBufferHeader"); AVPE_DEC_ELSE_GEN(0018B231); }

/* D3D_BlockOnTime(time, flags), stdcall, ret 8 (XbSymbolDatabase).
 * Minimal graphics bridge: Direct3D waits here for the GPU to write back the
 * time stamp of a pushbuffer position. The stand-in NV2A acknowledges commands
 * (PUT==GET) but never executes them, so the stamp never advances. Treat the
 * GPU as finished: nothing consumes the commands yet, so nothing can race. */
uint32_t g_avpe_gpu_waits;
extern void sub_001767F0_gen(void);
void sub_001767F0(void)   /* xdk_D3D_BlockOnTime */
{
    /* With the GPU command executor running (RECOMP_PB_EXEC) the stamp does
     * advance, and returning early lets D3D overwrite pushbuffer space the
     * executor has not consumed. So with the executor on, the original wait
     * runs (the executor writes the semaphore it polls); AVPE_FAKE_GPU_WAIT=1
     * forces the old immediate return. */
    static int real = -1;
    if (real < 0) real = (getenv("RECOMP_PB_EXEC") && !getenv("AVPE_FAKE_GPU_WAIT"))
                         || getenv("AVPE_REAL_BLOCKONTIME");
    if (real) {
        static int shown;
        if (shown++ < 4) {
            uint32_t dev = MEM32(0x1815E8u), fp = MEM32(dev + 0x30);
            fprintf(stderr, "[GPU] BlockOnTime(0x%08X): device %08X time %08X fence@%08X = %08X\n",
                    MEM32(g_esp + 4), dev, MEM32(dev + 0x2C), fp, fp ? MEM32(fp) : 0);
        }
        AVPE_DEC_ELSE_GEN(001767F0); return;
    }
    g_avpe_gpu_waits++;
    if (g_avpe_gpu_waits <= 3) fprintf(stderr, "[GPU] BlockOnTime(0x%08X, %u) -> immediate\n", MEM32(g_esp + 4), MEM32(g_esp + 8));
    g_esp += 12;
}

/* D3DDevice_BlockUntilVerticalBlank(), stdcall, no args (XbSymbolDatabase).
 * On hardware the NV2A vblank interrupt signals a KEVENT that Direct3D keeps
 * in guest memory; the runtime's KeWaitForSingleObject expects host objects
 * and faults on it. Pace at 60 Hz from the host instead. AVPE_NOVSYNC=1 skips
 * the sleep for headless measurement runs. */
#include <time.h>
uint32_t g_avpe_vblanks;
void sub_001723B0(void)   /* xdk_D3DDevice_BlockUntilVerticalBlank */
{
    g_avpe_vblanks++;
    if (!getenv("AVPE_NOVSYNC")) {
        /* Wait for the next vertical blank of a 60 Hz display: the next tick
         * of a fixed clock, not a full period from now (that added a whole
         * refresh to every frame: 25 ms of work became 42). */
        const uint64_t period = 16666667;
        struct timespec now;
        clock_gettime(CLOCK_MONOTONIC, &now);
        uint64_t t = (uint64_t)now.tv_sec * 1000000000ull + (uint64_t)now.tv_nsec;
        uint64_t next = (t / period + 1) * period;
        struct timespec ts = { (time_t)(next / 1000000000ull), (long)(next % 1000000000ull) };
        clock_nanosleep(CLOCK_MONOTONIC, TIMER_ABSTIME, &ts, NULL);
    }
    g_esp += 4;
}

/* A wrapped function whose C++ body exists (avpe-decomp defines avpe_dec_X:
 * the bridge keeps the sub_X name for its trace) runs that body; the lifted
 * one otherwise (a build without the decompiled sources). */
#define AVPE_DEC_OR_GEN(V) do { extern void avpe_dec_##V(void) __attribute__((weak)); \
    if (avpe_dec_##V) avpe_dec_##V(); else { avpe_verify_enter(0x##V##u); sub_##V##_gen(); avpe_verify_exit(0x##V##u); } } while (0)


/* Game allocator (heap, size, align) -> ptr, stdcall ret 12: observe requests
 * and report the first failure with the heap's state. Measurement only. */
extern void sub_000DC4D0_gen(void);
static uint32_t s_dstack[64]; static int s_dsp;            /* decompiled functions entered (alloc trace) */
uint64_t g_avpe_alloc_bytes; uint32_t g_avpe_allocs;
void sub_000DC4D0(void)
{
    uint32_t heap = MEM32(g_esp + 4), size = MEM32(g_esp + 8), align = MEM32(g_esp + 12);
    static int failed;
    static int ci = -1; static uint32_t cover; static long snap_at = -1;
    if (ci < 0) { const char *c = getenv("AVPE_COVER"), *n = getenv("AVPE_SNAP_ALLOC");
                  cover = c ? (uint32_t)strtoul(c, NULL, 16) : 0; snap_at = n ? atol(n) : -1; ci = 1; }
    uint32_t ret = MEM32(g_esp);
    if ((long)g_avpe_allocs == snap_at) {
        avpe_snapshot(getenv("AVPE_SNAPSHOT") ? getenv("AVPE_SNAPSHOT") : "/home/claude/avpe-recomp/snap_alloc.bin");
        fprintf(stderr, "[SNAP] alloc #%u heap=%08X size=%u align=%u ret=%08X\n", g_avpe_allocs, heap, size, align, ret);
    }
    avpe_watch_poll("alloc-enter");
    AVPE_DEC_OR_GEN(000DC4D0);
    avpe_watch_poll("alloc-exit");
    if ((long)g_avpe_allocs == snap_at) {
        FILE *f = fopen("/home/claude/avpe-recomp/snap_alloc_lifted.txt", "w");
        if (f) { fprintf(f, "eax=%08X esp=%08X\n", g_eax, g_esp); fclose(f); }
    }
    if (cover && g_eax && cover >= g_eax && cover - g_eax < size)
        fprintf(stderr, "[COVER] frame %u alloc #%u heap=%08X size=%u align=%u ret=%08X -> %08X..%08X covers %08X\n",
                g_avpe_frames, g_avpe_allocs, heap, size, align, ret, g_eax, g_eax + size, cover);
    g_avpe_allocs++; g_avpe_alloc_bytes += size;
    static long trace = -2;
    if (trace == -2) { const char *t = getenv("AVPE_TRACE_ALLOC"); trace = t ? (atol(t) > 1 ? atol(t) : 65536) : -1; }
    if (trace >= 0 && size >= (uint32_t)trace)
        fprintf(stderr, "[ALLOC] heap=%08X size=%u align=%u -> %08X from %08X (frame %u) decomp %X/%X/%X\n", heap, size, align, g_eax, ret, g_avpe_frames,
                s_dsp > 0 && s_dsp <= 64 ? s_dstack[s_dsp - 1] : 0, s_dsp > 1 && s_dsp <= 64 ? s_dstack[s_dsp - 2] : 0, s_dsp > 2 && s_dsp <= 64 ? s_dstack[s_dsp - 3] : 0);
    if (!g_eax && failed == 0) {                            /* a rough guest backtrace: code addresses on the stack */
        fprintf(stderr, "[ALLOC] stack:");
        for (int i = 0; i < 160; i++) { uint32_t v = MEM32(g_esp + 4 * i); if (v >= 0x11000 && v < 0x170000) fprintf(stderr, " +%X:%X", 4 * i, v); }
        fprintf(stderr, "\n");
    }
    if (!g_eax && failed++ < 5)
        fprintf(stderr, "[ALLOC] FAILED heap=%08X size=%u align=%u after %u allocs / %llu bytes requested\n",
                heap, size, align, g_avpe_allocs, (unsigned long long)g_avpe_alloc_bytes);
}

/* Fatal disc-error handler registered at init ([0x2F6754], [0x2F6848]): draws
 * the error screen into every buffer and halts (jmp $ at 0x633D0). Report who
 * called it before letting it run. */
extern void sub_00063190_gen(void);
void sub_00063190(void)
{
    fprintf(stderr, "[FATAL] disc-error handler called from 0x%08X (args %08X %08X)\n",
            MEM32(g_esp), MEM32(g_esp + 4), MEM32(g_esp + 8));
    { extern void avpe_soak_crash(const char *); avpe_soak_crash("FATAL: the game's disc-error screen"); }
    { const char *fe = getenv("AVPE_FATAL_EXIT");               /* (0: run the screen, as a probe of it must) */
      if (fe && *fe && *fe != '0') { fprintf(stderr, "[FATAL] exiting (AVPE_FATAL_EXIT)\n"); exit(42); } }
    AVPE_DEC_ELSE_GEN(00063190);
}

/* GVideoPlayer worker thread (0x120DE0, stdcall(ctx)): creates an XMV decoder
 * for D:\<ctx name>, plays it, closes it, sets [ctx+0x204]=1 and ExitThread(0).
 * XMV paces video on the audio clock; with no APU behind DirectSound the clock
 * never moves and the thread never ends. Minimal bridge: take the "played to the
 * end" path at once. AVPE_REAL_XMV=1 runs the original worker instead. */
extern void sub_00120DE0_gen(void);
extern void xdk_ExitThread(void);
uint32_t g_avpe_movies;
void sub_00120DE0(void)
{
    uint32_t ctx = MEM32(g_esp + 4);
    { extern int avpe_pc_movies_on(void) __attribute__((weak));   /* pc_movie.cpp: movies through FFmpeg */
      if (getenv("AVPE_REAL_XMV") || (avpe_pc_movies_on && avpe_pc_movies_on())) { AVPE_DEC_ELSE_GEN(00120DE0); return; } }
    char name[64]; int i = 0;
    for (; i < 63 && MEM8(ctx + i); i++) name[i] = (char)MEM8(ctx + i);
    name[i] = 0;
    g_avpe_movies++;
    fprintf(stderr, "[MOVIE] skip #%u '%s' (marked finished)\n", g_avpe_movies, name);
    MEM32(ctx + 0x200) = 0;          /* no decoder */
    MEM32(ctx + 0x204) = 1;          /* finished */
    PUSH32(g_esp, 0);                /* ExitThread(0) */
    PUSH32(g_esp, 0x00120F0Au);      /* return address as the original call leaves it */
    { extern recomp_func_t recomp_lookup(uint32_t); recomp_lookup(0x00142DB1u)(); }   /* translated or native */
}

/* D3D_AllocContiguousMemory(size, align) (XbSymbolDatabase): the TBD loader
 * takes chunk buffers flagged "physical" from here instead of the game heap.
 * Log what the runtime hands back (measurement). */
extern void sub_00176280_gen(void);
void sub_00176280(void)
{
    uint32_t size = MEM32(g_esp + 4), align = MEM32(g_esp + 8), ret = MEM32(g_esp);
    static int n;
    AVPE_DEC_ELSE_GEN(00176280);
    if (getenv("AVPE_TRACE_CONTIG") || n < 5)
        fprintf(stderr, "[CONTIG] #%d frame %u D3D_AllocContiguousMemory(%u, 0x%X) from %08X -> %08X\n", n, g_avpe_frames, size, align, ret, g_eax);
    n++;
}

/* CTbdFile::~CTbdFile (0xDE8F0, thiscall). AVPE_SNAP_DTOR=N: on the Nth call
 * from CShell::ShellLoadLevel (return address 0xF0C0E, the level teardown),
 * snapshot guest state before and after, for the oracle to replay the
 * original destructor from the first and diff against the second. */
extern void sub_000DE8F0_gen(void);
void sub_000DE8F0(void)
{
    static int n; static long want = -2;
    if (want == -2) { const char *w = getenv("AVPE_SNAP_DTOR"); want = w ? atol(w) : -1; }
    int hit = 0;
    extern int g_avpe_logwin;
    if (MEM32(g_esp) == 0x000F0C0Eu) {
        n++;
        fprintf(stderr, "[TEARDOWN] #%d frame %u CTbdFile dtor this=%08X\n", n, g_avpe_frames, g_ecx);
        if (n == want) { hit = 1; avpe_snapshot("/home/claude/avpe-recomp/snap_dtor.bin"); g_avpe_logwin = 1; }
    }
    AVPE_DEC_ELSE_GEN(000DE8F0);
    if (hit) { g_avpe_logwin = 0; avpe_snapshot("/home/claude/avpe-recomp/snap_dtor_lifted.bin"); }
}

/* XAPI heap internals (unnamed): log calls with args inside the snapshot
 * window so the sequence can be compared with the oracle's (--log-calls). */
int g_avpe_logwin;
/* AVPE_SNAP_CALL=N: snapshot before and after the Nth logged call (1-based)
 * so the oracle can replay that single function. */
static int avpe_logcall(uint32_t addr)
{
    static int n; static long want = -2;
    if (want == -2) { const char *w = getenv("AVPE_SNAP_CALL"); want = w ? atol(w) : -1; }
    if (!g_avpe_logwin) return 0;
    n++;
    fprintf(stderr, "[CALL] %08X ret=%08X args %08X %08X %08X ecx=%08X\n", addr,
        MEM32(g_esp), MEM32(g_esp + 4), MEM32(g_esp + 8), MEM32(g_esp + 12), g_ecx);
    if (n != want) return 0;
    fprintf(stderr, "[SNAP] call #%d\n", n);
    avpe_snapshot("/home/claude/avpe-recomp/snap_call.bin");
    return 1;
}
#define AVPE_AFTER(hit) do { if (hit) avpe_snapshot("/home/claude/avpe-recomp/snap_call_lifted.bin"); } while (0)
extern void sub_001442D7_gen(void);
void sub_001442D7(void) { int h = avpe_logcall(0x001442D7u); AVPE_DEC_ELSE_GEN(001442D7); AVPE_AFTER(h); }
extern void sub_00144D64_gen(void);
void sub_00144D64(void) { int h = avpe_logcall(0x00144D64u); AVPE_DEC_ELSE_GEN(00144D64); AVPE_AFTER(h); }
extern void sub_00145BAD_gen(void);
void sub_00145BAD(void) { int h = avpe_logcall(0x00145BADu); AVPE_DEC_ELSE_GEN(00145BAD); AVPE_AFTER(h); }

/* Decompilation verification (avpe-decomp/include/avpe/guest.h).
 * AVPE_VERIFY=<hex va>:<n>  snapshot guest state at entry to and exit from the
 * n-th call of the decompiled function at va (default n=1), into
 * /home/claude/avpe-recomp/verify/<va>_before.bin and _after.bin, then exit
 * (AVPE_VERIFY_CONTINUE=1 keeps running). tools/verify.py replays the original
 * from _before and compares with _after. */
static uint32_t s_verify_va, s_verify_n = 1, s_verify_calls;
static int s_verify_init, s_verify_armed;
static void verify_init(void)
{
    const char *v = getenv("AVPE_VERIFY");
    s_verify_init = 1;
    if (!v) return;
    s_verify_va = (uint32_t)strtoul(v, NULL, 16);
    const char *c = strchr(v, ':');
    if (c) s_verify_n = (uint32_t)atol(c + 1);
}
/* AVPE_VERIFY_MULTI=<file of "va[:n]" lines>: capture the n-th call (default
 * the first) of every listed function in one run, for tools/verify_multi.py.
 * Captures go to verify/multi.stream: a full memory image first, then for each
 * entry/exit a record with the registers and only the 4 KB pages that changed
 * since the previous record. Exits when every function has been captured
 * (unless AVPE_VERIFY_CONTINUE). */
#define MULTI_SLOTS 8192
static struct { uint32_t va, n, calls, esp; int state; } s_multi[MULTI_SLOTS];   /* state 0 wait, 1 armed, 2 done */
static int s_multi_on = -1, s_multi_left;
static FILE *s_stream;
static uint8_t *s_shadow;
static uint32_t s_stream_pages[0x04000000u / 4096];
static int multi_slot(uint32_t va)
{
    uint32_t h = (va * 2654435761u) & (MULTI_SLOTS - 1);
    while (s_multi[h].va && s_multi[h].va != va) h = (h + 1) & (MULTI_SLOTS - 1);
    return (int)h;
}
static void multi_record(uint32_t kind, uint32_t va, uint32_t callno)
{
    extern RECOMP_TLS uint16_t g_fp_control_word;
    const uint8_t *mem = (const uint8_t *)XBOX_PTR(0);
    uint32_t np = 0;
    for (uint32_t pg = 0; pg < 0x04000000u / 4096; pg++)
        if (kind == 0 || memcmp(s_shadow + pg * 4096, mem + pg * 4096, 4096)) s_stream_pages[np++] = pg;
    uint32_t rec[8] = { 0x43455256u /* VREC */, kind, va, callno, np, 0, 0, 0 };
    {   /* rec[5..6]: st(1) as a double, for functions taking two x87 arguments (_CIpow) */
        double v1 = g_fp_stack[(g_fp_top + 1) & 7]; uint64_t b1; memcpy(&b1, &v1, 8);
        rec[5] = (uint32_t)b1; rec[6] = (uint32_t)(b1 >> 32);
    }
    uint32_t hdr[16] = { 0x32414E53u, g_eax, g_ecx, g_edx, g_ebx, g_esp, g_ebp, g_esi, g_edi, 0x4000u,
                         0x04000000u, 0, g_fp_control_word, 0, 0, 0 };
    avpe_fp_header(hdr);
    fwrite(rec, 4, 8, s_stream); fwrite(hdr, 4, 16, s_stream);
    for (uint32_t i = 0; i < np; i++) {
        uint32_t pg = s_stream_pages[i];
        fwrite(&pg, 4, 1, s_stream); fwrite(mem + pg * 4096, 1, 4096, s_stream);
        memcpy(s_shadow + pg * 4096, mem + pg * 4096, 4096);
    }
    fflush(s_stream);
}
/* AVPE_VERIFY_EXTERN=va[,va...]: functions the oracle cannot run (they read
 * the controllers, the disc, the clock). Inside a capture, each call a
 * decompiled function makes to one of them through gcall is recorded: kind 3
 * at its entry, kind 4 at its return, so verify_multi.py can hand the oracle
 * what the call returned and the bytes it changed. */
static uint32_t s_extern[32]; static int s_nextern, s_capturing, s_extern_all;
static uint32_t s_in_extern, s_extern_esp, s_extern_seq;
/* AVPE_VERIFY_EXTERN=* : every call the captured function makes is recorded
 * (compositional verification: the oracle replays the callee's recorded
 * effects instead of running it, and checks the arguments it was given). */
static void extern_init(void)
{
    const char *e = getenv("AVPE_VERIFY_EXTERN");
    if (e && *e == '*') { s_extern_all = 1; return; }
    for (const char *p = e; p && *p && s_nextern < 32;) {
        char *end; uint32_t va = (uint32_t)strtoul(p, &end, 16);
        if (end == p) break;
        s_extern[s_nextern++] = va;
        p = *end == ',' ? end + 1 : end;
    }
}
/* Stub mode (a probe line with stub=1): every call the probed function makes
 * is recorded as above but not made: it returns at once with eax from the
 * line's ret=K:V (K-th call, default 0), popping what the callee pops. The
 * original's run in the oracle gets the same records, so each function is
 * verified on its own code (compositional), on inputs its callees could not
 * have run with. */
#include "abi_pops.inc"      /* recomp/, on the include path */
/* Calls that never return and spin loops (recomp/noreturn.json): a probe of a
 * function that reaches one ends its capture there (longjmp back to the probe
 * loop); the oracle stops the original at the same place. */
#include <setjmp.h>
#include "noreturn.inc"      /* recomp/, on the include path */
static jmp_buf s_noret_jb; static volatile int s_noret_armed;
/* Called at the top of each CShell::MainLoop pass, before the world's frame
 * (platform/pc_quicksave.cpp sets it: quicksave and quickload run there). */
void (*g_avpe_frame_hook)(void);
void (*g_avpe_create_hook)(uint32_t, uint32_t, uint32_t);
static int noret_in(const uint32_t *t, int n, uint32_t va)
{
    for (int i = 0; i < n; i++) if (t[i] == va) return 1;
    return 0;
}
void avpe_verify_spin(uint32_t at)
{
    if (s_noret_armed && s_capturing > 0 && !s_in_extern &&
        noret_in(s_noret_points, (int)(sizeof s_noret_points / 4), at)) {
        fprintf(stderr, "[PROBE] reached the spin at %08X: compared there\n", at);
        longjmp(s_noret_jb, 1);
    }
}
static int s_stub_mode; static uint32_t s_stub_ret[64], s_stub_k;
static uint64_t s_stub_set;                                   /* which ret=K:V were given */
static uint32_t s_sret[32][2]; static int s_nsret;            /* sret=RA:V: every call returning to RA (a loop's) */
#include "kernel_pops.inc"   /* recomp/: per import slot, what the kernel export pops */
static uint32_t abi_pops(uint32_t va)
{
    if (va >= 0xFE000000u) {                                  /* a kernel thunk (stub mode stubs kernel calls too) */
        uint32_t slot = (va - 0xFE000000u) / 4;
        return slot < sizeof s_kernel_slot_pops ? s_kernel_slot_pops[slot] : 0;
    }
    int lo = 0, hi = (int)(sizeof s_abi_pops / sizeof s_abi_pops[0]) - 1;
    while (lo <= hi) {
        int m = (lo + hi) / 2;
        if (s_abi_pops[m][0] == va) return s_abi_pops[m][1];   /* bit 31: returns in st(0) */
        if (s_abi_pops[m][0] < va) lo = m + 1; else hi = m - 1;
    }
    return 0;
}
int avpe_verify_stub(uint32_t va)
{
    if (!s_stub_mode || s_capturing <= 0 || s_in_extern != va) return 0;
    uint32_t k = s_stub_k++;
    g_eax = 0;
    if (k < 64 && (s_stub_set >> k & 1)) g_eax = s_stub_ret[k];
    else {
        uint32_t ra = MEM32(g_esp);                           /* the call's return address, pushed */
        for (int i = 0; i < s_nsret; i++) if (s_sret[i][0] == ra) { g_eax = s_sret[i][1]; break; }
    }
    uint32_t pops = abi_pops(va);
    if (pops & 0x80000000u) {   /* a callee that returns in st(0) pushes one: 0.0 (recorded, so the oracle pushes the same) */
        g_fp_top = (g_fp_top + 7u) & 7u;
        g_fp_stack[g_fp_top] = 0.0;
    }
    g_esp += 4 + (pops & 0x7FFFFFFFu);
    return 1;
}
static int is_extern(uint32_t va)
{
    if (s_extern_all) return 1;
    for (int i = 0; i < s_nextern; i++) if (s_extern[i] == va) return 1;
    return 0;
}
/* Time stamp counter reads (rdtsc: QueryPerformanceCounter) on the game
 * thread inside a capture, outside external calls: kind 5 records with the
 * value in header words 1-2, so the oracle's rdtsc returns the same. */
#include <pthread.h>
extern void (*xbox_tsc_observer)(uint64_t);
static pthread_t s_game_thread;
static void tsc_observe(uint64_t v)
{
    if (!s_stream || s_capturing <= 0 || s_in_extern || !pthread_equal(pthread_self(), s_game_thread)) return;
    uint32_t rec[8] = { 0x43455256u, 5, 0, 0, 0, 0, 0, 0 };
    uint32_t hdr[16] = { 0x32414E53u, (uint32_t)v, (uint32_t)(v >> 32) };
    fwrite(rec, 4, 8, s_stream); fwrite(hdr, 4, 16, s_stream);
}
/* Kernel calls the captured code makes on the game thread are recorded like
 * externs (kind 3/4, keyed by the thunk address 0xFE000000 + 4 * slot), so
 * the oracle replays their results (file reads, events, time) instead of
 * stopping at a kernel ordinal it has no handler for. */
extern void (*xbox_kernel_observer)(int phase, int slot);
static void kernel_observe(int phase, int slot)
{
    if (!s_stream || s_capturing <= 0 || !pthread_equal(pthread_self(), s_game_thread)) return;
    uint32_t va = 0xFE000000u + 4u * (uint32_t)slot;
    if (phase == 0 && !s_in_extern) {
        multi_record(3, va, s_extern_seq);
        s_in_extern = va; s_extern_esp = g_esp;
    } else if (phase == 1 && s_in_extern == va) {
        s_in_extern = 0;
        multi_record(4, va, s_extern_seq++);
    }
}
static void tsc_init(void)
{
    s_game_thread = pthread_self(); xbox_tsc_observer = tsc_observe;
    if (!getenv("AVPE_VERIFY_NO_KERNEL_EXTERN")) xbox_kernel_observer = kernel_observe;
}
static void multi_init(void)
{
    const char *path = getenv("AVPE_VERIFY_MULTI");
    s_multi_on = 0;
    if (!path) return;
    FILE *f = fopen(path, "r");
    if (!f) return;
    char line[64];
    while (fgets(line, sizeof line, f)) {
        uint32_t va = (uint32_t)strtoul(line, NULL, 16);
        if (!va) continue;
        const char *c = strchr(line, ':');
        int k = multi_slot(va);
        if (s_multi[k].va) continue;
        s_multi[k].va = va; s_multi[k].n = c ? (uint32_t)atol(c + 1) : 1; s_multi_left++;
    }
    fclose(f);
    s_stream = fopen("/home/claude/avpe-recomp/verify/multi.stream", "wb");
    s_shadow = (uint8_t *)malloc(0x04000000u);
    if (!s_stream || !s_shadow) return;
    multi_record(0, 0, 0);
    extern_init(); tsc_init();
    s_multi_on = 1;
    fprintf(stderr, "[VERIFY] multi: %d functions\n", s_multi_left);
}
static void multi_enter(uint32_t va)
{
    int k = multi_slot(va);
    if (s_multi[k].va != va || s_multi[k].state) return;
    if (++s_multi[k].calls != s_multi[k].n) return;
    multi_record(1, va, s_multi[k].calls);
    s_multi[k].state = 1; s_multi[k].esp = g_esp; s_capturing++;
}
static void multi_exit(uint32_t va)
{
    int k = multi_slot(va);
    if (s_multi[k].va != va || s_multi[k].state != 1 || g_esp <= s_multi[k].esp) return;   /* inner recursive call */
    multi_record(2, va, s_multi[k].calls);
    s_multi[k].state = 2; s_capturing--;
    if (--s_multi_left == 0) {
        fprintf(stderr, "[VERIFY] multi: all captured\n");
        fclose(s_stream); s_stream = NULL;
        if (!getenv("AVPE_VERIFY_CONTINUE")) exit(0);
        s_multi_on = 0;
    }
}

/* n-th decompiled caller of the function now running (0: itself), 0 if none (telemetry) */
uint32_t avpe_dstack_ancestor(int n)
{
    int i = s_dsp - 1 - n;
    return (i >= 0 && i < 64) ? s_dstack[i] : 0;
}
/* AVPE_FPTOP=1: report where the modelled x87 top first leaves 0 between two
 * decompiled-function events (MSVC code calls with an empty x87 stack, so a
 * nonzero top at an entry is drift: fnstsw then shows a TOP the hardware
 * would not have). Prints the event before and the event after, once per pair. */
static int s_fptop_on = -1; static uint32_t s_fptop_last; static char s_fptop_kind;
static void fptop_event(uint32_t va, char kind)
{
    if (s_fptop_on < 0) s_fptop_on = getenv("AVPE_FPTOP") != NULL;
    if (!s_fptop_on) return;
    static int reported;
    static int last_top;
    int top = g_fp_top & 7;
    /* a function returning in st(0) leaves top 7 until its caller pops: only an
     * ENTRY with a nonzero top is drift (MSVC never calls with values on the
     * x87 stack, the st(0) helpers aside); each entry point once */
    static uint32_t seen[256]; static int nseen;
    int dup = 0;
    for (int i = 0; i < nseen; i++) if (seen[i] == va) dup = 1;
    if (kind == '>' && top != 0 && !dup && reported < 200) {
        if (nseen < 256) seen[nseen++] = va;
        fprintf(stderr, "[FPTOP] frame %u: top %d -> %d between %c%08X and %c%08X\n", g_avpe_frames, last_top, top,
                s_fptop_kind ? s_fptop_kind : '?', s_fptop_last, kind, va);
        reported++;
    }
    last_top = top; s_fptop_last = va; s_fptop_kind = kind;
}
void avpe_verify_enter(uint32_t va)
{
    fptop_event(va, '>');
    if (s_dsp < 64) s_dstack[s_dsp] = va;
    s_dsp++;
    if (g_avpe_tel_on) avpe_tel_enter(va);
    if (g_avpe_diff_on) avpe_diff_enter(va);
    if (s_multi_on < 0) multi_init();
    if (s_multi_on > 0) { multi_enter(va); return; }
    if (!s_verify_init) verify_init();
    if (va != s_verify_va || ++s_verify_calls != s_verify_n) return;
    char p[160]; snprintf(p, sizeof p, "/home/claude/avpe-recomp/verify/%08X_before.bin", va);
    avpe_snapshot(p); s_verify_armed = 1;
}
void avpe_verify_exit(uint32_t va)
{
    fptop_event(va, '<');
    if (g_avpe_tel_on) avpe_tel_exit(va);
    if (g_avpe_diff_on) avpe_diff_exit(va);
    if (s_dsp > 0) s_dsp--;
    if (s_multi_on > 0) { multi_exit(va); return; }
    if (!s_verify_armed || va != s_verify_va) return;
    s_verify_armed = 0;
    char p[160]; snprintf(p, sizeof p, "/home/claude/avpe-recomp/verify/%08X_after.bin", va);
    avpe_snapshot(p);
    fprintf(stderr, "[VERIFY] %08X call #%u captured\n", va, s_verify_calls);
    if (!getenv("AVPE_VERIFY_CONTINUE")) exit(0);
}

/* Probes: call a decompiled function on purpose, for paths the game never
 * takes by itself (verification of untaken branches).
 *   AVPE_PROBE="va=5D560 ecx=@obj poke=26B410:0 arg=1234 arg=..."
 * runs once at presented frame AVPE_PROBE_FRAME (default 40): applies the
 * pokes, sets the registers, pushes the arguments and a marker return address
 * (0xDEC0DE00), arms AVPE_VERIFY for that call and calls the function.
 * @obj is the last value a glue function handed to avpe_probe_note();
 * poke=@obj+B0:FFFFFFFE writes a field of it; @+E8 as a value is the address
 * of a field (arg=@+E8 passes a pointer into the object). */
uint32_t g_avpe_probe_obj;
void avpe_probe_note(uint32_t v) { g_avpe_probe_obj = v; }
/* Per factory: the last object each Create made, for probes that need an
 * object of one class ("@C7FD60" = the last GUnitMover; "@C7FD60+E8"). */
#define NOTE_SLOTS 4096
static struct { uint32_t va, obj; } s_notes[NOTE_SLOTS];
void avpe_probe_note_va(uint32_t va, uint32_t v)
{
    g_avpe_probe_obj = v;
    if (!v) return;
    uint32_t h = (va * 2654435761u) & (NOTE_SLOTS - 1);
    while (s_notes[h].va && s_notes[h].va != va) h = (h + 1) & (NOTE_SLOTS - 1);
    s_notes[h].va = va; s_notes[h].obj = v;
}
static uint32_t note_of(uint32_t va)
{
    uint32_t h = (va * 2654435761u) & (NOTE_SLOTS - 1);
    while (s_notes[h].va && s_notes[h].va != va) h = (h + 1) & (NOTE_SLOTS - 1);
    return s_notes[h].va == va ? s_notes[h].obj : 0;
}
extern recomp_func_t recomp_lookup(uint32_t);
/* a value: hex, @obj, @+OFF (an address inside @obj), *ADDR (the word
 * at ADDR when the probe runs, e.g. a class id computed at startup), or
 * [X]+OFF (the word at value X, plus OFF; nests: [[2F6824]+48]+8), or
 * @S+OFF (a 64 KB zeroed guest block for data the probe pokes: messages, vectors) */
extern uint32_t xbox_ContiguousAlloc(uint32_t size, uint32_t alignment);
static int s_probe_unset;
static uint32_t s_probe_save;                 /* save=ADDR: the probed function's eax is stored there afterwards (for later probes: [ADDR]) */
static uint32_t s_probe_scratch;              /* @S: 64 KB of zeroed guest memory for probe data */
static uint32_t probe_val(const char *v)
{
    if (v[0] == '@' && v[1] == 'S') {
        if (!s_probe_scratch) { s_probe_scratch = xbox_ContiguousAlloc(0x10000, 16) & 0x03FFFFFFu; memset((void *)XBOX_PTR(s_probe_scratch), 0, 0x10000); }
        const char *p = strchr(v, '+');
        return s_probe_scratch + (p ? (uint32_t)strtoul(p + 1, NULL, 16) : 0);
    }
    if (v[0] == '[') {                       /* [X]+OFF: the word at X, plus OFF */
        int depth = 0; const char *e = v;
        for (; *e; e++) { if (*e == '[') depth++; else if (*e == ']' && --depth == 0) break; }
        char in[128]; size_t n = (size_t)(e - v - 1); if (n > 127) n = 127;
        memcpy(in, v + 1, n); in[n] = 0;
        uint32_t w = MEM32(probe_val(in));
        return *e && e[1] == '+' ? w + (uint32_t)strtoul(e + 2, NULL, 16) : w;
    }
    if (v[0] == '*') return MEM32((uint32_t)strtoul(v + 1, NULL, 16));
    if (v[0] != '@') return (uint32_t)strtoul(v, NULL, 16);
    const char *p = strchr(v, '+');
    /* @N<va>: the object that factory made, without the handle-table check
     * (an object made with no parent and no handle is never in the table) */
    if (v[1] == 'N') {
        uint32_t o = note_of((uint32_t)strtoul(v + 2, NULL, 16));
        if (!o) s_probe_unset = 1;
        return o + (p ? (uint32_t)strtoul(p + 1, NULL, 16) : 0);
    }
    uint32_t base = v[1] == 'C' ? note_of((uint32_t)strtoul(v + 2, NULL, 16)) : g_avpe_probe_obj;
    /* no such object, or it no longer exists (its handle no longer maps to
     * it: memory reused): the probe is skipped */
    if (!base || (v[1] == 'C' && MEM32(0x00289E40u + 4 * (MEM32(base + 0x18) >> 16)) != base)) s_probe_unset = 1;
    return base + (p ? (uint32_t)strtoul(p + 1, NULL, 16) : 0);
}
/* Apply one probe spec (registers, pokes, arguments); returns the function. */
static uint32_t probe_setup(char *buf, uint32_t *args, int *nargs)
{
    uint32_t va = 0; *nargs = 0;
    for (char *t = strtok(buf, " "); t; t = strtok(NULL, " ")) {
        char *eq = strchr(t, '='); if (!eq) continue; *eq = 0; const char *v = eq + 1;
        if (!strcmp(t, "va")) va = (uint32_t)strtoul(v, NULL, 16);
        else if (!strcmp(t, "eax")) g_eax = probe_val(v);
        else if (!strcmp(t, "ecx")) g_ecx = probe_val(v);
        else if (!strcmp(t, "edx")) g_edx = probe_val(v);
        else if (!strcmp(t, "ebx")) g_ebx = probe_val(v);
        else if (!strcmp(t, "esi")) g_esi = probe_val(v);
        else if (!strcmp(t, "edi")) g_edi = probe_val(v);
        else if (!strcmp(t, "arg") && *nargs < 16) args[(*nargs)++] = probe_val(v);
        else if (!strcmp(t, "stub")) s_stub_mode = atoi(v);
        else if (!strcmp(t, "save")) s_probe_save = probe_val(v);
        else if (!strcmp(t, "st0")) {               /* st(0) in: a double (16 hex digits) or a decimal */
            double d; uint64_t b;
            if (strlen(v) == 16 && !strchr(v, '.')) { b = strtoull(v, NULL, 16); memcpy(&d, &b, 8); }
            else d = strtod(v, NULL);
            g_fp_top = (g_fp_top + 7u) & 7u; g_fp_stack[g_fp_top] = d;
        }
        else if (!strcmp(t, "ret")) { const char *c = strchr(v, ':');
            uint32_t k = (uint32_t)strtoul(v, NULL, 10); if (c && k < 64) { s_stub_ret[k] = probe_val(c + 1); s_stub_set |= 1ull << k; } }
        else if (!strcmp(t, "sret")) { const char *c = strchr(v, ':');
            if (c && s_nsret < 32) { s_sret[s_nsret][0] = (uint32_t)strtoul(v, NULL, 16); s_sret[s_nsret][1] = probe_val(c + 1); s_nsret++; } }
        else if (!strcmp(t, "poke")) { const char *c = strchr(v, ':');
            char addr[64]; size_t n = c ? (size_t)(c - v) : strlen(v); if (n > 63) n = 63;
            memcpy(addr, v, n); addr[n] = 0;
            if (c) MEM32(probe_val(addr)) = probe_val(c + 1); }
    }
    return va;
}

/* AVPE_PROBE_MULTI=<file of probe specs, one per line>: at AVPE_PROBE_FRAME,
 * run every probe in turn, each recorded into verify/multi.stream (entry and
 * exit, keyed by its line number), for tools/verify_multi.py --probes. Each
 * probe starts from the registers and stack the game had; memory changes of
 * one probe stay for the next (they are recorded, so each replay starts from
 * exactly what its probe saw). Exits afterwards. */
static void probe_multi(const char *path)
{
    FILE *f = fopen(path, "r");
    if (!f) return;
    const char *sp = getenv("AVPE_PROBE_STREAM");
    s_stream = fopen(sp ? sp : "/home/claude/avpe-recomp/verify/multi.stream", "wb");
    s_shadow = (uint8_t *)malloc(0x04000000u);
    if (!s_stream || !s_shadow) return;
    multi_record(0, 0, 0);
    extern_init(); tsc_init();
    for (int i = 0; i < NOTE_SLOTS; i++)              /* the objects @C<va> can name */
        if (s_notes[i].va) fprintf(stderr, "[PROBE] @C%X = %08X%s\n", s_notes[i].va, s_notes[i].obj,
                                   MEM32(0x00289E40u + 4 * (MEM32(s_notes[i].obj + 0x18) >> 16)) == s_notes[i].obj ? " live" : "");
    /* every live game object (the handle table: an object is live when its
     * handle's slot points back at it), with its vtable: tools/liveprobe.py
     * maps vtables to classes and names objects by address (replays are
     * deterministic, so the address is the same object in the probe run) */
    if (getenv("AVPE_PROBE_OBJS"))
        for (uint32_t i = 1; i < 0x8000u; i++) {
            uint32_t o = MEM32(0x00289E40u + 4 * i);
            if (o < 0x1000u || o >= 0x03FF0000u || (MEM32(o + 0x18) >> 16) != i) continue;
            fprintf(stderr, "[OBJ] %08X %08X\n", o, MEM32(o));
        }
    static char line[16384]; uint32_t idx = 0;           /* reachfuzz probes carry many pokes */
    uint32_t save[8] = { g_eax, g_ecx, g_edx, g_ebx, g_esp, g_ebp, g_esi, g_edi };
    const uint32_t save_fptop = g_fp_top;               /* st0= pushes; each probe starts from the game's x87 stack */
    while (fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        idx++;
        if (!line[0]) continue;
        uint32_t args[16]; int nargs;
        s_probe_unset = 0;
        s_stub_mode = 0; s_stub_k = 0; memset(s_stub_ret, 0, sizeof s_stub_ret); s_stub_set = 0; s_nsret = 0;
        s_probe_save = 0;
        uint32_t va = probe_setup(line, args, &nargs);
        if (!va) continue;
        if (s_probe_unset) {
            fprintf(stderr, "[PROBE] line %u skipped: an @ object does not exist yet\n", idx);
            g_eax = save[0]; g_ecx = save[1]; g_edx = save[2]; g_ebx = save[3];
            g_esp = save[4]; g_ebp = save[5]; g_esi = save[6]; g_edi = save[7];
            g_fp_top = save_fptop;
            continue;
        }
        for (int i = nargs; i-- > 0;) PUSH32(g_esp, args[i]);
        PUSH32(g_esp, 0xDEC0DE00u);
        multi_record(1, va, idx);
        s_capturing = 1;
        if (setjmp(s_noret_jb) == 0) {
            s_noret_armed = 1;
            recomp_lookup(va)();
        }
        s_noret_armed = 0; s_in_extern = 0;
        s_capturing = 0; s_stub_mode = 0;
        multi_record(2, va, idx);
        if (s_probe_save) { MEM32(s_probe_save) = g_eax; s_probe_save = 0; }
        g_eax = save[0]; g_ecx = save[1]; g_edx = save[2]; g_ebx = save[3];
        g_esp = save[4]; g_ebp = save[5]; g_esi = save[6]; g_edi = save[7];
        g_fp_top = save_fptop;
    }
    fclose(f); fclose(s_stream); s_stream = NULL;
    fprintf(stderr, "[PROBE] multi: %u probes done\n", idx);
    if (getenv("AVPE_PROBE_CHILD")) { fflush(NULL); _exit(0); }   /* a fork server child: no game shutdown */
    exit(0);
}

/* AVPE_PROBE_SERVER=<dir> (with AVPE_PROBE_MULTI, AVPE_PROBE_FRAME): a fork
 * server. At the probe frame the game lists its objects (<dir>/objs.txt),
 * creates <dir>/ready and waits on the FIFO <dir>/req for lines
 * "<probe file> <stream file> <log file>" (or "quit"). Each request runs in a
 * fork() of the paused game: probe_multi there, stderr to the log, the
 * stream to the stream file; the parent stays at the probe frame for the
 * next request and answers "done <status>" on the FIFO <dir>/resp. One replay
 * to a late frame serves any number of probe batches (tools/forkprobe.py).
 * Linux only (fork, signals, /proc): the Windows build has no probe server. */
#ifdef _WIN32
static void probe_server(const char *dir)
{
    (void)dir;
    fprintf(stderr, "[PROBE] AVPE_PROBE_SERVER needs the Linux build; ignored\n");
}
#else
#include <sys/wait.h>
#include <signal.h>
#include <dirent.h>
#include <sys/syscall.h>
static void park_handler(int sig) { (void)sig; for (;;) pause(); }
/* The paused server needs no other thread; the NV2A ack thread spins a whole
 * core (sched_yield loop) and would halve the speed of every probe child. */
static void park_other_threads(void)
{
    signal(SIGUSR2, park_handler);
    pid_t me = (pid_t)syscall(SYS_gettid);
    DIR *d = opendir("/proc/self/task");
    struct dirent *e;
    while (d && (e = readdir(d))) {
        pid_t t = (pid_t)atoi(e->d_name);
        if (t > 0 && t != me) syscall(SYS_tgkill, getpid(), t, SIGUSR2);
    }
    if (d) closedir(d);
}
/* Guest RAM is a memfd mapped MAP_SHARED at several addresses (the
 * mirrors): fork() does not copy it, so without this every probe child
 * writes into the paused parent's game and the damage accumulates (a heap
 * free list that loops, objects of earlier probes). The child copies the
 * memfd into one of its own and maps every view onto the copy. */
#include <sys/mman.h>
#include <sys/stat.h>
static int private_guest_memory(void)
{
    struct { uintptr_t lo, hi, off; int prot; } v[64]; int n = 0;
    size_t size = 0;
    FILE *m = fopen("/proc/self/maps", "r");
    if (!m) return -1;
    char ln[512];
    while (fgets(ln, sizeof ln, m) && n < 64) {
        if (!strstr(ln, "xbox_map")) continue;
        unsigned long lo, hi, off; char perm[8];
        if (sscanf(ln, "%lx-%lx %7s %lx", &lo, &hi, perm, &off) != 4) continue;
        v[n].lo = lo; v[n].hi = hi; v[n].off = off;
        v[n].prot = (perm[0] == 'r' ? PROT_READ : 0) | (perm[1] == 'w' ? PROT_WRITE : 0) | (perm[2] == 'x' ? PROT_EXEC : 0);
        if (off + (hi - lo) > size) size = off + (hi - lo);
        n++;
    }
    fclose(m);
    if (!n) return -1;
    /* the original memfd is still open: copy the whole file (views do not
     * cover every range readably) */
    int old = -1;
    DIR *d = opendir("/proc/self/fd");
    struct dirent *e;
    while (d && (e = readdir(d))) {
        char lp[64], tgt[256]; snprintf(lp, sizeof lp, "/proc/self/fd/%s", e->d_name);
        ssize_t k = readlink(lp, tgt, sizeof tgt - 1);
        if (k > 0) { tgt[k] = 0; if (strstr(tgt, "memfd:xbox_map")) { old = atoi(e->d_name); break; } }
    }
    if (d) closedir(d);
    if (old >= 0) { struct stat st; if (fstat(old, &st) == 0 && (size_t)st.st_size > size) size = (size_t)st.st_size; }
    int fd = (int)syscall(SYS_memfd_create, "xbox_map_child", 0);
    if (fd < 0 || ftruncate(fd, (off_t)size) != 0) return -1;
    uint8_t *tmp = (uint8_t *)mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (tmp == MAP_FAILED) return -1;
    if (old >= 0) {
        for (size_t o = 0; o < size;) { ssize_t k = pread(old, tmp + o, size - o > (1u << 24) ? (1u << 24) : size - o, (off_t)o); if (k <= 0) break; o += (size_t)k; }
    } else {
        for (int i = 0; i < n; i++)                     /* no descriptor: from the readable views */
            if (v[i].prot & PROT_READ) memcpy(tmp + v[i].off, (void *)v[i].lo, v[i].hi - v[i].lo);
    }
    for (int i = 0; i < n; i++)
        if (mmap((void *)v[i].lo, v[i].hi - v[i].lo, v[i].prot, MAP_SHARED | MAP_FIXED, fd, (off_t)v[i].off) == MAP_FAILED)
            return -1;
    munmap(tmp, size);
    close(fd);
    return n;
}
static void probe_server(const char *dir)
{
    char p[512], line[1600];
    park_other_threads();
    snprintf(p, sizeof p, "%s/objs.txt", dir);
    FILE *o = fopen(p, "w");
    if (o) {
        for (uint32_t i = 1; i < 0x8000u; i++) {
            uint32_t ob = MEM32(0x00289E40u + 4 * i);
            if (ob < 0x1000u || ob >= 0x03FF0000u || (MEM32(ob + 0x18) >> 16) != i) continue;
            fprintf(o, "%08X %08X\n", ob, MEM32(ob));
        }
        fclose(o);
    }
    /* the whole guest memory at the probe frame: tools/sweepfork.py finds
     * objects of any class by their vtable pointers (not only handle-table
     * objects: script actions, render objects, effects) */
    snprintf(p, sizeof p, "%s/mem.bin", dir);
    o = fopen(p, "wb");
    if (o) { fwrite((void *)XBOX_PTR(0), 1, 0x04000000u, o); fclose(o); }
    snprintf(p, sizeof p, "%s/ready", dir);
    o = fopen(p, "w"); if (o) { fprintf(o, "%u\n", g_avpe_frames); fclose(o); }
    fprintf(stderr, "[PROBE] server ready at frame %u\n", g_avpe_frames);
    for (;;) {
        snprintf(p, sizeof p, "%s/req", dir);
        FILE *rq = fopen(p, "r");
        if (!rq) _exit(3);
        if (!fgets(line, sizeof line, rq)) { fclose(rq); continue; }
        fclose(rq);
        line[strcspn(line, "\r\n")] = 0;
        if (!strcmp(line, "quit")) { fflush(NULL); _exit(0); }
        char pf[512], sf[512], lf[512];
        if (sscanf(line, "%511s %511s %511s", pf, sf, lf) != 3) continue;
        fflush(NULL);
        pid_t c = fork();
        if (c == 0) {
            if (!freopen(lf, "w", stderr)) _exit(4);
            { int nv = private_guest_memory();             /* the parent's memory stays as it was paused */
              fprintf(stderr, "[PROBE] child: guest memory private (%d views)\n", nv);
              fflush(stderr);
              if (nv < 0) _exit(6); }
            setenv("AVPE_PROBE_STREAM", sf, 1); setenv("AVPE_PROBE_CHILD", "1", 1);
            if (getenv("AVPE_PROBE_ACK")) { extern void xbox_Nv2aAckRestart(void); xbox_Nv2aAckRestart(); }   /* fork kept only this thread: probes that render wait on the GPU */
            { const char *ct = getenv("AVPE_PROBE_CHILD_TIMEOUT"); alarm(ct ? (unsigned)atoi(ct) : 30); }   /* a probe that loops on bad input */
            probe_multi(pf);
            _exit(5);                                   /* probe_multi could not open its files */
        }
        int st = -1;
        if (c > 0) waitpid(c, &st, 0);
        snprintf(p, sizeof p, "%s/resp", dir);
        FILE *rs = fopen(p, "w");
        if (rs) { fprintf(rs, "done %d\n", st); fclose(rs); }
    }
}
#endif

void avpe_probe_tick(void)
{
    static int done; const char *spec = getenv("AVPE_PROBE");
    const char *multi = getenv("AVPE_PROBE_MULTI");
    if (!done && multi) {
        const char *fr = getenv("AVPE_PROBE_FRAME");
        if (g_avpe_frames < (uint32_t)(fr ? atol(fr) : 40)) return;
        done = 1;
        const char *srv = getenv("AVPE_PROBE_SERVER");
        if (srv) probe_server(srv);
        probe_multi(multi);
        return;
    }
    if (done || !spec) return;
    const char *fr = getenv("AVPE_PROBE_FRAME");
    if (g_avpe_frames < (uint32_t)(fr ? atol(fr) : 40)) return;
    done = 1;
    char buf[512]; strncpy(buf, spec, sizeof buf - 1); buf[sizeof buf - 1] = 0;
    uint32_t args[16]; int nargs = 0;
    uint32_t save[4] = { g_eax, g_ecx, g_edx, g_esp };
    uint32_t va = probe_setup(buf, args, &nargs);
    for (int i = nargs; i-- > 0;) PUSH32(g_esp, args[i]);
    PUSH32(g_esp, 0xDEC0DE00u);
    if (!s_verify_init) verify_init();
    s_verify_va = va; s_verify_n = s_verify_calls + 1;
    fprintf(stderr, "[PROBE] calling %08X (ecx=%08X eax=%08X, %d args)\n", va, g_ecx, g_eax, nargs);
    recomp_lookup(va)();
    g_eax = save[0]; g_ecx = save[1]; g_edx = save[2]; g_esp = save[3];
}

/* AVPE_VERIFY_STOP=<callee va>: end the capture when the decompiled function
 * (armed by AVPE_VERIFY or a probe) calls this translated function, before
 * the call. The oracle stops the original at the same callee's entry; memory,
 * the pushed arguments and the registers are compared at that instant. */
void avpe_verify_call(uint32_t callee, uint32_t nargs)
{
    if (s_noret_armed && s_capturing > 0 && !s_in_extern &&
        noret_in(s_noret_callees, (int)(sizeof s_noret_callees / 4), callee)) {
        fprintf(stderr, "[PROBE] call to %08X, which never returns: compared at its entry\n", callee);
        longjmp(s_noret_jb, 1);
    }
    if (s_stream && s_capturing > 0 && !s_in_extern && is_extern(callee)) {
        multi_record(3, callee, s_extern_seq);
        s_in_extern = callee; s_extern_esp = g_esp;
        return;
    }
    static long stop = -2;
    if (stop == -2) { const char *e = getenv("AVPE_VERIFY_STOP"); stop = e ? (long)strtoul(e, NULL, 16) : -1; }
    if (!s_verify_armed || (long)callee != stop) return;
    s_verify_armed = 0;
    char p[160]; snprintf(p, sizeof p, "/home/claude/avpe-recomp/verify/%08X_after.bin", s_verify_va);
    g_avpe_snap_extra = nargs;                    /* header word 11: callee argument count */
    avpe_snapshot(p);
    fprintf(stderr, "[VERIFY] %08X captured at call to %08X (%u args)\n", s_verify_va, callee, nargs);
    exit(0);
}

/* gcall's callee returned (before a cdecl caller pops its arguments). */
void avpe_verify_return(uint32_t callee)
{
    if (!s_in_extern || callee != s_in_extern || g_esp <= s_extern_esp) return;
    s_in_extern = 0;
    if (s_stream) multi_record(4, callee, s_extern_seq++);
}

/* CShell::ShellLoadLevel (0xF0BD0; LTCG: this in ecx, level name in eax).
 * Logs every level load. AVPE_LEVEL=<name> (e.g. "M01/background.tbd")
 * replaces the AVPE_LEVEL_AT-th load (default 2: the first attract demo after
 * the main menu) with that level: the native way into a mission before the
 * menus can be driven. */
extern void sub_000F0BD0_gen(void);
extern uint32_t xbox_ContiguousAlloc(uint32_t size, uint32_t alignment);
void sub_000F0BD0(void)
{
    static int n;
    char name[128]; int i = 0;
    for (; g_eax && i < 127 && MEM8(g_eax + i); i++) name[i] = (char)MEM8(g_eax + i);
    name[i] = 0;
    n++;
    { char tag[32]; snprintf(tag, sizeof tag, "before_load%d", n); avpe_coverage_dump(tag); }
    const char *lv = getenv("AVPE_LEVEL"), *at = getenv("AVPE_LEVEL_AT");
    if (lv && n == (at ? atoi(at) : 2)) {
        static uint32_t buf;
        if (!buf) buf = xbox_ContiguousAlloc(256, 16);
        size_t len = strlen(lv); if (len > 255) len = 255;
        for (size_t k = 0; k < len; k++) MEM8(buf + k) = (uint8_t)lv[k];
        MEM8(buf + len) = 0;
        fprintf(stderr, "[LEVEL] load #%d '%s' -> replaced with '%s' (frame %u)\n", n, name, lv, g_avpe_frames);
        g_eax = buf;
        { extern void avpe_soak_level(const char *); avpe_soak_level(lv); }        /* AVPE_SESSION: per-level stats (platform/pc_soak.cpp) */
    } else {
        { extern void avpe_soak_level(const char *); avpe_soak_level(name); }
        fprintf(stderr, "[LEVEL] load #%d '%s' (frame %u)\n", n, name, g_avpe_frames);
    }
    AVPE_DEC_ELSE_GEN(000F0BD0);
}

/* ---- Virtual controller (XAPI input HLE) --------------------------------
 * The Xbox USB stack talks to OHCI registers that are plain memory on the
 * Linux bench (the toolkit's register trap is Windows-only), so no pad ever
 * enumerates. These replace the XAPI device/input entry points the title
 * calls (XbSymbolDatabase addresses) with one gamepad on port 0.
 *
 * AVPE_PAD=<file>: lines appended while the game runs are pressed as they are
 * read: <button>[+<button>...][:<hold ms>] (default hold 150 ms).
 * Buttons: up down left right start back lthumb rthumb a b x y black white
 * lt rt; sticks lleft lright lup ldown rleft rright rup rdown (full throw).
 * A press is logged with the frame it landed on. */
#include <sys/time.h>
#define XDEVICE_TYPE_GAMEPAD 0x001FD9B0u     /* &g_DeviceType_Gamepad */
static FILE *s_pad_file;
static struct { uint16_t buttons; uint8_t analog[8]; int16_t lx, ly, rx, ry; uint64_t until_ms; } s_pad;
static uint32_t s_pad_packet;
static uint64_t now_ms(void) { struct timeval tv; gettimeofday(&tv, NULL); return (uint64_t)tv.tv_sec * 1000 + tv.tv_usec / 1000; }
static void pad_poll(void)
{
    const char *path = getenv("AVPE_PAD");
    /* A press that just ended is reported released for at least one poll, so
     * the game sees an edge between two presses of the same button. */
    if (s_pad.until_ms && now_ms() >= s_pad.until_ms) { memset(&s_pad, 0, sizeof s_pad); s_pad_packet++; return; }
    if (!path) return;
    if (!s_pad_file) s_pad_file = fopen(path, "r");
    if (!s_pad_file || s_pad.until_ms) return;          /* one press at a time */
    char line[128];
    clearerr(s_pad_file);
    if (!fgets(line, sizeof line, s_pad_file)) return;
    line[strcspn(line, "\r\n")] = 0;
    if (!line[0]) return;
    unsigned hold = 150; char *c = strchr(line, ':');
    if (c) { *c = 0; hold = (unsigned)atoi(c + 1); }
    memset(&s_pad, 0, sizeof s_pad);
    static const char *dig[] = { "up", "down", "left", "right", "start", "back", "lthumb", "rthumb" };
    static const char *ana[] = { "a", "b", "x", "y", "black", "white", "lt", "rt" };
    for (char *t = strtok(line, "+"); t; t = strtok(NULL, "+")) {
        for (int i = 0; i < 8; i++) if (!strcmp(t, dig[i])) s_pad.buttons |= 1u << i;
        for (int i = 0; i < 8; i++) if (!strcmp(t, ana[i])) s_pad.analog[i] = 0xFF;
        if (!strcmp(t, "lleft")) s_pad.lx = -32768; if (!strcmp(t, "lright")) s_pad.lx = 32767;
        if (!strcmp(t, "lup")) s_pad.ly = 32767;    if (!strcmp(t, "ldown")) s_pad.ly = -32768;
        if (!strcmp(t, "rleft")) s_pad.rx = -32768; if (!strcmp(t, "rright")) s_pad.rx = 32767;
        if (!strcmp(t, "rup")) s_pad.ry = 32767;    if (!strcmp(t, "rdown")) s_pad.ry = -32768;
    }
    s_pad.until_ms = now_ms() + hold;
    s_pad_packet++;
    fprintf(stderr, "[PAD] press '%s' for %u ms (frame %u)\n", line, hold, g_avpe_frames);
}

/* the PC platform layer (pc_input.cpp) presses the pad from the keyboard and SDL controllers */
__attribute__((weak)) void avpe_pc_pad(uint16_t *buttons, uint8_t analog[8], int16_t sticks[4]);

/* XInitDevices(count, PXDEVICE_PREALLOC_TYPE), stdcall 8. The library builds its USB
 * host stack here (OHCI init, hub and device tables, the memory-unit and gamepad
 * class drivers) so that the XPP calls below can talk to hardware. The pad
 * entry points are native (this file), nothing else in the game reads the
 * stack's tables, and there is no USB hardware: native start-up has nothing to
 * set up. AVPE_NATIVE_XPP=0 runs the lifted library as the reference. */
extern void sub_001FE288_gen(void);
static int native_xpp(void) { static int v = -1; if (v < 0) { const char *e = getenv("AVPE_NATIVE_XPP"); v = !(e && *e == '0'); } return v; }
void sub_001FE288(void)
{
    if (!native_xpp()) { sub_001FE288_gen(); return; }
    g_esp += 4 + 8;
}

/* DWORD XGetDevices(PXPP_DEVICE_TYPE) */
void sub_001FEB96(void) { uint32_t t = MEM32(g_esp + 4); g_eax = t == XDEVICE_TYPE_GAMEPAD ? 1u : 0u; g_esp += 8; }
/* BOOL XGetDeviceChanges(PXPP_DEVICE_TYPE, PDWORD insertions, PDWORD removals) */
void sub_001FEBB8(void)
{
    static int announced;
    uint32_t t = MEM32(g_esp + 4), ins = MEM32(g_esp + 8), rem = MEM32(g_esp + 12);
    uint32_t i = (t == XDEVICE_TYPE_GAMEPAD && !announced) ? 1u : 0u;
    if (i) announced = 1;
    if (ins) MEM32(ins) = i;
    if (rem) MEM32(rem) = 0;
    g_eax = i ? 1u : 0u;
    g_esp += 16;
}
/* HANDLE XInputOpen(PXPP_DEVICE_TYPE, DWORD port, DWORD slot, PXINPUT_POLLING_PARAMETERS) */
void sub_001FEC25(void)
{
    uint32_t t = MEM32(g_esp + 4), port = MEM32(g_esp + 8);
    g_eax = (t == XDEVICE_TYPE_GAMEPAD && port == 0) ? 0x00C0FFEEu : 0u;
    fprintf(stderr, "[PAD] XInputOpen(type %08X, port %u) -> %08X\n", t, port, g_eax);
    g_esp += 20;
}
/* VOID XInputClose(HANDLE) */
void sub_001FEC7B(void) { g_esp += 8; }
/* DWORD XInputGetCapabilities(HANDLE, PXINPUT_CAPABILITIES) */
void sub_001FEC87(void)
{
    uint32_t caps = MEM32(g_esp + 8);
    if (caps) { for (int i = 0; i < 0x16; i++) MEM8(caps + i) = 0; MEM8(caps) = 1; /* SubType: gamepad */ }
    g_eax = 0; g_esp += 12;
}
/* DWORD XInputGetState(HANDLE, PXINPUT_STATE): packet number, then
 * XINPUT_GAMEPAD { WORD buttons; BYTE analog[8]; SHORT lx, ly, rx, ry; } */
void sub_001FEE5F(void)
{
    uint32_t h = MEM32(g_esp + 4), st = MEM32(g_esp + 8);
    pad_poll();
    if (h != 0x00C0FFEEu || !st) { g_eax = 0x48F; /* ERROR_DEVICE_NOT_CONNECTED */ g_esp += 12; return; }
    uint16_t buttons = s_pad.buttons; uint8_t analog[8]; int16_t sticks[4] = { s_pad.lx, s_pad.ly, s_pad.rx, s_pad.ry };
    memcpy(analog, s_pad.analog, 8);
    if (!replay_pad(&buttons, analog, sticks) && avpe_pc_pad) avpe_pc_pad(&buttons, analog, sticks);     /* PC layer: keyboard, SDL controllers */
    /* a new packet number whenever the state changed */
    static uint8_t last[22];
    MEM32(st) = s_pad_packet;
    MEM16(st + 4) = buttons;
    for (int i = 0; i < 8; i++) MEM8(st + 6 + i) = analog[i];
    for (int i = 0; i < 4; i++) MEM16(st + 14 + 2 * i) = (uint16_t)sticks[i];
    if (memcmp(last + 4, (uint8_t *)XBOX_PTR(st + 4), 18)) { memcpy(last + 4, (uint8_t *)XBOX_PTR(st + 4), 18); MEM32(st) = ++s_pad_packet; }
    g_eax = 0; g_esp += 12;
}

/* Coverage checkpoints (instrumented build only; weak so the normal build links). */
__attribute__((weak)) void avpe_coverage_dump(const char *tag) { (void)tag; }

/* AVPE_SNAP_MISSION=<path>: snapshot guest RAM 100 frames after the
 * mission briefing closes (for comparing runs). */
uint32_t g_avpe_mission_frame;
void avpe_mission_tick(void)
{
    /* AVPE_COVERAGE_NOW=<file>: dump coverage tagged "now" each time it appears (then removed). */
    { static const char *t = (const char *)-1; if (t == (const char *)-1) t = getenv("AVPE_COVERAGE_NOW");
      if (t) { FILE *f = fopen(t, "r"); if (f) { fclose(f); remove(t); avpe_coverage_dump("now"); } } }
    static int done; const char *p = getenv("AVPE_SNAP_MISSION");
    const char *mf = getenv("AVPE_MISSION_FRAMES");
    if (done || !g_avpe_mission_frame || g_avpe_frames < g_avpe_mission_frame + (mf ? (uint32_t)atol(mf) : 100)) return;
    done = 1;
    avpe_coverage_dump("mission");
    if (!p) return;
    avpe_snapshot(p);
    fprintf(stderr, "[MISSION] snapshot at frame %u -> %s\n", g_avpe_frames, p);
}

/* GExitMissionGoalsButton constructor (0x69F50): at mission start it runs the
 * Mission Goals briefing as a modal loop until START. Logged so scripts know
 * when the briefing is waiting. */
extern void sub_00069F50_gen(void);
void sub_00069F50(void)
{
    fprintf(stderr, "[BRIEFING] mission goals up (frame %u, pad poll %u)\n", g_avpe_frames, g_avpe_pad_polls);
    if (!g_avpe_briefing_poll && g_avpe_frames > 10) g_avpe_briefing_poll = g_avpe_pad_polls ? g_avpe_pad_polls : 1;
    AVPE_DEC_ELSE_GEN(00069F50);
    fprintf(stderr, "[BRIEFING] closed (frame %u)\n", g_avpe_frames);
    if (g_avpe_frames > 10) g_avpe_mission_frame = g_avpe_frames;
}

/* Pause path tracing (cheat-code bring-up). */
/* The pause input handler: registered with the input device as a callback for
 * the "Pause" action (0x637A0), never called directly, so the disassembler
 * missed it until tools/seed_pointers.py. (0x16C190, which the symbol DB had
 * named Input_Pause, is a class-registration static initialiser.) */
extern void sub_000637F0_gen(void);
void sub_000637F0(void) { fprintf(stderr, "[PAUSE] pause handler this=%08X (frame %u)\n", g_ecx, g_avpe_frames); AVPE_DEC_OR_GEN(000637F0); }
extern void sub_000635C0_gen(void);
void sub_000635C0(void) { fprintf(stderr, "[PAUSE] GPauseHandler::Create (frame %u)\n", g_avpe_frames); AVPE_DEC_ELSE_GEN(000635C0); }
extern void sub_00063990_gen(void);
void sub_00063990(void) { fprintf(stderr, "[PAUSE] GPauseMenu::Create (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00063990); }
extern void sub_00064EC0_gen(void);
void sub_00064EC0(void) { fprintf(stderr, "[CHEAT] GCheatsMenu::Create (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00064EC0); }
extern void sub_000637A0_gen(void);
void sub_000637A0(void) { fprintf(stderr, "[PAUSE] pause handler registered, handler object eax=%08X (frame %u)\n", g_eax, g_avpe_frames); AVPE_DEC_ELSE_GEN(000637A0); }

/* Functions found by tools/seed_pointers.py (callbacks never called directly):
 * first call logged, and verification hooks so tools/verify.py can check the
 * translation against the original (AVPE_VERIFY=<va>:<n>). */
extern void sub_0004F220_gen(void);
void sub_0004F220(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0004F220 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0004F220); }
extern void sub_0007CDF0_gen(void);
void sub_0007CDF0(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0007CDF0 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0007CDF0); }
extern void sub_0007D240_gen(void);
void sub_0007D240(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0007D240 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0007D240); }
extern void sub_00095F70_gen(void);
void sub_00095F70(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 00095F70 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00095F70); }

/* Pause-menu cheat entry (GPauseMenu input handlers for CheatLeft/CheatRight).
 * The menu walks the string at *0x26B70C ("RLRRLRLLX") with its pointer at
 * this+0x2A0: L or R advances on a match and resets otherwise; a CheatRight
 * while the pointer is on 'X' unlocks the cheats. */
extern void sub_00063900_gen(void);
extern void sub_00063930_gen(void);
static void avpe_cheat_log(const char *k, uint32_t self, uint32_t before)
{
    uint32_t base = MEM32(0x26B70Cu), after = MEM32(self + 0x2A0u);
    fprintf(stderr, "[CHEAT] %s at pos %u ('%c') -> pos %u (frame %u)%s\n", k, before - base, MEM8(before),
            after - base, g_avpe_frames, (k[0] == 'R' && MEM8(before) == 'X') ? "  UNLOCKED" : "");
}
void sub_00063900(void) { uint32_t self = g_ecx, before = MEM32(self + 0x2A0u); AVPE_DEC_ELSE_GEN(00063900); avpe_cheat_log("L", self, before); }
void sub_00063930(void) { uint32_t self = g_ecx, before = MEM32(self + 0x2A0u); AVPE_DEC_ELSE_GEN(00063930); avpe_cheat_log("R", self, before); }

/* Menu tracing: which menu item has focus and which gets activated, by the
 * item's name CRC (this+0x110; tools/menulog.py turns CRCs into names). Lets
 * scripts drive menus without a picture. */
extern void sub_000E4340_gen(void);
extern void sub_000E7190_gen(void);
void sub_000E4340(void)
{
    uint32_t self = g_ecx, on = MEM8(g_esp + 4u);
    if (on) {
        uint32_t t = MEM32(self + 0x148u);   /* item text (SetText) */
        char buf[64] = "";
        for (int i = 0; t && i < 63 && MEM8(t + i); i++) buf[i] = (char)MEM8(t + i);
        fprintf(stderr, "[MENU] focus %08X item=%08X \"%s\" (frame %u)\n", MEM32(self + 0x110u), self, buf, g_avpe_frames);
    }
    AVPE_DEC_OR_GEN(000E4340);
}
extern int avpe_pc_menu_activate(uint32_t item);      /* pc_settings_menu.cpp: Quit Game */
void sub_000E7190(void)
{
    uint32_t item = MEM32(g_esp + 4u);
    fprintf(stderr, "[MENU] activate %08X item=%08X menu=%08X (frame %u)\n", item ? MEM32(item + 0x110u) : 0, item, g_ecx, g_avpe_frames);
    avpe_pc_menu_activate(item);
    AVPE_DEC_OR_GEN(000E7190);
}

extern void sub_00064F60_gen(void);
void sub_00064F60(void)
{
    uint32_t item = MEM32(g_esp + 4u);
    fprintf(stderr, "[CHEAT] menu picked %08X (frame %u)\n", item ? MEM32(item + 0x110u) : 0, g_avpe_frames);
    AVPE_DEC_OR_GEN(00064F60);
}
extern void sub_0005C250_gen(void);
void sub_0005C250(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0005C250 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0005C250); }
extern void sub_0005C560_gen(void);
void sub_0005C560(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0005C560 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0005C560); }
extern void sub_0007C890_gen(void);
void sub_0007C890(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0007C890 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0007C890); }
extern void sub_0007D150_gen(void);
void sub_0007D150(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0007D150 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0007D150); }
extern void sub_00170700_gen(void);
void sub_00170700(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 00170700 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00170700); }
extern void sub_0004DAD0_gen(void);
void sub_0004DAD0(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0004DAD0 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0004DAD0); }
extern void sub_0004F700_gen(void);
void sub_0004F700(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 0004F700 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(0004F700); }
extern void sub_00058260_gen(void);
void sub_00058260(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 00058260 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00058260); }
extern void sub_00020380_gen(void);
void sub_00020380(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 00020380 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00020380); }
extern void sub_00026220_gen(void);
void sub_00026220(void) { static int n; if (!n++) fprintf(stderr, "[SEEDFN] 00026220 first call (frame %u)\n", g_avpe_frames); AVPE_DEC_OR_GEN(00026220); }

/* XDK DirectSound (MCPX voice, thiscall, ret 4): per-channel volume and
 * mixbin set-up. It divides by the voice's channel count (byte [ecx+0x64])
 * before testing it for zero; with zero channels every later step is skipped
 * and the function changes nothing but the stack. On the Linux bench a voice
 * occasionally arrives with no channels yet (seen in M02, M09, P02, P08, P11)
 * and the division faults (SIGFPE); the same voice on the console would
 * fault too. Do what the function does for zero channels, minus the fault. */
extern void sub_001A4DE5_gen(void);
void sub_001A4DE5(void)
{
    if (MEM8(g_ecx + 0x64) == 0) {
        static int n;
        if (n++ < 20) fprintf(stderr, "[DSOUND] voice %08X has no channels: volume update skipped (frame %u)\n", g_ecx, g_avpe_frames);
        g_esp += 8;                                     /* ret 4 */
        return;
    }
    AVPE_DEC_ELSE_GEN(001A4DE5);
}
