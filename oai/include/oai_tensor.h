/* oai_tensor.h -- dense float matrices and the linear algebra Oai needs.
 *
 * Deliberately minimal: a row-major matrix, the handful of BLAS-like calls the
 * model uses, and the random helpers used to initialise it. Every routine is
 * CPU-side; the GPU path in oai_gpu.h mirrors the matmuls.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_TENSOR_H
#define OAI_TENSOR_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int    rows;
    int    cols;
    float *data;   /* rows*cols floats, row-major, owned */
} oai_mat;

/* Deterministic xorshift generator, so a seed reproduces a run exactly. */
typedef struct { unsigned int s; } oai_rng;

void  oai_rng_seed(oai_rng *r, unsigned int seed);
float oai_rng_uniform(oai_rng *r);            /* [0,1) */
float oai_rng_normal(oai_rng *r);             /* mean 0, stddev 1 */
int   oai_rng_below(oai_rng *r, int limit);   /* [0,limit) */

int   oai_mat_init(oai_mat *m, int rows, int cols);
void  oai_mat_free(oai_mat *m);
void  oai_mat_zero(oai_mat *m);
void  oai_mat_fill(oai_mat *m, float value);
void  oai_mat_copy(oai_mat *dst, const oai_mat *src);
/* Kaiming-style init scaled by 1/sqrt(fan_in). */
void  oai_mat_randomize(oai_mat *m, oai_rng *rng, float scale);

/* C = A * B, all row-major. A is (m x k), B is (k x n), C is (m x n). */
void oai_matmul(const float *A, const float *B, float *C, int m, int k, int n);
/* C = A * B^T. B is (n x k). */
void oai_matmul_nt(const float *A, const float *B, float *C, int m, int k, int n);
/* C = A^T * B. A is (k x m). Accumulates into C rather than overwriting. */
void oai_matmul_tn_acc(const float *A, const float *B, float *C,
                       int m, int k, int n);

void  oai_add_bias(float *C, const float *bias, int rows, int cols);
void  oai_sum_rows(const float *A, float *out, int rows, int cols);
void  oai_tanh_inplace(float *x, size_t n);
/* dx += (1 - y^2) * dy, the tanh backward pass. */
void  oai_tanh_backward(const float *y, const float *dy, float *dx, size_t n);
/* Row-wise softmax, in place. */
void  oai_softmax_rows(float *x, int rows, int cols);
float oai_l2_norm(const float *x, size_t n);
void  oai_scale(float *x, size_t n, float factor);
void  oai_axpy(float *y, const float *x, size_t n, float a);

#ifdef __cplusplus
}
#endif
#endif /* OAI_TENSOR_H */
