/* test_tensor.c -- the linear algebra, checked against naive references.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_tensor.h"
#include "oai_pool.h"
#include "test_util.h"

/* Deliberately the dumbest possible implementations, so a bug in the blocked
 * and threaded versions has nowhere to hide. */
static void ref_nn(const float *A, const float *B, float *C, int m, int k, int n)
{
    int i, j, p;
    for (i = 0; i < m; ++i)
        for (j = 0; j < n; ++j) {
            double s = 0.0;
            for (p = 0; p < k; ++p) s += (double)A[i*k+p] * B[p*n+j];
            C[i*n+j] = (float)s;
        }
}

static void ref_nt(const float *A, const float *B, float *C, int m, int k, int n)
{
    int i, j, p;
    for (i = 0; i < m; ++i)
        for (j = 0; j < n; ++j) {
            double s = 0.0;
            for (p = 0; p < k; ++p) s += (double)A[i*k+p] * B[j*k+p];
            C[i*n+j] = (float)s;
        }
}

static void ref_tn(const float *A, const float *B, float *C, int m, int k, int n)
{
    int i, j, p;
    for (i = 0; i < m; ++i)
        for (j = 0; j < n; ++j) {
            double s = 0.0;
            for (p = 0; p < k; ++p) s += (double)A[p*m+i] * B[p*n+j];
            C[i*n+j] += (float)s;
        }
}

static float *rand_buf(oai_rng *r, int n)
{
    float *b = (float *)malloc((size_t)n * sizeof(float));
    int i;
    for (i = 0; i < n; ++i) b[i] = oai_rng_normal(r);
    return b;
}

static void check_shapes(int m, int k, int n)
{
    oai_rng r;
    float *A, *B, *C, *R;
    int i;

    oai_rng_seed(&r, (unsigned)(m * 7919 + k * 104729 + n));
    A = rand_buf(&r, m * k);
    B = rand_buf(&r, k * n);
    C = (float *)calloc((size_t)m * n, sizeof(float));
    R = (float *)calloc((size_t)m * n, sizeof(float));

    oai_matmul(A, B, C, m, k, n);
    ref_nn(A, B, R, m, k, n);
    for (i = 0; i < m * n; ++i)
        CHECK_NEAR(C[i], R[i], 1e-3, "matmul %dx%dx%d at %d", m, k, n, i);

    /* B reinterpreted as (n x k) for the transposed form. */
    free(B);
    B = rand_buf(&r, n * k);
    oai_matmul_nt(A, B, C, m, k, n);
    ref_nt(A, B, R, m, k, n);
    for (i = 0; i < m * n; ++i)
        CHECK_NEAR(C[i], R[i], 1e-3, "matmul_nt %dx%dx%d at %d", m, k, n, i);

    /* A reinterpreted as (k x m) for the accumulate form. */
    free(A);
    free(B);
    A = rand_buf(&r, k * m);
    B = rand_buf(&r, k * n);
    for (i = 0; i < m * n; ++i) { C[i] = 0.5f; R[i] = 0.5f; }
    oai_matmul_tn_acc(A, B, C, m, k, n);
    ref_tn(A, B, R, m, k, n);
    for (i = 0; i < m * n; ++i)
        CHECK_NEAR(C[i], R[i], 1e-3, "matmul_tn %dx%dx%d at %d", m, k, n, i);

    free(A); free(B); free(C); free(R);
}

int main(void)
{
    printf("test_tensor\n");

    TEST("matmuls match a naive reference, single threaded");
    oai_pool_init(1);
    check_shapes(1, 1, 1);
    check_shapes(3, 5, 7);
    check_shapes(64, 288, 256);     /* the real forward-pass shape */
    check_shapes(65, 33, 129);      /* odd sizes, past the block boundary */

    TEST("threading does not change the result");
    oai_pool_init(4);
    check_shapes(64, 288, 256);
    check_shapes(65, 33, 129);
    oai_pool_shutdown();

    TEST("softmax rows sum to one");
    {
        float x[12];
        oai_rng r;
        int i, row;
        oai_rng_seed(&r, 99);
        for (i = 0; i < 12; ++i) x[i] = oai_rng_normal(&r) * 10.0f;
        oai_softmax_rows(x, 3, 4);
        for (row = 0; row < 3; ++row) {
            double s = 0.0;
            for (i = 0; i < 4; ++i) {
                s += x[row * 4 + i];
                CHECK(x[row * 4 + i] >= 0.0f, "softmax produced a negative");
            }
            CHECK_NEAR(s, 1.0, 1e-5, "softmax row %d", row);
        }
    }

    TEST("softmax survives large inputs without overflowing");
    {
        float x[3] = { 1000.0f, 1001.0f, 999.0f };
        double s;
        oai_softmax_rows(x, 1, 3);
        s = (double)x[0] + x[1] + x[2];
        CHECK_NEAR(s, 1.0, 1e-5, "large-input softmax");
        CHECK(x[1] > x[0] && x[0] > x[2], "softmax order preserved");
    }

    TEST("tanh backward matches the analytic derivative");
    {
        float y[4] = { 0.0f, 0.5f, -0.5f, 0.9f };
        float dy[4] = { 1.0f, 1.0f, 1.0f, 1.0f };
        float dx[4];
        int i;
        oai_tanh_backward(y, dy, dx, 4);
        for (i = 0; i < 4; ++i)
            CHECK_NEAR(dx[i], 1.0f - y[i] * y[i], 1e-6, "tanh grad %d", i);
    }

    TEST("the generator is deterministic and in range");
    {
        oai_rng a, b;
        int i;
        double sum = 0.0;
        oai_rng_seed(&a, 42);
        oai_rng_seed(&b, 42);
        for (i = 0; i < 1000; ++i) {
            float ua = oai_rng_uniform(&a);
            float ub = oai_rng_uniform(&b);
            CHECK(ua == ub, "same seed diverged at %d", i);
            CHECK(ua >= 0.0f && ua < 1.0f, "uniform out of range: %f", ua);
            sum += oai_rng_normal(&a);
            oai_rng_normal(&b);
        }
        CHECK(fabs(sum / 1000.0) < 0.15, "normal mean drifted: %f", sum / 1000.0);
        for (i = 0; i < 200; ++i) {
            int v = oai_rng_below(&a, 7);
            CHECK(v >= 0 && v < 7, "below(7) returned %d", v);
        }
    }

    TEST("l2 norm and axpy");
    {
        float v[3] = { 3.0f, 4.0f, 0.0f };
        float y[3] = { 1.0f, 1.0f, 1.0f };
        CHECK_NEAR(oai_l2_norm(v, 3), 5.0, 1e-6, "l2");
        oai_axpy(y, v, 3, 2.0f);
        CHECK_NEAR(y[0], 7.0, 1e-6, "axpy 0");
        CHECK_NEAR(y[1], 9.0, 1e-6, "axpy 1");
        oai_scale(y, 3, 0.5f);
        CHECK_NEAR(y[0], 3.5, 1e-6, "scale");
    }

    TEST_MAIN_END();
}
