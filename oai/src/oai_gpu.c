/* oai_gpu.c -- runtime-loaded OpenCL backend with a hard resource budget.
 *
 * See include/oai_gpu.h for the three mechanisms that keep Oai to its share of
 * the device. Nothing here is required for Oai to run: every failure path
 * falls back to the CPU kernels in oai_tensor.c.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#include "oai_gpu.h"
#include "oai_tensor.h"
#include "oai_platform.h"
#include "cl_min.h"

#include <math.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

/* Shapes smaller than this are dominated by transfer latency, so they stay on
 * the CPU even when a device is active. */
#define OAI_GPU_MIN_WORK 200000L
#define OAI_TILE 16

/* ============================================== dynamically bound entry points */

typedef cl_int (*fn_clGetPlatformIDs)(cl_uint, cl_platform_id *, cl_uint *);
typedef cl_int (*fn_clGetPlatformInfo)(cl_platform_id, cl_platform_info,
                                       size_t, void *, size_t *);
typedef cl_int (*fn_clGetDeviceIDs)(cl_platform_id, cl_device_type, cl_uint,
                                    cl_device_id *, cl_uint *);
typedef cl_int (*fn_clGetDeviceInfo)(cl_device_id, cl_device_info, size_t,
                                     void *, size_t *);
typedef cl_int (*fn_clCreateSubDevices)(cl_device_id,
                                        const cl_device_partition_property *,
                                        cl_uint, cl_device_id *, cl_uint *);
typedef cl_context (*fn_clCreateContext)(const cl_context_properties *,
                                         cl_uint, const cl_device_id *,
                                         void *, void *, cl_int *);
typedef cl_command_queue (*fn_clCreateCommandQueue)(cl_context, cl_device_id,
                                                    cl_command_queue_properties,
                                                    cl_int *);
typedef cl_mem (*fn_clCreateBuffer)(cl_context, cl_mem_flags, size_t, void *,
                                    cl_int *);
typedef cl_program (*fn_clCreateProgramWithSource)(cl_context, cl_uint,
                                                   const char **,
                                                   const size_t *, cl_int *);
typedef cl_int (*fn_clBuildProgram)(cl_program, cl_uint, const cl_device_id *,
                                    const char *, void *, void *);
typedef cl_int (*fn_clGetProgramBuildInfo)(cl_program, cl_device_id,
                                           cl_program_build_info, size_t,
                                           void *, size_t *);
typedef cl_kernel (*fn_clCreateKernel)(cl_program, const char *, cl_int *);
typedef cl_int (*fn_clSetKernelArg)(cl_kernel, cl_uint, size_t, const void *);
typedef cl_int (*fn_clEnqueueNDRangeKernel)(cl_command_queue, cl_kernel,
                                            cl_uint, const size_t *,
                                            const size_t *, const size_t *,
                                            cl_uint, const cl_event *,
                                            cl_event *);
typedef cl_int (*fn_clEnqueueWriteBuffer)(cl_command_queue, cl_mem, cl_bool,
                                          size_t, size_t, const void *,
                                          cl_uint, const cl_event *,
                                          cl_event *);
typedef cl_int (*fn_clEnqueueReadBuffer)(cl_command_queue, cl_mem, cl_bool,
                                         size_t, size_t, void *, cl_uint,
                                         const cl_event *, cl_event *);
typedef cl_int (*fn_clFinish)(cl_command_queue);
typedef cl_int (*fn_clReleaseMemObject)(cl_mem);
typedef cl_int (*fn_clReleaseKernel)(cl_kernel);
typedef cl_int (*fn_clReleaseProgram)(cl_program);
typedef cl_int (*fn_clReleaseCommandQueue)(cl_command_queue);
typedef cl_int (*fn_clReleaseContext)(cl_context);
typedef cl_int (*fn_clReleaseDevice)(cl_device_id);
typedef cl_int (*fn_clGetKernelWorkGroupInfo)(cl_kernel, cl_device_id,
                                              cl_kernel_work_group_info,
                                              size_t, void *, size_t *);

