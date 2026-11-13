/*
 * AvP: Extinction static recompilation - Windows host (the PC build).
 *
 * Same boot order as main_linux.c: load XBE, memory layout, kernel, paths,
 * kernel bridge, stack, dispatch, watchdog, entry point. Differences:
 *   - the working directory is the executable's folder, so the game files
 *     are found in game\ next to avpe.exe and saves go to saves\;
 *   - the PC platform layer (AVPE_PC: OpenGL, SDL input and audio) is on
 *     unless AVPE_PC=0;
 *   - crashes are reported by an unhandled-exception filter.
 */
#include <windows.h>
#include <mmsystem.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdint.h>
#include <io.h>
#include <xbox/xboxrecomp.h>

extern RECOMP_TLS uint32_t g_eax, g_ecx, g_edx, g_esp;
extern RECOMP_TLS uint32_t g_ebx, g_esi, g_edi;
extern ptrdiff_t g_xbox_mem_offset;
extern void xbe_entry_point(void);
extern int recomp_dispatch_init(void);
extern void xbox_path_init(const char *game_dir, const char *save_dir);

#ifndef AVPE_BUILD_ID
#define AVPE_BUILD_ID "dev"
#endif
#define AVPE_ENTRY_POINT 0x001418E8u
#define AVPE_GAME_DIR    "game"
#define AVPE_XBE_PATH    "game\\default.xbe"

static LONG WINAPI on_fault(EXCEPTION_POINTERS *ep)
{
    const EXCEPTION_RECORD *er = ep->ExceptionRecord;
    fprintf(stderr, "\n[CRASH] exception 0x%08lX at host %p", er->ExceptionCode, er->ExceptionAddress);
    if (er->ExceptionCode == EXCEPTION_ACCESS_VIOLATION && er->NumberParameters >= 2) {
        uintptr_t host = (uintptr_t)er->ExceptionInformation[1];
        uintptr_t guest = host - (uintptr_t)g_xbox_mem_offset;
        fprintf(stderr, ", %s host %p", er->ExceptionInformation[0] ? "writing" : "reading", (void *)host);
        if (guest < 0x100000000ull) fprintf(stderr, " = guest 0x%08lX", (unsigned long)guest);
    }
    fprintf(stderr, "\n[CRASH] guest eax=%08X ebx=%08X ecx=%08X edx=%08X esi=%08X edi=%08X esp=%08X\n",
            g_eax, g_ebx, g_ecx, g_edx, g_esi, g_edi, g_esp);
    /* the faulting address as an offset into avpe.exe: symbolized against the
     * unstripped build kept with the release (addr2line -e avpe.exe 0x140000000+off) */
    {
        HMODULE exe = GetModuleHandleA(NULL);
        fprintf(stderr, "[CRASH] avpe.exe+0x%llX (thread %lu)\n",
                (unsigned long long)((uintptr_t)er->ExceptionAddress - (uintptr_t)exe), GetCurrentThreadId());
    }
    fflush(stderr);
    {   /* AVPE_SESSION: close the session's books and keep the game's memory next to them */
        extern void avpe_soak_crash(const char *what);
        extern const char *avpe_session_dir(void);
        char what[64];
        snprintf(what, sizeof what, "exception 0x%08lX", er->ExceptionCode);
        avpe_soak_crash(what);
        const char *dir = avpe_session_dir();
        /* No Windows minidump: it stores every module's full path and the
         * environment (the player's user name). The game's own memory instead
         * (the Xbox side, 64 MB, the format the verification tools read) with
         * the registers above in avpe.log: enough to rebuild the moment. */
        if (dir && !getenv("AVPE_NO_GUESTDUMP")) {
            char path[MAX_PATH];
            snprintf(path, sizeof path, "%s\\guest.bin", dir);
            HANDLE f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, FILE_ATTRIBUTE_NORMAL, NULL);
            if (f != INVALID_HANDLE_VALUE) {
                const uint8_t *g = (const uint8_t *)(uintptr_t)g_xbox_mem_offset;
                DWORD done = 0, total = 0;
                static uint8_t zero[0x10000];
                for (uint32_t off = 0; off < 0x04000000u; off += 0x10000u) {
                    MEMORY_BASIC_INFORMATION mi;     /* an unmapped chunk is written as zeros: offsets stay guest addresses */
                    int ok = VirtualQuery(g + off, &mi, sizeof mi) && mi.State == MEM_COMMIT &&
                             !(mi.Protect & (PAGE_NOACCESS | PAGE_GUARD));
                    if (!WriteFile(f, ok ? g + off : zero, 0x10000u, &done, NULL)) break;
                    total += done;
                }
                CloseHandle(f);
                fprintf(stderr, "[CRASH] guest memory written (%lu bytes): guest.bin\n", (unsigned long)total);
            }
        }
    }
    fflush(stderr);
    {   extern void avpe_session_scrub(void);       /* the log and guest.bin are complete: take identity out, check */
        avpe_session_scrub();
    }
    return EXCEPTION_CONTINUE_SEARCH;
}

