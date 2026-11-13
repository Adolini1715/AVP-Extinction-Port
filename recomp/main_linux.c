/*
 * AvP: Extinction static recompilation - Linux host (OpenGL backend).
 *
 * Same boot order as templates/new-game/src/main.c (the Windows host):
 * load XBE, memory layout, kernel, path, kernel bridge, stack, dispatch,
 * watchdog, entry point. The Windows VEH crash handler is replaced by a
 * SIGSEGV/SIGILL/SIGFPE/SIGBUS handler that prints the guest register file.
 */
#define _GNU_SOURCE
#include <dlfcn.h>
#include <signal.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <execinfo.h>
#include <ucontext.h>
#include <xbox/xboxrecomp.h>

extern RECOMP_TLS uint32_t g_eax, g_ecx, g_edx, g_esp;
extern RECOMP_TLS uint32_t g_ebx, g_esi, g_edi;
extern ptrdiff_t g_xbox_mem_offset;
extern void xbe_entry_point(void);
extern int recomp_dispatch_init(void);
extern void xbox_path_init(const char *game_dir, const char *save_dir);

#define AVPE_ENTRY_POINT 0x001418E8u
#define AVPE_GAME_DIR    "game"
#define AVPE_XBE_PATH    "game/default.xbe"

static void on_fault(int sig, siginfo_t *si, void *uc_)
{
    ucontext_t *uc = (ucontext_t *)uc_;
    uintptr_t host_addr = (uintptr_t)si->si_addr;
    uintptr_t guest = host_addr - (uintptr_t)g_xbox_mem_offset;
    fprintf(stderr, "\n[CRASH] signal %d (%s) host addr %p", sig, strsignal(sig), si->si_addr);
    if (guest < 0x100000000ull) fprintf(stderr, " = guest 0x%08lX", (unsigned long)guest);
    fprintf(stderr, "\n[CRASH] host rip %p\n", (void *)uc->uc_mcontext.gregs[REG_RIP]);
    if (!uc->uc_mcontext.gregs[REG_RIP]) {               /* a call through a null pointer: who called */
        void *ra = *(void **)uc->uc_mcontext.gregs[REG_RSP];
        Dl_info di;
        if (dladdr(ra, &di) && di.dli_sname)
            fprintf(stderr, "[CRASH] called from %s+0x%lx\n", di.dli_sname, (unsigned long)((char *)ra - (char *)di.dli_saddr));
        else
            fprintf(stderr, "[CRASH] called from %p\n", ra);
    }
    fprintf(stderr, "[CRASH] guest eax=%08X ebx=%08X ecx=%08X edx=%08X esi=%08X edi=%08X esp=%08X\n",
            g_eax, g_ebx, g_ecx, g_edx, g_esi, g_edi, g_esp);
    void *bt[48]; int n = backtrace(bt, 48);
    backtrace_symbols_fd(bt, n, 2);
    _exit(128 + sig);
}

static int load_file(const char *path, void **data, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "Cannot open %s\n", path); return 0; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *p = malloc((size_t)n);
    if (!p || fread(p, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(p); return 0; }
    fclose(f); *data = p; *size = (size_t)n; return 1;
}

int main(void)
{
    { extern const char *avpe_session_autostart(void);   /* Linux: opt-in (AVPE_RECORD=1), so the verification runs stay untouched */
      if (getenv("AVPE_RECORD") && strcmp(getenv("AVPE_RECORD"), "0")) avpe_session_autostart(); }
    if (getenv("AVPE_LAUNCHER") && !strcmp(getenv("AVPE_LAUNCHER"), "1")) {   /* opt-in on Linux (tests) */
        extern int avpe_launcher_run(const char *build);
        if (avpe_launcher_run("PC build dev (Linux)") == 0) return 0;
    }
    setvbuf(stdout, NULL, _IONBF, 0); setvbuf(stderr, NULL, _IONBF, 0);
    /* Console-faithful NtFreeVirtualMemory. Without it every MEM_DECOMMIT
     * fails (the toolkit hands guest pointer slots to the host VirtualFree),
     * the XAPI heap takes its decommit-failed path, and its layout drifts from
     * the console's from the first level unload on. Found with the oracle
     * (tools/snap_replay.py): menu teardown diverged at heap call 149 of 483;
     * with reclaim on, all 483 calls and the final memory match the original.
     * Without it the title crashes at the fourth level change (frame ~6,600). */
    setenv("RECOMP_HEAP_RECLAIM", "1", 0);
    /* Execute the title's GPU command stream (toolkit's software NV2A
     * executor, with our anti-aliasing, semaphore and DMA-object fixes).
     * AVPE_NO_GPU=1 runs without it (fast, no pixels). The PC build
     * (AVPE_PC) draws with OpenGL instead and never runs it. */
    if (!getenv("AVPE_NO_GPU") && !getenv("AVPE_PC")) setenv("RECOMP_PB_EXEC", "1", 0);
    /* Without the executor the stand-in GPU only acknowledges commands, so
     * its thread can sleep between ticks instead of holding a core. */
    if (getenv("AVPE_PC")) setenv("RECOMP_NV2A_ACK_SLEEP_MS", "1", 0);
    printf("=== AvP: Extinction - static recompilation (Linux host) ===\n");

    struct sigaction sa; memset(&sa, 0, sizeof sa);
    sa.sa_sigaction = on_fault; sa.sa_flags = SA_SIGINFO;
    sigaction(SIGSEGV, &sa, NULL); sigaction(SIGILL, &sa, NULL);
    sigaction(SIGFPE, &sa, NULL); sigaction(SIGBUS, &sa, NULL);

    void *xbe; size_t xbe_size;
    if (!load_file(AVPE_XBE_PATH, &xbe, &xbe_size)) return 1;
    printf("XBE loaded: %zu bytes\n", xbe_size);

    if (!xbox_MemoryLayoutInit(xbe, xbe_size)) { fprintf(stderr, "memory layout init failed\n"); return 1; }
    g_xbox_mem_offset = xbox_GetMemoryOffset();
    printf("Xbox memory mapped, offset 0x%llX\n", (unsigned long long)g_xbox_mem_offset);

    xbox_kernel_init();
    /* Writable drives (E: saves, T:/U:/Z:, partition images) go under the
     * save dir, never into the disc image. AVPE_SAVE_DIR picks it (a fresh
     * one gives a run with no profile). */
    {
        const char *sd = getenv("AVPE_SAVE_DIR");
        const char *gd = getenv("AVPE_GAME_DIR");     /* an overlay of the disc, e.g. with a mod */
        xbox_path_init(gd ? gd : AVPE_GAME_DIR, sd ? sd : "saves");
    }
    xbox_kernel_bridge_init();
    g_esp = XBOX_STACK_TOP;
    if (!recomp_dispatch_init()) fprintf(stderr, "[BOOT] flat dispatch unavailable\n");
    xbox_WatchdogStart();

    printf("Entry 0x%08X, esp 0x%08X. Starting game...\n", AVPE_ENTRY_POINT, g_esp);
    xbe_entry_point();
    printf("Game returned.\n");
    xbox_kernel_shutdown();
    xbox_MemoryLayoutShutdown();
    return 0;
}