static struct {
    oai_dl lib;
    int    loaded;
    fn_clGetPlatformIDs          GetPlatformIDs;
    fn_clGetPlatformInfo         GetPlatformInfo;
    fn_clGetDeviceIDs            GetDeviceIDs;
    fn_clGetDeviceInfo           GetDeviceInfo;
    fn_clCreateSubDevices        CreateSubDevices;
    fn_clCreateContext           CreateContext;
    fn_clCreateCommandQueue      CreateCommandQueue;
    fn_clCreateBuffer            CreateBuffer;
    fn_clCreateProgramWithSource CreateProgramWithSource;
    fn_clBuildProgram            BuildProgram;
    fn_clGetProgramBuildInfo     GetProgramBuildInfo;
    fn_clCreateKernel            CreateKernel;
    fn_clSetKernelArg            SetKernelArg;
    fn_clEnqueueNDRangeKernel    EnqueueNDRangeKernel;
    fn_clEnqueueWriteBuffer      EnqueueWriteBuffer;
    fn_clEnqueueReadBuffer       EnqueueReadBuffer;
    fn_clFinish                  Finish;
    fn_clReleaseMemObject        ReleaseMemObject;
    fn_clReleaseKernel           ReleaseKernel;
    fn_clReleaseProgram          ReleaseProgram;
    fn_clReleaseCommandQueue     ReleaseCommandQueue;
    fn_clReleaseContext          ReleaseContext;
    fn_clReleaseDevice           ReleaseDevice;
    fn_clGetKernelWorkGroupInfo  GetKernelWorkGroupInfo;
} cl;

/* ================================================================== state */

static struct {
    int              active;
    int              partitioned;
    cl_device_id     root_device;
    cl_device_id     device;       /* == root_device unless fission worked */
    cl_context       ctx;
    cl_command_queue queue;
    cl_program       program;
    cl_kernel        kernel;
    int              tiled;

    cl_mem  buf_a, buf_b, buf_c;
    size_t  cap_a, cap_b, cap_c;   /* bytes */
    size_t  mem_budget;            /* bytes Oai may allocate on the device */

    float   budget;
    double  busy, idle;
    double  debt;          /* yield owed but not yet taken, in seconds */
    double  owed_last;     /* what the most recent dispatch alone owed */
    long    calls;
    int     calibrating;   /* suppress yielding and stats while measuring */
    double  cal_gpu, cal_cpu;   /* calibration totals, in seconds */
    oai_mutex *lock;

    oai_gpu_info info;
} G;

/* The kernel text is compiled in so a single binary needs no data files.
 * Keep in sync with kernels/matmul.cl (tools/embed_kernels.py does that). */
static const char *k_kernel_source =
"#define TS 16\n"
"__kernel void sgemm_tiled(__global const float *A,\n"
"                          __global const float *B,\n"
"                          __global float *C,\n"
"                          const int M, const int K, const int N)\n"
"{\n"
"    const int lrow = get_local_id(0);\n"
"    const int lcol = get_local_id(1);\n"
"    const int row  = get_global_id(0);\n"
"    const int col  = get_global_id(1);\n"
"    __local float Asub[TS][TS];\n"
"    __local float Bsub[TS][TS];\n"
"    float acc = 0.0f;\n"
"    const int tiles = (K + TS - 1) / TS;\n"
"    for (int t = 0; t < tiles; ++t) {\n"
"        const int tiledCol = t * TS + lcol;\n"
"        const int tiledRow = t * TS + lrow;\n"
"        Asub[lrow][lcol] = (row < M && tiledCol < K)\n"
"                         ? A[(size_t)row * K + tiledCol] : 0.0f;\n"
"        Bsub[lrow][lcol] = (tiledRow < K && col < N)\n"
"                         ? B[(size_t)tiledRow * N + col] : 0.0f;\n"
"        barrier(CLK_LOCAL_MEM_FENCE);\n"
"        for (int k = 0; k < TS; ++k)\n"
"            acc += Asub[lrow][k] * Bsub[k][lcol];\n"
"        barrier(CLK_LOCAL_MEM_FENCE);\n"
"    }\n"
"    if (row < M && col < N)\n"
"        C[(size_t)row * N + col] = acc;\n"
"}\n"
"__kernel void sgemm_naive(__global const float *A,\n"
"                          __global const float *B,\n"
"                          __global float *C,\n"
"                          const int M, const int K, const int N)\n"
"{\n"
"    const int row = get_global_id(0);\n"
"    const int col = get_global_id(1);\n"
"    if (row >= M || col >= N) return;\n"
"    float acc = 0.0f;\n"
"    for (int k = 0; k < K; ++k)\n"
"        acc += A[(size_t)row * K + k] * B[(size_t)k * N + col];\n"
"    C[(size_t)row * N + col] = acc;\n"
"}\n";

/* ================================================================ loading */

