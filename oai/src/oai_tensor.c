/* oai_tensor.c -- matrix storage and the small linear algebra kernels.
 *
 * The matmuls are blocked and accumulate in registers; that is enough to keep
 * a CPU-only build responsive at the model sizes Oai ships with. When a GPU is
 * available oai_gpu.c takes over the same three shapes.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_tensor.h"
#include "oai_pool.h"

#include <math.h>
#include <stdlib.h>
#include <string.h>

/* ==================================================================== rng */

void oai_rng_seed(oai_rng *r, unsigned int seed)
{
    r->s = seed ? seed : 0x9E3779B9u;
}

static unsigned int oai_rng_next(oai_rng *r)
{
    unsigned int x = r->s;
    x ^= x << 13;
    x ^= x >> 17;
    x ^= x << 5;
    r->s = x;
    return x;
}

float oai_rng_uniform(oai_rng *r)
{
    return (float)(oai_rng_next(r) >> 8) * (1.0f / 16777216.0f);
}

float oai_rng_normal(oai_rng *r)
{
    /* Box-Muller. u is nudged off zero so the log stays finite. */
    float u = oai_rng_uniform(r);
    float v = oai_rng_uniform(r);
    if (u < 1e-7f) u = 1e-7f;
    return sqrtf(-2.0f * logf(u)) * cosf(6.2831853f * v);
}

int oai_rng_below(oai_rng *r, int limit)
{
    if (limit <= 0) return 0;
    return (int)(oai_rng_next(r) % (unsigned int)limit);
}

/* ================================================================ storage */

int oai_mat_init(oai_mat *m, int rows, int cols)
{
    m->rows = rows;
    m->cols = cols;
    m->data = (float *)calloc((size_t)rows * (size_t)cols, sizeof(float));
    return m->data ? 0 : -1;
}

void oai_mat_free(oai_mat *m)
{
    if (!m) return;
    free(m->data);
    m->data = NULL;
    m->rows = m->cols = 0;
}

void oai_mat_zero(oai_mat *m)
{
    if (m->data)
        memset(m->data, 0, (size_t)m->rows * (size_t)m->cols * sizeof(float));
}

void oai_mat_fill(oai_mat *m, float value)
{
    size_t i, n = (size_t)m->rows * (size_t)m->cols;
    for (i = 0; i < n; ++i) m->data[i] = value;
}

void oai_mat_copy(oai_mat *dst, const oai_mat *src)
{
    size_t n = (size_t)src->rows * (size_t)src->cols;
    if (dst->rows != src->rows || dst->cols != src->cols) return;
    memcpy(dst->data, src->data, n * sizeof(float));
}

void oai_mat_randomize(oai_mat *m, oai_rng *rng, float scale)
{
    size_t i, n = (size_t)m->rows * (size_t)m->cols;
    for (i = 0; i < n; ++i) m->data[i] = oai_rng_normal(rng) * scale;
}

/* ================================================================ matmuls */

/* Each matmul is written as a body that handles a slice of the output and a
 * wrapper that hands the slice range to the worker pool. With --threads 1 (or
 * a small shape) the pool calls the body directly, so there is one code path
 * either way. */

#define OAI_BLOCK 64

typedef struct {
    const float *A;
    const float *B;
    float       *C;
    int          m, k, n;
} mm_args;

/* C = A*B, over output rows [begin, end). */
static void mm_nn_body(void *arg, int begin, int end)
{
    const mm_args *a = (const mm_args *)arg;
    int i, j, p, jj, pp;

    memset(a->C + (size_t)begin * a->n, 0,
           (size_t)(end - begin) * (size_t)a->n * sizeof(float));

    for (i = begin; i < end; ++i) {
        const float *arow = a->A + (size_t)i * a->k;
        float *crow = a->C + (size_t)i * a->n;
        for (pp = 0; pp < a->k; pp += OAI_BLOCK) {
            int pmax = pp + OAI_BLOCK < a->k ? pp + OAI_BLOCK : a->k;
            for (jj = 0; jj < a->n; jj += OAI_BLOCK) {
                int jmax = jj + OAI_BLOCK < a->n ? jj + OAI_BLOCK : a->n;
                for (p = pp; p < pmax; ++p) {
                    float av = arow[p];
                    const float *brow = a->B + (size_t)p * a->n;
                    if (av == 0.0f) continue;
                    for (j = jj; j < jmax; ++j) crow[j] += av * brow[j];
                }
            }
        }
    }
}

