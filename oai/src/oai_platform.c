/* oai_platform.c -- POSIX and Windows implementations of the Oai portability
 * layer. Everything platform-specific in Oai lives here; the rest of the code
 * base is plain C99.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_platform.h"

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <signal.h>
#include <errno.h>
#include <sys/stat.h>

#ifdef OAI_WINDOWS
#  define WIN32_LEAN_AND_MEAN
#  include <windows.h>
#  include <io.h>
#  include <direct.h>
#  include <conio.h>
#else
#  include <unistd.h>
#  include <pthread.h>
#  include <dlfcn.h>
#  include <termios.h>
#  include <time.h>
#  include <fcntl.h>
#  include <sys/ioctl.h>
#endif

/* ================================================================ threads */

struct oai_thread {
#ifdef OAI_WINDOWS
    HANDLE h;
#else
    pthread_t h;
#endif
    void (*fn)(void *);
    void *arg;
};

struct oai_mutex {
#ifdef OAI_WINDOWS
    CRITICAL_SECTION cs;
#else
    pthread_mutex_t m;
#endif
};

struct oai_cond {
#ifdef OAI_WINDOWS
    CONDITION_VARIABLE cv;
#else
    pthread_cond_t c;
#endif
};

#ifdef OAI_WINDOWS
static DWORD WINAPI oai_thread_trampoline(LPVOID p)
{
    oai_thread *t = (oai_thread *)p;
    t->fn(t->arg);
    return 0;
}
#else
static void *oai_thread_trampoline(void *p)
{
    oai_thread *t = (oai_thread *)p;
    t->fn(t->arg);
    return NULL;
}
#endif

oai_thread *oai_thread_start(void (*fn)(void *), void *arg)
{
    oai_thread *t = (oai_thread *)calloc(1, sizeof *t);
    if (!t) return NULL;
    t->fn = fn;
    t->arg = arg;
#ifdef OAI_WINDOWS
    t->h = CreateThread(NULL, 0, oai_thread_trampoline, t, 0, NULL);
    if (!t->h) { free(t); return NULL; }
#else
    if (pthread_create(&t->h, NULL, oai_thread_trampoline, t) != 0) {
        free(t);
        return NULL;
    }
#endif
    return t;
}

void oai_thread_join(oai_thread *t)
{
    if (!t) return;
#ifdef OAI_WINDOWS
    WaitForSingleObject(t->h, INFINITE);
    CloseHandle(t->h);
#else
    pthread_join(t->h, NULL);
#endif
    free(t);
}

oai_mutex *oai_mutex_new(void)
{
    oai_mutex *m = (oai_mutex *)calloc(1, sizeof *m);
    if (!m) return NULL;
#ifdef OAI_WINDOWS
    InitializeCriticalSection(&m->cs);
#else
    pthread_mutex_init(&m->m, NULL);
#endif
    return m;
}

void oai_mutex_free(oai_mutex *m)
{
    if (!m) return;
#ifdef OAI_WINDOWS
    DeleteCriticalSection(&m->cs);
#else
    pthread_mutex_destroy(&m->m);
#endif
    free(m);
}

void oai_mutex_lock(oai_mutex *m)
{
    if (!m) return;
#ifdef OAI_WINDOWS
    EnterCriticalSection(&m->cs);
#else
    pthread_mutex_lock(&m->m);
#endif
}

void oai_mutex_unlock(oai_mutex *m)
{
    if (!m) return;
#ifdef OAI_WINDOWS
    LeaveCriticalSection(&m->cs);
#else
    pthread_mutex_unlock(&m->m);
#endif
}

oai_cond *oai_cond_new(void)
{
    oai_cond *c = (oai_cond *)calloc(1, sizeof *c);
    if (!c) return NULL;
#ifdef OAI_WINDOWS
    InitializeConditionVariable(&c->cv);
#else
    pthread_cond_init(&c->c, NULL);
#endif
    return c;
}

void oai_cond_free(oai_cond *c)
{
    if (!c) return;
#ifndef OAI_WINDOWS
    pthread_cond_destroy(&c->c);
#endif
    free(c);
}

void oai_cond_wait(oai_cond *c, oai_mutex *m)
{
    if (!c || !m) return;
#ifdef OAI_WINDOWS
    SleepConditionVariableCS(&c->cv, &m->cs, INFINITE);
#else
    pthread_cond_wait(&c->c, &m->m);
#endif
}