static int cl_load(void)
{
    static const char *const names[] = {
        "libOpenCL.so.1", "libOpenCL.so", "OpenCL.dll",
        "/System/Library/Frameworks/OpenCL.framework/OpenCL",
        "libOpenCL.dylib"
    };
    if (cl.loaded) return cl.lib != NULL;
    cl.loaded = 1;
    cl.lib = oai_dl_open(names, (int)(sizeof names / sizeof names[0]));
    if (!cl.lib) return 0;

/* Function-pointer to function-pointer casts are well defined, which is why
 * oai_dl_sym hands back an oai_dl_func rather than a void*. */
#define BIND(field, symbol)                                                   \
    do {                                                                      \
        cl.field = (fn_cl##symbol)oai_dl_sym(cl.lib, "cl" #symbol);           \
    } while (0)
#define BIND_REQUIRED(field, symbol)                                          \
    do {                                                                      \
        BIND(field, symbol);                                                  \
        if (!cl.field) { oai_dl_close(cl.lib); cl.lib = NULL; return 0; }     \
    } while (0)

    BIND_REQUIRED(GetPlatformIDs,          GetPlatformIDs);
    BIND_REQUIRED(GetPlatformInfo,         GetPlatformInfo);
    BIND_REQUIRED(GetDeviceIDs,            GetDeviceIDs);
    BIND_REQUIRED(GetDeviceInfo,           GetDeviceInfo);
    BIND_REQUIRED(CreateContext,           CreateContext);
    BIND_REQUIRED(CreateCommandQueue,      CreateCommandQueue);
    BIND_REQUIRED(CreateBuffer,            CreateBuffer);
    BIND_REQUIRED(CreateProgramWithSource, CreateProgramWithSource);
    BIND_REQUIRED(BuildProgram,            BuildProgram);
    BIND_REQUIRED(CreateKernel,            CreateKernel);
    BIND_REQUIRED(SetKernelArg,            SetKernelArg);
    BIND_REQUIRED(EnqueueNDRangeKernel,    EnqueueNDRangeKernel);
    BIND_REQUIRED(EnqueueWriteBuffer,      EnqueueWriteBuffer);
    BIND_REQUIRED(EnqueueReadBuffer,       EnqueueReadBuffer);
    BIND_REQUIRED(Finish,                  Finish);
    BIND_REQUIRED(ReleaseMemObject,        ReleaseMemObject);
    BIND_REQUIRED(ReleaseKernel,           ReleaseKernel);
    BIND_REQUIRED(ReleaseProgram,          ReleaseProgram);
    BIND_REQUIRED(ReleaseCommandQueue,     ReleaseCommandQueue);
    BIND_REQUIRED(ReleaseContext,          ReleaseContext);
    /* Optional: only present on OpenCL 1.2+ ICDs. */
    BIND(CreateSubDevices,       CreateSubDevices);
    BIND(ReleaseDevice,          ReleaseDevice);
    BIND(GetProgramBuildInfo,    GetProgramBuildInfo);
    BIND(GetKernelWorkGroupInfo, GetKernelWorkGroupInfo);
#undef BIND
#undef BIND_REQUIRED
    return 1;
}

/* Appends to a fixed buffer, returning the new length.
 *
 * `used += snprintf(buf + used, cap - used, ...)` looks right and is not:
 * snprintf returns what it *would* have written, so on truncation `used` runs
 * past `cap`, the next `buf + used` is out of bounds and `cap - used`
 * underflows to a huge size_t. This clamps instead. */
static size_t str_append(char *buf, size_t cap, size_t used, const char *fmt, ...)
{
    va_list ap;
    int n;

    if (cap == 0 || used >= cap - 1) return used;
    va_start(ap, fmt);
    n = vsnprintf(buf + used, cap - used, fmt, ap);
    va_end(ap);
    if (n < 0) return used;
    used += (size_t)n;
    if (used >= cap) used = cap - 1;   /* it truncated; stay inside the buffer */
    return used;
}

/* ========================================================= device listing */

typedef struct {
    cl_device_id id;
    char         name[128];
    char         vendor[96];
    char         driver[64];
    cl_uint      compute_units;
    cl_ulong     global_mem;
    cl_uint      clock_mhz;
} oai_dev;

/* Which device types Oai will consider.
 *
 * CPU OpenCL devices are excluded: routing work through an OpenCL runtime to
 * reach the same processor Oai already uses directly is slower, never faster.
 * Setting OAI_OPENCL_ALLOW_CPU=1 includes them anyway, which is how the
 * OpenCL path gets exercised on a machine with no GPU -- install pocl and the
 * whole backend, kernel build and device fission included, runs on the CPU. */
static cl_device_type wanted_device_types(void)
{
    const char *allow = getenv("OAI_OPENCL_ALLOW_CPU");
    cl_device_type types = CL_DEVICE_TYPE_GPU | CL_DEVICE_TYPE_ACCELERATOR;
    if (allow && allow[0] && allow[0] != '0')
        types |= CL_DEVICE_TYPE_CPU;
    return types;
}

/* Collects up to `max` usable devices across every platform. */
static int enumerate_devices(oai_dev *out, int max)
{
    cl_platform_id platforms[8];
    cl_uint nplat = 0, p;
    int count = 0;

    if (!cl_load()) return 0;
    if (cl.GetPlatformIDs(8, platforms, &nplat) != CL_SUCCESS) return 0;

    for (p = 0; p < nplat && count < max; ++p) {
        cl_device_id devs[16];
        cl_uint ndev = 0, d;
        cl_int rc = cl.GetDeviceIDs(platforms[p], wanted_device_types(),
                                    16, devs, &ndev);
        if (rc != CL_SUCCESS) continue;
        for (d = 0; d < ndev && count < max; ++d) {
            oai_dev *e = &out[count];
            memset(e, 0, sizeof *e);
            e->id = devs[d];
            cl.GetDeviceInfo(devs[d], CL_DEVICE_NAME, sizeof e->name,
                             e->name, NULL);
            cl.GetDeviceInfo(devs[d], CL_DEVICE_VENDOR, sizeof e->vendor,
                             e->vendor, NULL);
            cl.GetDeviceInfo(devs[d], CL_DRIVER_VERSION, sizeof e->driver,
                             e->driver, NULL);
            cl.GetDeviceInfo(devs[d], CL_DEVICE_MAX_COMPUTE_UNITS,
                             sizeof e->compute_units, &e->compute_units, NULL);
            cl.GetDeviceInfo(devs[d], CL_DEVICE_GLOBAL_MEM_SIZE,
                             sizeof e->global_mem, &e->global_mem, NULL);
            cl.GetDeviceInfo(devs[d], CL_DEVICE_MAX_CLOCK_FREQUENCY,
                             sizeof e->clock_mhz, &e->clock_mhz, NULL);
            count++;
        }
    }
    return count;
}

void oai_gpu_probe(oai_gpu_info *out)
{
    oai_dev devs[8];
    int n;

    memset(out, 0, sizeof *out);
    if (!cl_load()) {
        snprintf(out->status, sizeof out->status,
                 "no OpenCL runtime found - CPU only");
        return;
    }
    out->available = 1;

    n = enumerate_devices(devs, 8);
    if (n == 0) {
        snprintf(out->status, sizeof out->status,
                 "OpenCL present but no GPU device - CPU only");
        return;
    }
    snprintf(out->device_name, sizeof out->device_name, "%s", devs[0].name);
    snprintf(out->vendor, sizeof out->vendor, "%s", devs[0].vendor);
    snprintf(out->driver, sizeof out->driver, "%s", devs[0].driver);
    out->compute_units_total = (int)devs[0].compute_units;
    out->global_mem_mb = (unsigned long)(devs[0].global_mem / (1024 * 1024));
    snprintf(out->status, sizeof out->status, "%d GPU device%s available",
             n, n == 1 ? "" : "s");
}

void oai_gpu_list_devices(char *buf, size_t buflen)
{
    oai_dev devs[8];
    int n, i;
    size_t used = 0;

    if (!buflen) return;
    buf[0] = '\0';

    if (!cl_load()) {
        snprintf(buf, buflen,
                 "No OpenCL runtime could be loaded on this machine.\n"
                 "Oai will train on the CPU. Install your vendor's OpenCL\n"
                 "driver (or an ICD such as pocl) to enable the GPU path.\n");
        return;
    }
    n = enumerate_devices(devs, 8);
    if (n == 0) {
        snprintf(buf, buflen,
                 "OpenCL is installed but exposes no GPU or accelerator.\n"
                 "Oai will train on the CPU.\n");
        return;
    }
    for (i = 0; i < n && used + 1 < buflen; ++i) {
        used = str_append(buf, buflen, used,
            "  [%d] %s\n"
            "      vendor %s | driver %s\n"
            "      %u compute units | %lu MB global memory | %u MHz\n",
            i, devs[i].name, devs[i].vendor, devs[i].driver,
            (unsigned)devs[i].compute_units,
            (unsigned long)(devs[i].global_mem / (1024 * 1024)),
            (unsigned)devs[i].clock_mhz);
    }
}

/* ============================================================ initialisation */

/* Asks the driver for a sub-device with `units` compute units. Returns the
 * sub-device on success, or NULL when the driver cannot partition. */
static cl_device_id try_partition(cl_device_id root, int units, int total)
{
    cl_device_partition_property props[4];
    cl_device_id sub = NULL;
    cl_uint got = 0;

    if (!cl.CreateSubDevices) return NULL;
    if (units >= total || units <= 0) return NULL;

    props[0] = CL_DEVICE_PARTITION_BY_COUNTS;
    props[1] = (cl_device_partition_property)units;
    props[2] = CL_DEVICE_PARTITION_BY_COUNTS_LIST_END;
    props[3] = 0;

    if (cl.CreateSubDevices(root, props, 1, &sub, &got) == CL_SUCCESS
        && got == 1 && sub) {
        return sub;
    }
    return NULL;
}

static int build_program(void)
{
    cl_int rc = 0;
    size_t len = strlen(k_kernel_source);

    G.program = cl.CreateProgramWithSource(G.ctx, 1, &k_kernel_source, &len, &rc);
    if (rc != CL_SUCCESS || !G.program) return -1;

    rc = cl.BuildProgram(G.program, 1, &G.device, "-cl-fast-relaxed-math",
                         NULL, NULL);
    if (rc != CL_SUCCESS) {
        /* Try again without the fast-math flag before giving up. */
        rc = cl.BuildProgram(G.program, 1, &G.device, "", NULL, NULL);
        if (rc != CL_SUCCESS) return -1;
    }

    G.kernel = cl.CreateKernel(G.program, "sgemm_tiled", &rc);
    G.tiled = 1;
    if (rc != CL_SUCCESS || !G.kernel) {
        G.kernel = cl.CreateKernel(G.program, "sgemm_naive", &rc);
        G.tiled = 0;
        if (rc != CL_SUCCESS || !G.kernel) return -1;
    }
    /* A device whose max work-group size cannot hold a 16x16 tile has to use
     * the naive kernel instead. */
    if (G.tiled && cl.GetKernelWorkGroupInfo) {
        size_t wg = 0;
        if (cl.GetKernelWorkGroupInfo(G.kernel, G.device,
                                      CL_KERNEL_WORK_GROUP_SIZE,
                                      sizeof wg, &wg, NULL) == CL_SUCCESS
            && wg < (size_t)(OAI_TILE * OAI_TILE)) {
            cl.ReleaseKernel(G.kernel);
            G.kernel = cl.CreateKernel(G.program, "sgemm_naive", &rc);
            G.tiled = 0;
            if (rc != CL_SUCCESS || !G.kernel) return -1;
        }
    }
    return 0;
}

int oai_gpu_init(int index, float budget)
{
    oai_dev devs[8];
    int n, units;
    cl_int rc = 0;
    cl_device_id sub;

    oai_gpu_shutdown();
    memset(&G, 0, sizeof G);

    if (budget < 0.05f) budget = 0.05f;
    if (budget > 1.0f)  budget = 1.0f;
    G.budget = budget;

    if (!cl_load()) {
        snprintf(G.info.status, sizeof G.info.status,
                 "no OpenCL runtime - training on CPU");
        return -1;
    }
    G.info.available = 1;

    n = enumerate_devices(devs, 8);
    if (n == 0) {
        snprintf(G.info.status, sizeof G.info.status,
                 "no GPU device - training on CPU");
        return -1;
    }
    if (index < 0 || index >= n) index = 0;

    G.root_device = devs[index].id;
    G.device      = devs[index].id;

    units = (int)ceilf((float)devs[index].compute_units * budget);
    if (units < 1) units = 1;
    if (units > (int)devs[index].compute_units)
        units = (int)devs[index].compute_units;

    sub = try_partition(G.root_device, units, (int)devs[index].compute_units);
    if (sub) {
        G.device = sub;
        G.partitioned = 1;
    }

    G.ctx = cl.CreateContext(NULL, 1, &G.device, NULL, NULL, &rc);
    if (rc != CL_SUCCESS || !G.ctx) {
        snprintf(G.info.status, sizeof G.info.status,
                 "could not create an OpenCL context - training on CPU");
        oai_gpu_shutdown();
        return -1;
    }
    G.queue = cl.CreateCommandQueue(G.ctx, G.device, 0, &rc);
    if (rc != CL_SUCCESS || !G.queue) {
        snprintf(G.info.status, sizeof G.info.status,
                 "could not create a command queue - training on CPU");
        oai_gpu_shutdown();
        return -1;
    }
    if (build_program() != 0) {
        snprintf(G.info.status, sizeof G.info.status,
                 "kernel build failed - training on CPU");
        oai_gpu_shutdown();
        return -1;
    }

    G.mem_budget = (size_t)((double)devs[index].global_mem * (double)budget);
    G.lock = oai_mutex_new();
    G.active = 1;

    G.info.active              = 1;
    G.info.partitioned         = G.partitioned;
    G.info.compute_units_total = (int)devs[index].compute_units;
    G.info.compute_units_used  = G.partitioned ? units
                                               : (int)devs[index].compute_units;
    G.info.global_mem_mb = (unsigned long)(devs[index].global_mem / (1024*1024));
    G.info.budget_mem_mb = (unsigned long)(G.mem_budget / (1024 * 1024));
    G.info.budget        = budget;
    snprintf(G.info.device_name, sizeof G.info.device_name, "%s",
             devs[index].name);
    snprintf(G.info.vendor, sizeof G.info.vendor, "%s", devs[index].vendor);
    snprintf(G.info.driver, sizeof G.info.driver, "%s", devs[index].driver);
    snprintf(G.info.status, sizeof G.info.status,
             G.partitioned
               ? "GPU active, %d/%d compute units reserved by device fission"
               : "GPU active, %d%% duty cycle across %d compute units",
             G.partitioned ? units : (int)(budget * 100.0f + 0.5f),
             G.partitioned ? (int)devs[index].compute_units
                           : (int)devs[index].compute_units);
    return 0;
}

void oai_gpu_shutdown(void)
{
    if (cl.lib) {
        if (G.buf_a) cl.ReleaseMemObject(G.buf_a);
        if (G.buf_b) cl.ReleaseMemObject(G.buf_b);
        if (G.buf_c) cl.ReleaseMemObject(G.buf_c);
        if (G.kernel) cl.ReleaseKernel(G.kernel);
        if (G.program) cl.ReleaseProgram(G.program);
        if (G.queue) cl.ReleaseCommandQueue(G.queue);
        if (G.ctx) cl.ReleaseContext(G.ctx);
        if (G.partitioned && G.device && cl.ReleaseDevice)
            cl.ReleaseDevice(G.device);
    }
    if (G.lock) oai_mutex_free(G.lock);
    G.buf_a = G.buf_b = G.buf_c = NULL;
    G.cap_a = G.cap_b = G.cap_c = 0;
    G.kernel = NULL; G.program = NULL; G.queue = NULL; G.ctx = NULL;
    G.device = NULL; G.root_device = NULL;
    G.lock = NULL;
    G.active = 0;
    G.partitioned = 0;
    G.info.active = 0;
}

int oai_gpu_is_active(void)
{
    return G.active;
}

void oai_gpu_set_budget(float budget)
{
    if (budget < 0.05f) budget = 0.05f;
    if (budget > 1.0f)  budget = 1.0f;
    G.budget = budget;
    G.info.budget = budget;
}

void oai_gpu_get_info(oai_gpu_info *out)
{
    *out = G.info;
    out->busy_seconds = G.busy;
    out->idle_seconds = G.idle;
    out->kernel_calls = G.calls;
    out->budget       = G.budget;
}

/* ================================================================ matmul */

/* Grows a device buffer to at least `bytes`, honouring the memory budget. */
static int ensure_buffer(cl_mem *buf, size_t *cap, size_t bytes,
                         cl_mem_flags flags)
{
    cl_int rc = 0;
    cl_mem nb;
    if (*cap >= bytes && *buf) return 0;
    if (bytes > G.mem_budget) return -1;   /* would exceed our share */
    nb = cl.CreateBuffer(G.ctx, flags, bytes, NULL, &rc);
    if (rc != CL_SUCCESS || !nb) return -1;
    if (*buf) cl.ReleaseMemObject(*buf);
    *buf = nb;
    *cap = bytes;
    return 0;
}

static size_t round_up(size_t v, size_t mult)
{
    return ((v + mult - 1) / mult) * mult;
}

static int gpu_matmul(const float *A, const float *B, float *C,
                      int m, int k, int n)
{
    size_t bytes_a = (size_t)m * k * sizeof(float);
    size_t bytes_b = (size_t)k * n * sizeof(float);
    size_t bytes_c = (size_t)m * n * sizeof(float);
    size_t global[2], local[2];
    cl_int rc;
    double t0, elapsed;

    if (bytes_a + bytes_b + bytes_c > G.mem_budget) return -1;

    if (ensure_buffer(&G.buf_a, &G.cap_a, bytes_a, CL_MEM_READ_ONLY)  != 0
     || ensure_buffer(&G.buf_b, &G.cap_b, bytes_b, CL_MEM_READ_ONLY)  != 0
     || ensure_buffer(&G.buf_c, &G.cap_c, bytes_c, CL_MEM_WRITE_ONLY) != 0)
        return -1;

    t0 = oai_time_now();

    rc  = cl.EnqueueWriteBuffer(G.queue, G.buf_a, CL_FALSE, 0, bytes_a, A,
                                0, NULL, NULL);
    rc |= cl.EnqueueWriteBuffer(G.queue, G.buf_b, CL_FALSE, 0, bytes_b, B,
                                0, NULL, NULL);
    if (rc != CL_SUCCESS) return -1;

    rc  = cl.SetKernelArg(G.kernel, 0, sizeof(cl_mem), &G.buf_a);
    rc |= cl.SetKernelArg(G.kernel, 1, sizeof(cl_mem), &G.buf_b);
    rc |= cl.SetKernelArg(G.kernel, 2, sizeof(cl_mem), &G.buf_c);
    rc |= cl.SetKernelArg(G.kernel, 3, sizeof(int), &m);
    rc |= cl.SetKernelArg(G.kernel, 4, sizeof(int), &k);
    rc |= cl.SetKernelArg(G.kernel, 5, sizeof(int), &n);
    if (rc != CL_SUCCESS) return -1;

    if (G.tiled) {
        local[0] = local[1] = OAI_TILE;
        global[0] = round_up((size_t)m, OAI_TILE);
        global[1] = round_up((size_t)n, OAI_TILE);
        rc = cl.EnqueueNDRangeKernel(G.queue, G.kernel, 2, NULL, global, local,
                                     0, NULL, NULL);
    } else {
        global[0] = (size_t)m;
        global[1] = (size_t)n;
        rc = cl.EnqueueNDRangeKernel(G.queue, G.kernel, 2, NULL, global, NULL,
                                     0, NULL, NULL);
    }
    if (rc != CL_SUCCESS) return -1;

    rc = cl.EnqueueReadBuffer(G.queue, G.buf_c, CL_TRUE, 0, bytes_c, C,
                              0, NULL, NULL);
    if (rc != CL_SUCCESS) return -1;
    cl.Finish(G.queue);

    elapsed = oai_time_now() - t0;
    if (G.calibrating) return 0;

    G.busy += elapsed;
    G.calls++;

    /* Duty cycle. With a hard partition the driver is already limiting us, so
     * only the software path needs to yield. Yielding elapsed*(1/budget - 1)
     * makes long-run occupancy approach the budget.
     *
     * The yield is *accumulated* rather than taken after every dispatch. A
     * single step's matmuls each owe about a millisecond, and a sleep that
     * short is dominated by the operating system's timer granularity -- on
     * Windows it used to round up to a 15.6 ms tick and cost five times the
     * throughput. Paying the debt in fewer, longer sleeps keeps the same
     * average occupancy and is kinder to the scheduler either way. */
    if (!G.partitioned && G.budget < 0.999f) {
        G.owed_last = elapsed * (1.0 / (double)G.budget - 1.0);
        G.debt += G.owed_last;
        if (G.debt > 2.0) G.debt = 2.0;   /* do not bank a stall */
    }
    return 0;
}

/* How much debt is worth a syscall. Below this the sleep costs more in
 * overhead and rounding than the yield is worth. */
#define OAI_MIN_YIELD_S 0.002
/* Floor on how much may be paid off in one sleep. The real cap is whatever
 * this dispatch alone owed (see yield_cap), because a cap below that can
 * never keep up: at a 10% budget a 20 ms kernel owes 180 ms, and paying a
 * fixed 50 ms would let occupancy settle near 29% instead of 10%. */
#define OAI_MIN_YIELD_CAP_S 0.050
/* Absolute ceiling, so one sleep cannot make a cancel feel unresponsive.
 * A kernel slow enough to owe more than this per dispatch will run above its
 * budget; responsiveness wins that trade. */
#define OAI_MAX_YIELD_S 0.250

/* The most that may be slept in one go. */
static double yield_cap(void)
{
    double cap = G.owed_last > OAI_MIN_YIELD_CAP_S ? G.owed_last
                                                   : OAI_MIN_YIELD_CAP_S;
    return cap > OAI_MAX_YIELD_S ? OAI_MAX_YIELD_S : cap;
}

void oai_gpu_or_cpu_matmul(const float *A, const float *B, float *C,
                           int m, int k, int n)
{
    long   work = (long)m * (long)k * (long)n;
    int    ok = 0;
    double yield = 0.0;

    if (G.active && work >= OAI_GPU_MIN_WORK) {
        oai_mutex_lock(G.lock);
        ok = (gpu_matmul(A, B, C, m, k, n) == 0);
        if (ok && G.debt >= OAI_MIN_YIELD_S) {
            double cap = yield_cap();
            yield = G.debt > cap ? cap : G.debt;
            G.debt -= yield;
            G.idle += yield;
        }
        oai_mutex_unlock(G.lock);

        /* Sleep with the lock released -- holding it would block anything
         * else that wants the device for no reason. */
        if (yield > 0.0) oai_sleep_ms(yield * 1000.0);

        if (ok) return;
        /* A failed dispatch is not fatal: fall through to the CPU. */
    }
    oai_matmul(A, B, C, m, k, n);
}

/* ============================================================ calibration */

/* Times one shape on both paths.
 *
 * When the budget is enforced by duty cycling, the GPU figure is scaled by
 * 1/budget: that is what a step really costs once the yield is paid, and
 * comparing raw kernel time would recommend the GPU in cases where it
 * finishes first but still delivers fewer steps per second. A partitioned
 * device pays no yield -- its share is already reflected in how long the
 * kernel takes on fewer compute units -- so its time is used as measured. */
void oai_gpu_calibrate_shape(int m, int k, int n)
{
    float *A, *B, *C;
    double t0, gpu_t, cpu_t;
    int    i, reps = 3;
    size_t na = (size_t)m * k, nb = (size_t)k * n, nc = (size_t)m * n;

    if (!G.active) return;

    A = (float *)malloc(na * sizeof(float));
    B = (float *)malloc(nb * sizeof(float));
    C = (float *)malloc(nc * sizeof(float));
    if (!A || !B || !C) { free(A); free(B); free(C); return; }

    /* Real values, not zeros: the CPU matmul skips zero multipliers, so a
     * zeroed buffer would time the CPU as far faster than it is. */
    for (i = 0; i < (int)na; ++i) A[i] = 0.5f + (float)(i % 17) * 0.01f;
    for (i = 0; i < (int)nb; ++i) B[i] = 0.5f - (float)(i % 13) * 0.01f;

    G.calibrating = 1;

    oai_mutex_lock(G.lock);
    if (gpu_matmul(A, B, C, m, k, n) != 0) {   /* warm up and check it works */
        oai_mutex_unlock(G.lock);
        G.calibrating = 0;
        free(A); free(B); free(C);
        return;
    }
    t0 = oai_time_now();
    for (i = 0; i < reps; ++i) gpu_matmul(A, B, C, m, k, n);
    gpu_t = (oai_time_now() - t0) / reps;
    oai_mutex_unlock(G.lock);

    G.calibrating = 0;

    oai_matmul(A, B, C, m, k, n);              /* warm up the CPU path too */
    t0 = oai_time_now();
    for (i = 0; i < reps; ++i) oai_matmul(A, B, C, m, k, n);
    cpu_t = (oai_time_now() - t0) / reps;

    if (G.partitioned || G.budget >= 0.999f)
        G.cal_gpu += gpu_t;
    else
        G.cal_gpu += gpu_t / (double)G.budget;
    G.cal_cpu += cpu_t;

    free(A); free(B); free(C);
}

int oai_gpu_calibrate_finish(int force)
{
    if (!G.active) return 0;
    if (G.cal_gpu <= 0.0 || G.cal_cpu <= 0.0) return 1;   /* nothing measured */

    G.info.cal_gpu_ms = (float)(G.cal_gpu * 1000.0);
    G.info.cal_cpu_ms = (float)(G.cal_cpu * 1000.0);
    G.info.calibrated = 1;

    if (G.cal_gpu <= G.cal_cpu) return 1;                 /* the GPU wins */

    if (force) {
        snprintf(G.info.status, sizeof G.info.status,
                 "GPU kept because --backend gpu was given, though it is "
                 "%.1fx slower than the CPU at this model size",
                 G.cal_gpu / G.cal_cpu);
        return 1;
    }

    {
        char device[64];          /* trimmed so the whole line fits status[] */
        float gpu_ms = G.info.cal_gpu_ms, cpu_ms = G.info.cal_cpu_ms;
        float ratio = (float)(G.cal_gpu / G.cal_cpu);
        snprintf(device, sizeof device, "%.60s", G.info.device_name);
        oai_gpu_shutdown();
        snprintf(G.info.status, sizeof G.info.status,
                 "%s is %.1fx slower than the CPU at this size "
                 "(%.2f ms vs %.2f ms per step), so Oai is using the CPU",
                 device, ratio, gpu_ms, cpu_ms);
        G.info.calibrated = 1;
        G.info.cal_gpu_ms = gpu_ms;
        G.info.cal_cpu_ms = cpu_ms;
    }
    return 0;
}
