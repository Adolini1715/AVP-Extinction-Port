/* Coverage runtime for the instrumented build (recomp/build_coverage.sh).
 * GCC's -finstrument-functions calls __cyg_profile_func_enter on entry to
 * every translated function; each distinct host function is recorded once.
 * avpe_coverage_dump(tag) writes the set so far to $AVPE_COVERAGE.<tag>
 * (host addresses; tools/coverage.py maps them back to Xbox functions). */
#define _GNU_SOURCE
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include <dlfcn.h>
#define SLOTS (1u << 16)
static void *s_seen[SLOTS];
static unsigned s_calls[SLOTS];   /* entries per function (AVPE_COVERAGE_COUNTS=1 writes them) */
static unsigned s_count;
__attribute__((no_instrument_function)) void __cyg_profile_func_enter(void *fn, void *site)
{
    (void)site;
    uintptr_t h = ((uintptr_t)fn >> 4) * 2654435761u;
    for (unsigned i = 0; i < SLOTS; i++) {
        unsigned k = (unsigned)(h + i) & (SLOTS - 1);
        void *cur = __atomic_load_n(&s_seen[k], __ATOMIC_RELAXED);
        if (cur == fn) { __atomic_add_fetch(&s_calls[k], 1, __ATOMIC_RELAXED); return; }
        if (!cur) {
            void *expected = NULL;
            if (__atomic_compare_exchange_n(&s_seen[k], &expected, fn, 0, __ATOMIC_RELAXED, __ATOMIC_RELAXED)) {
                __atomic_add_fetch(&s_count, 1, __ATOMIC_RELAXED);
                __atomic_add_fetch(&s_calls[k], 1, __ATOMIC_RELAXED); return;
            }
            if (expected == fn) { __atomic_add_fetch(&s_calls[k], 1, __ATOMIC_RELAXED); return; }
        }
    }
}
__attribute__((no_instrument_function)) void __cyg_profile_func_exit(void *fn, void *site) { (void)fn; (void)site; }
__attribute__((no_instrument_function)) void avpe_coverage_dump(const char *tag)
{
    const char *p = getenv("AVPE_COVERAGE");
    if (!p) return;
    char path[256]; snprintf(path, sizeof path, "%s.%s", p, tag);
    FILE *f = fopen(path, "w"); if (!f) return;
    Dl_info di;
    const char *cc = getenv("AVPE_COVERAGE_COUNTS");
    for (unsigned k = 0; k < SLOTS; k++) {
        void *fn = s_seen[k];
        if (!(fn && dladdr(fn, &di) && di.dli_sname)) continue;
        if (cc) fprintf(f, "%s %u\n", di.dli_sname, s_calls[k]);
        else fprintf(f, "%s\n", di.dli_sname);
    }
    fclose(f);
    fprintf(stderr, "[COVERAGE] %s: %u functions so far\n", path, s_count);
}

/* A run that ends normally (replay frames done) also writes $AVPE_COVERAGE.exit. */
__attribute__((no_instrument_function, destructor)) static void cov_at_exit(void) { avpe_coverage_dump("exit"); }