void oai_cond_signal(oai_cond *c)
{
    if (!c) return;
#ifdef OAI_WINDOWS
    WakeConditionVariable(&c->cv);
#else
    pthread_cond_signal(&c->c);
#endif
}

void oai_cond_broadcast(oai_cond *c)
{
    if (!c) return;
#ifdef OAI_WINDOWS
    WakeAllConditionVariable(&c->cv);
#else
    pthread_cond_broadcast(&c->c);
#endif
}

int oai_cpu_count(void)
{
#ifdef OAI_WINDOWS
    SYSTEM_INFO si;
    GetSystemInfo(&si);
    return si.dwNumberOfProcessors > 0 ? (int)si.dwNumberOfProcessors : 1;
#else
    long n = sysconf(_SC_NPROCESSORS_ONLN);
    return n > 0 ? (int)n : 1;
#endif
}

/* ================================================================ atomics */

void oai_atomic_store(oai_atomic_i32 *a, int32_t value)
{
#ifdef OAI_WINDOWS
    InterlockedExchange((volatile LONG *)&a->v, (LONG)value);
#else
    __sync_lock_test_and_set(&a->v, value);
#endif
}

int32_t oai_atomic_load(const oai_atomic_i32 *a)
{
#ifdef OAI_WINDOWS
    return (int32_t)InterlockedCompareExchange(
        (volatile LONG *)&((oai_atomic_i32 *)a)->v, 0, 0);
#else
    __sync_synchronize();
    return a->v;
#endif
}

int32_t oai_atomic_add(oai_atomic_i32 *a, int32_t delta)
{
#ifdef OAI_WINDOWS
    return (int32_t)InterlockedExchangeAdd((volatile LONG *)&a->v, (LONG)delta)
           + delta;
#else
    return __sync_add_and_fetch(&a->v, delta);
#endif
}

int oai_atomic_cas(oai_atomic_i32 *a, int32_t expected, int32_t desired)
{
#ifdef OAI_WINDOWS
    return InterlockedCompareExchange((volatile LONG *)&a->v, (LONG)desired,
                                      (LONG)expected) == (LONG)expected;
#else
    return __sync_bool_compare_and_swap(&a->v, expected, desired);
#endif
}

/* =================================================================== time */

double oai_time_now(void)
{
#ifdef OAI_WINDOWS
    static LARGE_INTEGER freq;
    LARGE_INTEGER now;
    if (freq.QuadPart == 0) QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&now);
    return (double)now.QuadPart / (double)freq.QuadPart;
#else
    struct timespec ts;
#  if defined(CLOCK_MONOTONIC)
    clock_gettime(CLOCK_MONOTONIC, &ts);
#  else
    clock_gettime(CLOCK_REALTIME, &ts);
#  endif
    return (double)ts.tv_sec + (double)ts.tv_nsec * 1e-9;
#endif
}

void oai_sleep_ms(double ms)
{
    if (ms <= 0.0) return;
#ifdef OAI_WINDOWS
    Sleep((DWORD)(ms + 0.5));
#else
    struct timespec ts;
    ts.tv_sec  = (time_t)(ms / 1000.0);
    ts.tv_nsec = (long)((ms - (double)ts.tv_sec * 1000.0) * 1e6);
    nanosleep(&ts, NULL);
#endif
}

/* ========================================================= dynamic loading */

oai_dl oai_dl_open(const char *const *candidates, int count)
{
    int i;
    for (i = 0; i < count; ++i) {
#ifdef OAI_WINDOWS
        HMODULE h = LoadLibraryA(candidates[i]);
        if (h) return (oai_dl)h;
#else
        void *h = dlopen(candidates[i], RTLD_NOW | RTLD_LOCAL);
        if (h) return (oai_dl)h;
#endif
    }
    return NULL;
}

oai_dl_func oai_dl_sym(oai_dl h, const char *name)
{
    if (!h) return NULL;
#ifdef OAI_WINDOWS
    return (oai_dl_func)GetProcAddress((HMODULE)h, name);
#else
    /* The POSIX-sanctioned dance: dlsym returns void*, and the only portable
     * way to land it in a function pointer is through the object
     * representation. */
    {
        oai_dl_func fp;
        void *sym = dlsym(h, name);
        memcpy(&fp, &sym, sizeof fp);
        return fp;
    }
#endif
}

void oai_dl_close(oai_dl h)
{
    if (!h) return;
#ifdef OAI_WINDOWS
    FreeLibrary((HMODULE)h);
#else
    dlclose(h);
#endif
}

