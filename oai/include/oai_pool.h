/* oai_pool.h -- a fixed worker pool for splitting one loop across cores.
 *
 * The pool exists to make --threads mean something: the matmuls in
 * oai_tensor.c hand it a row or column range and it runs the pieces in
 * parallel. Workers park on a condition variable between tasks, so an idle
 * pool costs nothing.
 *
 * The pool is process-wide and is never used re-entrantly -- only the three
 * matmuls call it, and they do not nest.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_POOL_H
#define OAI_POOL_H

#ifdef __cplusplus
extern "C" {
#endif

/* Body of a parallel loop: handle indices [begin, end). */
typedef void (*oai_pool_fn)(void *arg, int begin, int end);

/* Starts `threads` workers (0 or 1 means "run everything inline"; a negative
 * value asks for one per core). Returns the number of threads that will
 * actually run work, including the calling thread. */
int  oai_pool_init(int threads);
void oai_pool_shutdown(void);
int  oai_pool_threads(void);

/* Splits [0, total) across the pool and returns once every piece is done.
 * Falls back to a direct call when the pool is not running, when `total` is
 * small, or when called from inside another parallel region. */
void oai_pool_parallel_for(oai_pool_fn fn, void *arg, int total,
                           int min_per_thread);

#ifdef __cplusplus
}
#endif
#endif /* OAI_POOL_H */
