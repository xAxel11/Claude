/* matmul.cl -- Oai's GPU kernels.
 *
 * Embedded into the binary by src/oai_gpu.c (kept here as a readable copy;
 * scripts/embed_kernels.py regenerates the embedded string from this file).
 *
 * Row-major throughout: A is (M x K), B is (K x N), C is (M x N).
 */

#define TS 16

__kernel void sgemm_tiled(__global const float *A,
                          __global const float *B,
                          __global float *C,
                          const int M, const int K, const int N)
{
    const int lrow = get_local_id(0);
    const int lcol = get_local_id(1);
    const int row  = get_global_id(0);
    const int col  = get_global_id(1);

    __local float Asub[TS][TS];
    __local float Bsub[TS][TS];

    float acc = 0.0f;
    const int tiles = (K + TS - 1) / TS;

    for (int t = 0; t < tiles; ++t) {
        const int tiledCol = t * TS + lcol;
        const int tiledRow = t * TS + lrow;

        Asub[lrow][lcol] = (row < M && tiledCol < K)
                         ? A[(size_t)row * K + tiledCol] : 0.0f;
        Bsub[lrow][lcol] = (tiledRow < K && col < N)
                         ? B[(size_t)tiledRow * N + col] : 0.0f;

        barrier(CLK_LOCAL_MEM_FENCE);

        for (int k = 0; k < TS; ++k)
            acc += Asub[lrow][k] * Bsub[k][lcol];

        barrier(CLK_LOCAL_MEM_FENCE);
    }

    if (row < M && col < N)
        C[(size_t)row * N + col] = acc;
}

/* Fallback for drivers that reject the local-memory version. */
__kernel void sgemm_naive(__global const float *A,
                          __global const float *B,
                          __global float *C,
                          const int M, const int K, const int N)
{
    const int row = get_global_id(0);
    const int col = get_global_id(1);
    if (row >= M || col >= N) return;

    float acc = 0.0f;
    for (int k = 0; k < K; ++k)
        acc += A[(size_t)row * K + k] * B[(size_t)k * N + col];
    C[(size_t)row * N + col] = acc;
}