/* =============================================================== terminal */

static int g_term_raw_active = 0;

#ifdef OAI_WINDOWS
static DWORD g_saved_in_mode, g_saved_out_mode;
static UINT  g_saved_cp;
#else
static struct termios g_saved_termios;
static int            g_saved_flags;
#endif

int oai_term_raw(void)
{
    if (g_term_raw_active) return 0;
#ifdef OAI_WINDOWS
    {
        HANDLE hi = GetStdHandle(STD_INPUT_HANDLE);
        HANDLE ho = GetStdHandle(STD_OUTPUT_HANDLE);
        DWORD in_mode, out_mode;
        if (!GetConsoleMode(hi, &in_mode) || !GetConsoleMode(ho, &out_mode))
            return -1;
        g_saved_in_mode  = in_mode;
        g_saved_out_mode = out_mode;
        g_saved_cp       = GetConsoleOutputCP();
        SetConsoleOutputCP(65001); /* UTF-8 */
        in_mode &= (DWORD)~(ENABLE_LINE_INPUT | ENABLE_ECHO_INPUT
                            | ENABLE_PROCESSED_INPUT);
        SetConsoleMode(hi, in_mode);
#       ifndef ENABLE_VIRTUAL_TERMINAL_PROCESSING
#         define ENABLE_VIRTUAL_TERMINAL_PROCESSING 0x0004
#       endif
        SetConsoleMode(ho, out_mode | ENABLE_VIRTUAL_TERMINAL_PROCESSING);
    }
#else
    {
        struct termios t;
        if (!isatty(STDIN_FILENO)) return -1;
        if (tcgetattr(STDIN_FILENO, &g_saved_termios) != 0) return -1;
        t = g_saved_termios;
        t.c_lflag &= (tcflag_t)~(ICANON | ECHO | ISIG);
        t.c_iflag &= (tcflag_t)~(IXON | ICRNL);
        t.c_cc[VMIN]  = 0;
        t.c_cc[VTIME] = 0;
        if (tcsetattr(STDIN_FILENO, TCSANOW, &t) != 0) return -1;
        g_saved_flags = fcntl(STDIN_FILENO, F_GETFL, 0);
        fcntl(STDIN_FILENO, F_SETFL, g_saved_flags | O_NONBLOCK);
    }
#endif
    g_term_raw_active = 1;
    return 0;
}

void oai_term_restore(void)
{
    if (!g_term_raw_active) return;
#ifdef OAI_WINDOWS
    SetConsoleMode(GetStdHandle(STD_INPUT_HANDLE), g_saved_in_mode);
    SetConsoleMode(GetStdHandle(STD_OUTPUT_HANDLE), g_saved_out_mode);
    SetConsoleOutputCP(g_saved_cp);
#else
    tcsetattr(STDIN_FILENO, TCSANOW, &g_saved_termios);
    fcntl(STDIN_FILENO, F_SETFL, g_saved_flags);
#endif
    g_term_raw_active = 0;
}

void oai_term_size(int *cols, int *rows)
{
    int c = 80, r = 24;
#ifdef OAI_WINDOWS
    CONSOLE_SCREEN_BUFFER_INFO csbi;
    if (GetConsoleScreenBufferInfo(GetStdHandle(STD_OUTPUT_HANDLE), &csbi)) {
        c = csbi.srWindow.Right - csbi.srWindow.Left + 1;
        r = csbi.srWindow.Bottom - csbi.srWindow.Top + 1;
    }
#else
    struct winsize ws;
    if (ioctl(STDOUT_FILENO, TIOCGWINSZ, &ws) == 0 && ws.ws_col > 0) {
        c = ws.ws_col;
        r = ws.ws_row;
    }
#endif
    if (c < 40) c = 40;
    if (r < 10) r = 10;
    if (cols) *cols = c;
    if (rows) *rows = r;
}