static int load_file(const char *path, void **data, size_t *size)
{
    FILE *f = fopen(path, "rb");
    if (!f) { fprintf(stderr, "Cannot open %s (the game files go in a folder named game next to avpe.exe)\n", path); return 0; }
    fseek(f, 0, SEEK_END); long n = ftell(f); fseek(f, 0, SEEK_SET);
    void *p = malloc((size_t)n);
    if (!p || fread(p, 1, (size_t)n, f) != (size_t)n) { fclose(f); free(p); return 0; }
    fclose(f); *data = p; *size = (size_t)n; return 1;
}

static void set_default(const char *name, const char *value)
{
    if (!getenv(name)) _putenv_s(name, value);
}

/* ---- the settings window, before the game starts ---------------------------
 * Resolution, window or fullscreen, widescreen, frame cap and mouse speed,
 * written to avpe.ini (other lines there are kept), and the key list. It
 * shows at every start unless "Show at start-up" is cleared; holding Shift
 * while starting shows it again. Returns 0 when the player chose Quit. */
enum { ID_RES = 101, ID_MODE, ID_WIDE, ID_FPS, ID_MOUSE, ID_SHOW, ID_PLAY, ID_QUIT };
static int s_settingsResult = -1;
static void settings_read(HWND w);
static LRESULT CALLBACK settings_proc(HWND w, UINT msg, WPARAM wp, LPARAM lp)
{
    switch (msg) {
    case WM_COMMAND:
        if (LOWORD(wp) == ID_PLAY || LOWORD(wp) == IDOK) { settings_read(w); s_settingsResult = 1; DestroyWindow(w); return 0; }
        if (LOWORD(wp) == ID_QUIT || LOWORD(wp) == IDCANCEL) { s_settingsResult = 0; DestroyWindow(w); return 0; }
        break;
    case WM_CLOSE: s_settingsResult = 0; DestroyWindow(w); return 0;
    case WM_DESTROY: PostQuitMessage(0); return 0;
    }
    return DefWindowProcA(w, msg, wp, lp);
}

static char s_iniKeep[64][256];
static int s_iniKeepN;
static char s_iniVal[8][64];
static const char *kIniKeys[] = { "AVPE_PC_SIZE", "AVPE_PC_RES", "AVPE_PC_FULLSCREEN", "AVPE_PC_WIDE", "AVPE_PC_FPS", "AVPE_PC_MOUSE", "AVPE_SHOW_SETTINGS" };
enum { K_SIZE, K_RES, K_FULL, K_WIDE, K_FPS, K_MOUSE, K_SHOW, K_N };
static void ini_load(void)
{
    FILE *f = fopen("avpe.ini", "r");
    char line[256];
    s_iniKeepN = 0;
    for (int i = 0; i < K_N; i++) s_iniVal[i][0] = 0;
    while (f && fgets(line, sizeof line, f)) {
        line[strcspn(line, "\r\n")] = 0;
        int ours = -1;
        for (int i = 0; i < K_N; i++) {
            size_t n = strlen(kIniKeys[i]);
            if (!strncmp(line, kIniKeys[i], n) && line[n] == '=') { ours = i; snprintf(s_iniVal[i], sizeof s_iniVal[i], "%s", line + n + 1); }
        }
        if (ours < 0 && s_iniKeepN < 64) snprintf(s_iniKeep[s_iniKeepN++], 256, "%s", line);
    }
    if (f) fclose(f);
}
static void ini_save(void)
{
    FILE *f = fopen("avpe.ini", "w");
    if (!f) return;
    for (int i = 0; i < s_iniKeepN; i++) fprintf(f, "%s\n", s_iniKeep[i]);
    for (int i = 0; i < K_N; i++) if (s_iniVal[i][0]) fprintf(f, "%s=%s\n", kIniKeys[i], s_iniVal[i]);
    fclose(f);
}

