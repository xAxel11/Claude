/* oai_pool.c -- the worker pool behind --threads.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_pool.h"
#include "oai_platform.h"

#include <stdlib.h>
#include <string.h>

#define OAI_POOL_MAX 64

typedef struct {
    oai_pool_fn fn;
    void       *arg;
    int         total;
    int         chunks;      /* how many pieces this task is split into */
} pool_task;

static struct {
    int          nthreads;   /* including the calling thread */
    int          nworkers;   /* spawned threads */
    oai_thread  *workers[OAI_POOL_MAX];
    oai_mutex   *lock;
    oai_cond    *work_ready;
    oai_cond    *work_done;
    pool_task    task;
    long         generation; /* bumped for every dispatched task */
    int          next_chunk;
    int          outstanding;
    int          shutting_down;
    int          in_region;  /* guards against accidental nesting */
    int          running;
} P;

/* Runs chunks from the current task until none are left. */
static void run_chunks(long generation)
{
    for (;;) {
        int chunk, begin, end, total, chunks;
        oai_pool_fn fn;
        void *arg;

        oai_mutex_lock(P.lock);
        if (P.generation != generation || P.next_chunk >= P.task.chunks) {
            oai_mutex_unlock(P.lock);
            return;
        }
        chunk  = P.next_chunk++;
        fn     = P.task.fn;
        arg    = P.task.arg;
        total  = P.task.total;
        chunks = P.task.chunks;
        oai_mutex_unlock(P.lock);

        begin = (int)((long)total * chunk / chunks);
        end   = (int)((long)total * (chunk + 1) / chunks);
        if (end > begin) fn(arg, begin, end);

        oai_mutex_lock(P.lock);
        if (--P.outstanding == 0) oai_cond_broadcast(P.work_done);
        oai_mutex_unlock(P.lock);
    }
}

static void pool_worker(void *arg)
{
    long seen = 0;
    (void)arg;
    for (;;) {
        long generation;
        oai_mutex_lock(P.lock);
        while (P.generation == seen && !P.shutting_down)
            oai_cond_wait(P.work_ready, P.lock);
        if (P.shutting_down) { oai_mutex_unlock(P.lock); return; }
        generation = P.generation;
        seen = generation;
        oai_mutex_unlock(P.lock);

        run_chunks(generation);
    }
}

int oai_pool_init(int threads)
{
    int i;

    oai_pool_shutdown();
    memset(&P, 0, sizeof P);

    if (threads < 0) threads = oai_cpu_count();
    if (threads > OAI_POOL_MAX) threads = OAI_POOL_MAX;
    if (threads <= 1) {
        P.nthreads = 1;
        return 1;
    }

    P.lock       = oai_mutex_new();
    P.work_ready = oai_cond_new();
    P.work_done  = oai_cond_new();
    if (!P.lock || !P.work_ready || !P.work_done) {
        oai_pool_shutdown();
        P.nthreads = 1;
        return 1;
    }

    P.nthreads = threads;
    P.running  = 1;
    for (i = 0; i < threads - 1; ++i) {
        P.workers[i] = oai_thread_start(pool_worker, NULL);
        if (!P.workers[i]) break;
        P.nworkers++;
    }
    P.nthreads = P.nworkers + 1;
    return P.nthreads;
}

void oai_pool_shutdown(void)
{
    int i;
    if (P.running) {
        oai_mutex_lock(P.lock);
        P.shutting_down = 1;
        P.generation++;
        oai_cond_broadcast(P.work_ready);
        oai_mutex_unlock(P.lock);
        for (i = 0; i < P.nworkers; ++i) oai_thread_join(P.workers[i]);
    }
    oai_cond_free(P.work_ready);
    oai_cond_free(P.work_done);
    oai_mutex_free(P.lock);
    memset(&P, 0, sizeof P);
    P.nthreads = 1;
}

int oai_pool_threads(void)
{
    return P.nthreads > 0 ? P.nthreads : 1;
}

void oai_pool_parallel_for(oai_pool_fn fn, void *arg, int total,
                           int min_per_thread)
{
    int chunks;
    long generation;

    if (total <= 0) return;
    if (min_per_thread < 1) min_per_thread = 1;

    if (!P.running || P.nthreads <= 1 || P.in_region
        || total < min_per_thread * 2) {
        fn(arg, 0, total);
        return;
    }

    chunks = total / min_per_thread;
    if (chunks > P.nthreads) chunks = P.nthreads;
    if (chunks < 2) { fn(arg, 0, total); return; }

    oai_mutex_lock(P.lock);
    P.in_region     = 1;
    P.task.fn       = fn;
    P.task.arg      = arg;
    P.task.total    = total;
    P.task.chunks   = chunks;
    P.next_chunk    = 0;
    P.outstanding   = chunks;
    generation      = ++P.generation;
    oai_cond_broadcast(P.work_ready);
    oai_mutex_unlock(P.lock);

    /* The calling thread is a worker too, so it never idles while others run. */
    run_chunks(generation);

    oai_mutex_lock(P.lock);
    while (P.outstanding > 0) oai_cond_wait(P.work_done, P.lock);
    P.in_region = 0;
    oai_mutex_unlock(P.lock);
}