#ifdef OAI_WINDOWS
int oai_term_getkey(void)
{
    if (!_kbhit()) return OAI_KEY_NONE;
    {
        int c = _getch();
        if (c == 0 || c == 224) {           /* extended scan code */
            int e = _getch();
            switch (e) {
            case 72: return OAI_KEY_UP;
            case 80: return OAI_KEY_DOWN;
            case 75: return OAI_KEY_LEFT;
            case 77: return OAI_KEY_RIGHT;
            case 73: return OAI_KEY_PGUP;
            case 81: return OAI_KEY_PGDN;
            case 71: return OAI_KEY_HOME;
            case 79: return OAI_KEY_END;
            case 83: return OAI_KEY_DELETE;
            default: return OAI_KEY_NONE;
            }
        }
        if (c == 8) return OAI_KEY_BACKSPACE;
        if (c == '\r') return OAI_KEY_ENTER;
        return c;
    }
}
#else
static int oai_read_byte(unsigned char *out)
{
    ssize_t n = read(STDIN_FILENO, out, 1);
    return n == 1;
}

int oai_term_getkey(void)
{
    unsigned char c;
    if (!oai_read_byte(&c)) return OAI_KEY_NONE;
    if (c != 0x1b) {
        if (c == '\n' || c == '\r') return OAI_KEY_ENTER;
        if (c == 8) return OAI_KEY_BACKSPACE;
        return (int)c;
    }
    /* Possible escape sequence. A lone ESC arrives with nothing behind it. */
    {
        unsigned char a, b;
        if (!oai_read_byte(&a)) return OAI_KEY_ESC;
        if (a != '[' && a != 'O') return OAI_KEY_ESC;
        if (!oai_read_byte(&b)) return OAI_KEY_ESC;
        switch (b) {
        case 'A': return OAI_KEY_UP;
        case 'B': return OAI_KEY_DOWN;
        case 'C': return OAI_KEY_RIGHT;
        case 'D': return OAI_KEY_LEFT;
        case 'H': return OAI_KEY_HOME;
        case 'F': return OAI_KEY_END;
        default: break;
        }
        if (b >= '1' && b <= '6') {
            unsigned char tail;
            int code = OAI_KEY_NONE;
            switch (b) {
            case '1': code = OAI_KEY_HOME;   break;
            case '3': code = OAI_KEY_DELETE; break;
            case '4': code = OAI_KEY_END;    break;
            case '5': code = OAI_KEY_PGUP;   break;
            case '6': code = OAI_KEY_PGDN;   break;
            default:  code = OAI_KEY_NONE;   break;
            }
            while (oai_read_byte(&tail) && tail != '~') { /* drain */ }
            return code;
        }
        return OAI_KEY_ESC;
    }
}
#endif

/* ================================================================ signals */

static oai_atomic_i32 *g_signal_flag = NULL;

static void oai_signal_handler(int sig)
{
    (void)sig;
    if (g_signal_flag) oai_atomic_store(g_signal_flag, 1);
}

void oai_install_signal_handler(oai_atomic_i32 *flag)
{
    g_signal_flag = flag;
    signal(SIGINT, oai_signal_handler);
    signal(SIGTERM, oai_signal_handler);
}

/* ============================================================= filesystem */

int oai_mkdir_p(const char *path)
{
    char buf[1024];
    size_t len, i;
    if (!path) return -1;
    len = strlen(path);
    if (len == 0 || len >= sizeof buf) return -1;
    memcpy(buf, path, len + 1);
    for (i = 1; i < len; ++i) {
        if (buf[i] == '/' || buf[i] == '\\') {
            char saved = buf[i];
            buf[i] = '\0';
#ifdef OAI_WINDOWS
            _mkdir(buf);
#else
            mkdir(buf, 0777);
#endif
            buf[i] = saved;
        }
    }
#ifdef OAI_WINDOWS
    if (_mkdir(buf) != 0 && errno != EEXIST) return -1;
#else
    if (mkdir(buf, 0777) != 0 && errno != EEXIST) return -1;
#endif
    return 0;
}

int oai_file_exists(const char *path)
{
    FILE *f;
    if (!path) return 0;
    f = fopen(path, "rb");
    if (!f) return 0;
    fclose(f);
    return 1;
}

char *oai_read_file(const char *path, size_t *out_len)
{
    FILE *f = fopen(path, "rb");
    long size;
    char *buf;
    size_t got;
    if (!f) return NULL;
    if (fseek(f, 0, SEEK_END) != 0) { fclose(f); return NULL; }
    size = ftell(f);
    if (size < 0) { fclose(f); return NULL; }
    rewind(f);
    buf = (char *)malloc((size_t)size + 1);
    if (!buf) { fclose(f); return NULL; }
    got = fread(buf, 1, (size_t)size, f);
    fclose(f);
    buf[got] = '\0';
    if (out_len) *out_len = got;
    return buf;
}