static void settings_read(HWND w)
{
    char buf[64];
    int full = (int)SendDlgItemMessageA(w, ID_MODE, CB_GETCURSEL, 0, 0) == 1;
    int k = (int)SendDlgItemMessageA(w, ID_RES, CB_GETCURSEL, 0, 0);
    buf[0] = 0;
    if (k > 0) SendDlgItemMessageA(w, ID_RES, CB_GETLBTEXT, k, (LPARAM)buf);
    buf[strcspn(buf, " ")] = 0;                                 /* "WxH", or "" for the desktop's */
    snprintf(s_iniVal[K_FULL], 64, "%d", full);
    snprintf(s_iniVal[K_SIZE], 64, "%s", full ? "" : buf);
    snprintf(s_iniVal[K_RES], 64, "%s", full ? buf : "");
    snprintf(s_iniVal[K_WIDE], 64, "%d", IsDlgButtonChecked(w, ID_WIDE) == BST_CHECKED);
    snprintf(s_iniVal[K_SHOW], 64, "%d", IsDlgButtonChecked(w, ID_SHOW) == BST_CHECKED);
    SendDlgItemMessageA(w, ID_FPS, CB_GETLBTEXT, SendDlgItemMessageA(w, ID_FPS, CB_GETCURSEL, 0, 0), (LPARAM)s_iniVal[K_FPS]);
    SendDlgItemMessageA(w, ID_MOUSE, CB_GETLBTEXT, SendDlgItemMessageA(w, ID_MOUSE, CB_GETCURSEL, 0, 0), (LPARAM)s_iniVal[K_MOUSE]);
    ini_save();
}

