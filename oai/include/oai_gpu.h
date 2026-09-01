/* oai_gpu.h -- optional OpenCL acceleration with an explicit resource budget.
 *
 * Oai is built to be a good neighbour on a shared machine. It never takes the
 * whole GPU:
 *
 *   1. It asks the driver to carve off a sub-device holding only
 *      ceil(compute_units * budget) units. Where the driver supports device
 *      fission this is a hard partition -- the rest of the GPU is untouched.
 *   2. Where fission is unavailable (most consumer GPUs) it falls back to duty
 *      cycling: it measures how long each batch of kernels occupies the device
 *      and then yields for (1/budget - 1) times as long, so the long-run
 *      occupancy converges on the budget.
 *   3. Device memory it allocates is capped at the same fraction of global
 *      memory, and it refuses to start if the model will not fit inside it.
 *
 * The whole layer is optional. With no OpenCL runtime, no GPU, or --backend
 * cpu, every entry point degrades to the CPU path in oai_tensor.c.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_GPU_H
#define OAI_GPU_H

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int    available;          /* an OpenCL runtime was loaded */
    int    active;             /* a device is initialised and in use */
    int    partitioned;        /* the budget is enforced by device fission */
    char   device_name[128];
    char   vendor[96];
    char   driver[64];
    int    compute_units_total;
    int    compute_units_used;
    unsigned long global_mem_mb;
    unsigned long budget_mem_mb;
    float  budget;             /* the fraction requested, 0.05 .. 1.0 */
    double busy_seconds;       /* device time spent inside kernels */
    double idle_seconds;       /* time deliberately yielded to other users */
    long   kernel_calls;
    int    calibrated;         /* the two figures below are meaningful */
    float  cal_gpu_ms;         /* measured cost of one step's matmuls, GPU */
    float  cal_cpu_ms;         /* the same work on the CPU */
    char   status[192];        /* human-readable state, shown in the UI */
} oai_gpu_info;

/* Probes for an OpenCL runtime and describes what it found without claiming
 * the device. Safe to call when no runtime exists. */
void oai_gpu_probe(oai_gpu_info *out);

/* Brings up a context, queue and the matmul kernel on device `index`, holding
 * Oai to `budget` of the device. Returns 0 on success. */
int  oai_gpu_init(int index, float budget);
void oai_gpu_shutdown(void);
int  oai_gpu_is_active(void);
void oai_gpu_get_info(oai_gpu_info *out);
/* Adjusts the duty-cycle target while training runs. */
void oai_gpu_set_budget(float budget);

/* C = A*B on the GPU when it is active and the shape is worth shipping over
 * the bus, otherwise on the CPU. This is the only call the model makes. */
void oai_gpu_or_cpu_matmul(const float *A, const float *B, float *C,
                           int m, int k, int n);

/* Decides whether the GPU is actually worth using for this model.
 *
 * A small model is dominated by the round trip to the device: on a modest card
 * with the default sizes the CPU is often several times faster, and silently
 * being slower because a GPU exists is not what "auto" should mean. Call
 * oai_gpu_calibrate_shape once per matmul shape the model uses, then
 * oai_gpu_calibrate_finish, which returns 1 if the device was kept and 0 if it
 * was released in favour of the CPU. `force` (i.e. --backend gpu) keeps the
 * device either way, and says so. */
void oai_gpu_calibrate_shape(int m, int k, int n);
int  oai_gpu_calibrate_finish(int force);

/* Lists devices into `buf` for `oai --list-devices`. */
void oai_gpu_list_devices(char *buf, size_t buflen);

#ifdef __cplusplus
}
#endif
#endif /* OAI_GPU_H */
