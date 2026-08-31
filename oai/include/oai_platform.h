/* oai_platform.h -- Oai portability layer.
 *
 * Threads, atomics, high-resolution time, sleeping, dynamic library loading
 * and raw-mode terminal handling for POSIX and Windows behind one small API.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_PLATFORM_H
#define OAI_PLATFORM_H

#include <stddef.h>
#include <stdint.h>

#if defined(_WIN32) || defined(_WIN64)
#  define OAI_WINDOWS 1
#else
#  define OAI_POSIX 1
#endif

#ifdef __cplusplus
extern "C" {
#endif

/* ---------------------------------------------------------------- threads */

typedef struct oai_thread oai_thread;
typedef struct oai_mutex  oai_mutex;
typedef struct oai_cond   oai_cond;

/* Spawn a detachable worker. Returns NULL on failure. */
oai_thread *oai_thread_start(void (*fn)(void *), void *arg);
/* Block until the worker returns, then release it. Safe on NULL. */
void        oai_thread_join(oai_thread *t);

oai_mutex *oai_mutex_new(void);
void       oai_mutex_free(oai_mutex *m);
void       oai_mutex_lock(oai_mutex *m);
void       oai_mutex_unlock(oai_mutex *m);

/* Logical cores available to the process; always >= 1. */
/* Condition variables, used by the worker pool to park idle threads instead of
 * spinning. oai_cond_wait must be called with `m` held, as usual. */
oai_cond *oai_cond_new(void);
void      oai_cond_free(oai_cond *c);
void      oai_cond_wait(oai_cond *c, oai_mutex *m);
void      oai_cond_signal(oai_cond *c);
void      oai_cond_broadcast(oai_cond *c);

int oai_cpu_count(void);

/* --------------------------------------------------------------- atomics */

/* A 32-bit integer that may be written by one thread and read by another
 * without a lock. Used for the cancellation flag and live training stats. */
typedef struct { volatile int32_t v; } oai_atomic_i32;

void    oai_atomic_store(oai_atomic_i32 *a, int32_t value);
int32_t oai_atomic_load(const oai_atomic_i32 *a);
int32_t oai_atomic_add(oai_atomic_i32 *a, int32_t delta);
/* Sets *a to `desired` if it currently holds `expected`. Returns 1 on swap. */
int     oai_atomic_cas(oai_atomic_i32 *a, int32_t expected, int32_t desired);

/* ------------------------------------------------------------------ time */

/* Seconds since an unspecified epoch, monotonic, sub-millisecond resolution. */
double oai_time_now(void);
void   oai_sleep_ms(double ms);

/* --------------------------------------------------------- dynamic loading */

typedef void *oai_dl;
/* A generic function pointer. Symbols are returned as one of these rather than
 * as void*, because converting an object pointer to a function pointer is not
 * something ISO C allows -- the conversion is done once, safely, inside
 * oai_dl_sym. Cast the result to the signature you expect. */
typedef void (*oai_dl_func)(void);

/* Try each name in turn (e.g. "libOpenCL.so.1", "OpenCL.dll"); NULL if none. */
oai_dl      oai_dl_open(const char *const *candidates, int count);
oai_dl_func oai_dl_sym(oai_dl h, const char *name);
void        oai_dl_close(oai_dl h);

/* -------------------------------------------------------------- terminal */

/* Switch the terminal to unbuffered, non-echoing input and enable ANSI
 * sequences. Returns 0 on success; restores itself via oai_term_restore. */
int  oai_term_raw(void);
void oai_term_restore(void);
/* Current terminal size. Falls back to 80x24 when it cannot be determined. */
void oai_term_size(int *cols, int *rows);
/* Non-blocking key read. Returns 0 when no key is waiting, else a key code
 * (an ASCII byte, or one of the OAI_KEY_* values below). */
int  oai_term_getkey(void);

enum {
    OAI_KEY_NONE      = 0,
    OAI_KEY_ENTER     = 13,
    OAI_KEY_ESC       = 27,
    OAI_KEY_BACKSPACE = 127,
    OAI_KEY_UP        = 0x1000,
    OAI_KEY_DOWN      = 0x1001,
    OAI_KEY_LEFT      = 0x1002,
    OAI_KEY_RIGHT     = 0x1003,
    OAI_KEY_PGUP      = 0x1004,
    OAI_KEY_PGDN      = 0x1005,
    OAI_KEY_HOME      = 0x1006,
    OAI_KEY_END       = 0x1007,
    OAI_KEY_DELETE    = 0x1008
};

/* Install a Ctrl+C / termination handler that sets *flag to 1 instead of
 * killing the process, so training can unwind and checkpoint. */
void oai_install_signal_handler(oai_atomic_i32 *flag);

/* ------------------------------------------------------------ filesystem */

int  oai_mkdir_p(const char *path);
int  oai_file_exists(const char *path);
/* Reads a whole file into a malloc'd NUL-terminated buffer. NULL on failure. */
char *oai_read_file(const char *path, size_t *out_len);

#ifdef __cplusplus
}
#endif
#endif /* OAI_PLATFORM_H */