static int settings_window(void)
{
    ini_load();
    /* shown when asked for: AVPE_SHOW_SETTINGS=1 in avpe.ini, or Shift held
     * while starting; the game has its own settings screen on F10 */
    if (strcmp(s_iniVal[K_SHOW], "1") && !(GetAsyncKeyState(VK_SHIFT) & 0x8000))
        return 1;
    HINSTANCE hi = GetModuleHandleA(NULL);
    WNDCLASSA wc = {0};
    wc.lpfnWndProc = settings_proc; wc.hInstance = hi; wc.lpszClassName = "AvPESettings";
    wc.hbrBackground = (HBRUSH)(COLOR_BTNFACE + 1); wc.hCursor = LoadCursor(NULL, IDC_ARROW);
    wc.hIcon = LoadIcon(NULL, IDI_APPLICATION);
    RegisterClassA(&wc);
    HDC dc = GetDC(NULL);
    int dpi = GetDeviceCaps(dc, LOGPIXELSY);
    ReleaseDC(NULL, dc);
#define S(v) MulDiv(v, dpi, 96)
    RECT r = { 0, 0, S(560), S(470) };
    DWORD style = WS_OVERLAPPED | WS_CAPTION | WS_SYSMENU | WS_MINIMIZEBOX;
    AdjustWindowRect(&r, style, FALSE);
    int ww = r.right - r.left, wh = r.bottom - r.top;
    HWND w = CreateWindowA("AvPESettings", "Aliens versus Predator: Extinction - Settings", style,
                           (GetSystemMetrics(SM_CXSCREEN) - ww) / 2, (GetSystemMetrics(SM_CYSCREEN) - wh) / 2, ww, wh, NULL, NULL, hi, NULL);
    if (!w) return 1;
    HFONT font = CreateFontA(-S(13), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, 0, "Segoe UI");
    HFONT mono = CreateFontA(-S(12), 0, 0, 0, FW_NORMAL, 0, 0, 0, DEFAULT_CHARSET, 0, 0, CLEARTYPE_QUALITY, FIXED_PITCH, "Consolas");
#define CTL(cls, text, st, x, y, cx, cy, id) \
    SendMessageA(CreateWindowA(cls, text, WS_CHILD | WS_VISIBLE | (st), S(x), S(y), S(cx), S(cy), w, (HMENU)(INT_PTR)(id), hi, NULL), WM_SETFONT, (WPARAM)font, TRUE)
    CTL("STATIC", "Resolution", 0, 16, 18, 110, 20, 0);
    CTL("COMBOBOX", "", CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, 130, 14, 160, 300, ID_RES);
    CTL("STATIC", "Display", 0, 16, 50, 110, 20, 0);
    CTL("COMBOBOX", "", CBS_DROPDOWNLIST | WS_TABSTOP, 130, 46, 160, 100, ID_MODE);
    CTL("STATIC", "Frame rate cap", 0, 16, 82, 110, 20, 0);
    CTL("COMBOBOX", "", CBS_DROPDOWNLIST | WS_TABSTOP, 130, 78, 160, 150, ID_FPS);
    CTL("STATIC", "Mouse speed", 0, 16, 114, 110, 20, 0);
    CTL("COMBOBOX", "", CBS_DROPDOWNLIST | WS_VSCROLL | WS_TABSTOP, 130, 110, 160, 250, ID_MOUSE);
    CTL("BUTTON", "Widescreen (16:9 view)", BS_AUTOCHECKBOX | WS_TABSTOP, 310, 14, 230, 22, ID_WIDE);
    CTL("BUTTON", "Show this window at every start\n(or hold Shift while starting; F10 in the game)", BS_AUTOCHECKBOX | BS_MULTILINE | WS_TABSTOP, 310, 46, 230, 40, ID_SHOW);
    HWND keys = CreateWindowA("EDIT",
        "IN A MISSION\r\n"
        "Left click      select (hold: circle)  Shift+click   add to selection\r\n"
        "Right click     move/attack/pick up/trophy/repair\r\n"
        "Shift+right     attack-move            Alt+right     waypoint\r\n"
        "Wheel           zoom                   Middle drag   turn, tilt camera\r\n"
        "WASD, arrows    scroll                 Minimap       click to jump\r\n"
        "Q               special ability; opens/closes Uplink and Shrine\r\n"
        "E               second ability (Predator heal, Queen egg sack)\r\n"
        "Z X C V         stance: Aggressive, Defensive, Stand Ground, No Attack\r\n"
        "T F G           order: Patrol, Follow, Gather\r\n"
        "Ctrl+1..4       make group             1..4          select group\r\n"
        "Space           latest event           H             base\r\n"
        "Alt (hold)      order menu             X             confirm order (Uplink)\r\n"
        "R  camera preset   Esc  pause   Shift+Esc  quit   F10  settings\r\n"
        "\r\n"
        "MENUS: mouse, or arrows, Enter/Space = select, Esc/Backspace = back.\r\n"
        "A controller works at the same time (Xbox layout; LB White, RB Black).",
        WS_CHILD | WS_VISIBLE | WS_BORDER | ES_MULTILINE | ES_READONLY | WS_VSCROLL, S(16), S(150), S(528), S(262), w, NULL, hi, NULL);
    SendMessageA(keys, WM_SETFONT, (WPARAM)mono, TRUE);
    CTL("BUTTON", "Play", BS_DEFPUSHBUTTON | WS_TABSTOP, 352, 426, 90, 30, ID_PLAY);
    CTL("BUTTON", "Quit", BS_PUSHBUTTON | WS_TABSTOP, 454, 426, 90, 30, ID_QUIT);

    /* resolutions: the desktop's first, then every mode of the main display */
    HWND res = GetDlgItem(w, ID_RES);
    DEVMODEA dm = {0}; dm.dmSize = sizeof dm;
    EnumDisplaySettingsA(NULL, ENUM_CURRENT_SETTINGS, &dm);
    int dw = (int)dm.dmPelsWidth, dh = (int)dm.dmPelsHeight;
    char buf[64], cur[64] = "";
    snprintf(buf, sizeof buf, "%dx%d (desktop)", dw, dh);
    SendMessageA(res, CB_ADDSTRING, 0, (LPARAM)buf);
    int modes[256][2], nm = 0;
    for (DWORD i = 0; EnumDisplaySettingsA(NULL, i, &dm) && nm < 256; i++) {
        int mw = (int)dm.dmPelsWidth, mh = (int)dm.dmPelsHeight, dup = mw < 640 || mh < 480 || (mw == dw && mh == dh);
        for (int k = 0; k < nm && !dup; k++) dup = modes[k][0] == mw && modes[k][1] == mh;
        if (!dup) { modes[nm][0] = mw; modes[nm][1] = mh; nm++; }
    }
    for (int a = 0; a < nm; a++)                                /* largest first */
        for (int b = a + 1; b < nm; b++)
            if (modes[b][0] * modes[b][1] > modes[a][0] * modes[a][1]) { int t0 = modes[a][0], t1 = modes[a][1]; modes[a][0] = modes[b][0]; modes[a][1] = modes[b][1]; modes[b][0] = t0; modes[b][1] = t1; }
    for (int k = 0; k < nm; k++) { snprintf(buf, sizeof buf, "%dx%d", modes[k][0], modes[k][1]); SendMessageA(res, CB_ADDSTRING, 0, (LPARAM)buf); }
    int full = !strcmp(s_iniVal[K_FULL], "1");
    snprintf(cur, sizeof cur, "%s", full ? s_iniVal[K_RES] : s_iniVal[K_SIZE]);
    int sel = 0;
    if (cur[0]) { int n2 = (int)SendMessageA(res, CB_GETCOUNT, 0, 0); for (int k = 1; k < n2; k++) { SendMessageA(res, CB_GETLBTEXT, k, (LPARAM)buf); if (!strcmp(buf, cur)) sel = k; } }
    SendMessageA(res, CB_SETCURSEL, sel, 0);
    HWND mode = GetDlgItem(w, ID_MODE);
    SendMessageA(mode, CB_ADDSTRING, 0, (LPARAM)"Window");
    SendMessageA(mode, CB_ADDSTRING, 0, (LPARAM)"Fullscreen");
    SendMessageA(mode, CB_SETCURSEL, full, 0);
    static const char *fps[] = { "30", "60", "120", "144", "240" };
    HWND hf = GetDlgItem(w, ID_FPS);
    int fsel = 1;
    for (int k = 0; k < 5; k++) { SendMessageA(hf, CB_ADDSTRING, 0, (LPARAM)fps[k]); if (!strcmp(s_iniVal[K_FPS], fps[k])) fsel = k; }
    SendMessageA(hf, CB_SETCURSEL, fsel, 0);
    static const char *mouse[] = { "0.50", "0.75", "1.00", "1.25", "1.50", "2.00", "2.50", "3.00" };
    HWND hm = GetDlgItem(w, ID_MOUSE);
    int msel = 2;
    for (int k = 0; k < 8; k++) { SendMessageA(hm, CB_ADDSTRING, 0, (LPARAM)mouse[k]); if (s_iniVal[K_MOUSE][0] && atof(s_iniVal[K_MOUSE]) == atof(mouse[k])) msel = k; }
    SendMessageA(hm, CB_SETCURSEL, msel, 0);
    CheckDlgButton(w, ID_WIDE, strcmp(s_iniVal[K_WIDE], "0") ? BST_CHECKED : BST_UNCHECKED);
    CheckDlgButton(w, ID_SHOW, !strcmp(s_iniVal[K_SHOW], "1") ? BST_CHECKED : BST_UNCHECKED);
    ShowWindow(w, SW_SHOW);
    SetForegroundWindow(w);
    SetFocus(GetDlgItem(w, ID_PLAY));

    MSG m;
    while (GetMessageA(&m, NULL, 0, 0) > 0) {
        if (s_settingsResult < 0 && IsDialogMessageA(w, &m)) continue;
        TranslateMessage(&m);
        DispatchMessageA(&m);
        if (s_settingsResult >= 0 && !IsWindow(w)) break;
    }
    return s_settingsResult;
#undef CTL
#undef S
}