void oai_matmul(const float *A, const float *B, float *C, int m, int k, int n)
{
    mm_args a;
    a.A = A; a.B = B; a.C = C; a.m = m; a.k = k; a.n = n;
    oai_pool_parallel_for(mm_nn_body, &a, m, 8);
}

/* C = A*B^T, over output rows [begin, end). */
static void mm_nt_body(void *arg, int begin, int end)
{
    const mm_args *a = (const mm_args *)arg;
    int i, j, p;

    for (i = begin; i < end; ++i) {
        const float *arow = a->A + (size_t)i * a->k;
        float *crow = a->C + (size_t)i * a->n;
        for (j = 0; j < a->n; ++j) {
            const float *brow = a->B + (size_t)j * a->k;
            float sum = 0.0f;
            for (p = 0; p < a->k; ++p) sum += arow[p] * brow[p];
            crow[j] = sum;
        }
    }
}

void oai_matmul_nt(const float *A, const float *B, float *C, int m, int k, int n)
{
    mm_args a;
    a.A = A; a.B = B; a.C = C; a.m = m; a.k = k; a.n = n;
    oai_pool_parallel_for(mm_nt_body, &a, m, 8);
}

/* C += A^T*B, split over output *columns* [begin, end).
 *
 * Splitting by column rather than by row is what makes this safe to run in
 * parallel: the natural loop order accumulates into every row of C on each
 * pass over k, so two threads owning different rows would still collide on
 * the same cache lines. Owning disjoint column bands, they never touch the
 * same float. */
static void mm_tn_body(void *arg, int begin, int end)
{
    const mm_args *a = (const mm_args *)arg;
    int i, j, p;

    for (p = 0; p < a->k; ++p) {
        const float *arow = a->A + (size_t)p * a->m;   /* A is (k x m) */
        const float *brow = a->B + (size_t)p * a->n;
        for (i = 0; i < a->m; ++i) {
            float av = arow[i];
            float *crow = a->C + (size_t)i * a->n;
            if (av == 0.0f) continue;
            for (j = begin; j < end; ++j) crow[j] += av * brow[j];
        }
    }
}

void oai_matmul_tn_acc(const float *A, const float *B, float *C,
                       int m, int k, int n)
{
    mm_args a;
    a.A = A; a.B = B; a.C = C; a.m = m; a.k = k; a.n = n;
    oai_pool_parallel_for(mm_tn_body, &a, n, 16);
}

/* ============================================================== elementwise */

void oai_add_bias(float *C, const float *bias, int rows, int cols)
{
    int i, j;
    for (i = 0; i < rows; ++i) {
        float *row = C + (size_t)i * cols;
        for (j = 0; j < cols; ++j) row[j] += bias[j];
    }
}

void oai_sum_rows(const float *A, float *out, int rows, int cols)
{
    int i, j;
    for (j = 0; j < cols; ++j) out[j] = 0.0f;
    for (i = 0; i < rows; ++i) {
        const float *row = A + (size_t)i * cols;
        for (j = 0; j < cols; ++j) out[j] += row[j];
    }
}

void oai_tanh_inplace(float *x, size_t n)
{
    size_t i;
    for (i = 0; i < n; ++i) x[i] = tanhf(x[i]);
}

void oai_tanh_backward(const float *y, const float *dy, float *dx, size_t n)
{
    size_t i;
    for (i = 0; i < n; ++i) dx[i] = (1.0f - y[i] * y[i]) * dy[i];
}

void oai_softmax_rows(float *x, int rows, int cols)
{
    int i, j;
    for (i = 0; i < rows; ++i) {
        float *row = x + (size_t)i * cols;
        float max = row[0], sum = 0.0f;
        for (j = 1; j < cols; ++j) if (row[j] > max) max = row[j];
        for (j = 0; j < cols; ++j) {
            row[j] = expf(row[j] - max);
            sum += row[j];
        }
        if (sum <= 0.0f) sum = 1e-9f;
        for (j = 0; j < cols; ++j) row[j] /= sum;
    }
}

float oai_l2_norm(const float *x, size_t n)
{
    size_t i;
    double sum = 0.0;
    for (i = 0; i < n; ++i) sum += (double)x[i] * (double)x[i];
    return (float)sqrt(sum);
}

void oai_scale(float *x, size_t n, float factor)
{
    size_t i;
    for (i = 0; i < n; ++i) x[i] *= factor;
}

void oai_axpy(float *y, const float *x, size_t n, float a)
{
    size_t i;
    for (i = 0; i < n; ++i) y[i] += a * x[i];
}