int main(int argc, char **argv)
{
    (void)argc; (void)argv;
    /* run from the executable's folder */
    char exe[MAX_PATH];
    DWORD n = GetModuleFileNameA(NULL, exe, MAX_PATH);
    if (n && n < MAX_PATH) {
        char *slash = strrchr(exe, '\\');
        if (slash) { *slash = 0; SetCurrentDirectoryA(exe); }
    }
    /* the launcher (platform/pc_launcher.cpp: Play, quick settings, recording); the old
     * settings window only when the launcher cannot open a window */
    if (!getenv("AVPE_NO_SETTINGS")) {
        extern int avpe_launcher_run(const char *build);
        int r = avpe_launcher_run("PC build " AVPE_BUILD_ID);
        if (r == 0) return 0;
        if (r < 0 && !settings_window()) return 0;
    }
    /* avpe.ini next to the exe: KEY=VALUE lines, the same settings as the
     * environment variables (which win when both are given) */
    {
        FILE *ini = fopen("avpe.ini", "r");
        char line[512];
        while (ini && fgets(line, sizeof line, ini)) {
            char *e = line + strcspn(line, "\r\n");
            *e = 0;
            char *k = line;
            while (*k == ' ' || *k == '\t') k++;
            if (!*k || *k == '#' || *k == ';' || *k == '[') continue;
            char *eq = strchr(k, '=');
            if (!eq) continue;
            *eq = 0;
            char *v = eq + 1, *ke = eq;
            while (ke > k && (ke[-1] == ' ' || ke[-1] == '\t')) *--ke = 0;
            while (*v == ' ' || *v == '\t') v++;
            if (!getenv(k)) _putenv_s(k, v);
        }
        if (ini) fclose(ini);
    }
    /* The runtime logs a great deal: to avpe.log (a console scrolling it
     * would cost frames). AVPE_CONSOLE=1 shows a console instead. */
    extern const char *avpe_session_autostart(void);
    const char *sess = avpe_session_autostart();
    if (getenv("AVPE_CONSOLE") && AllocConsole()) {
        freopen("CONOUT$", "w", stdout);
        freopen("CONOUT$", "w", stderr);
    } else {
        /* Both streams append to one file. (Pointing stderr's descriptor at
         * stdout's with _dup2 lost everything written to stderr under
         * Whisky's Wine: only stdout's lines reached the file.) */
        /* the play-test recorder (pc_soak.cpp) starts here unless AVPE_RECORD=0: the log goes in its folder */
        char logpath[MAX_PATH];
        snprintf(logpath, sizeof logpath, "%s%savpe.log", sess ? sess : "", sess ? "\\" : "");
        FILE *t = fopen(logpath, "w");
        if (t) fclose(t);
        freopen(logpath, "a", stdout);
        freopen(logpath, "a", stderr);
    }
    /* unbuffered: a log has to survive the process being killed */
    setvbuf(stdout, NULL, _IONBF, 0); setvbuf(stderr, NULL, _IONBF, 0);
    /* AVPE_PC=0 runs the Xbox code paths only (no window, no input, no sound) */
    if (getenv("AVPE_PC") && !strcmp(getenv("AVPE_PC"), "0")) _putenv_s("AVPE_PC", "");   /* "" removes it */
    else set_default("AVPE_PC", "1");
    /* see main_linux.c: console-faithful decommit (heap layout), and the
     * stand-in GPU only acknowledges commands, so its thread may sleep */
    set_default("RECOMP_HEAP_RECLAIM", "1");
    if (getenv("AVPE_PC")) set_default("RECOMP_NV2A_ACK_SLEEP_MS", "1");
#ifndef AVPE_BUILD_ID
#define AVPE_BUILD_ID "dev"
#endif
    printf("=== AvP: Extinction - static recompilation (Windows host) ===\n");
    printf("build %s\n", AVPE_BUILD_ID);     /* date and the two repos' commits (build_windows.sh) */

    SetUnhandledExceptionFilter(on_fault);
    /* 1 ms timer resolution: the frame cap sleeps (pc_timing.cpp), and
     * Windows rounds sleeps up to 15.6 ms otherwise */
    timeBeginPeriod(1);

    void *xbe; size_t xbe_size;
    if (!load_file(AVPE_XBE_PATH, &xbe, &xbe_size)) { MessageBoxA(NULL, "game\\default.xbe not found next to avpe.exe.", "AvP: Extinction", MB_OK | MB_ICONERROR); return 1; }
    printf("XBE loaded: %zu bytes\n", xbe_size);

    if (!xbox_MemoryLayoutInit(xbe, xbe_size)) { fprintf(stderr, "memory layout init failed\n"); return 1; }
    g_xbox_mem_offset = xbox_GetMemoryOffset();
    printf("Xbox memory mapped, offset 0x%llX\n", (unsigned long long)g_xbox_mem_offset);

    xbox_kernel_init();
    {
        const char *sd = getenv("AVPE_SAVE_DIR");
        const char *gd = getenv("AVPE_GAME_DIR");
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
