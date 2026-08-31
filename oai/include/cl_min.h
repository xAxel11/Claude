/* cl_min.h -- the slice of the OpenCL 1.2 ABI that Oai actually calls.
 *
 * Oai does not link against libOpenCL. It loads the ICD loader at runtime and
 * resolves symbols by name, so a build works on machines with no OpenCL SDK
 * and runs on machines with no GPU at all. Vendoring these declarations keeps
 * that promise without dragging in a vendor SDK.
 *
 * Types and values below match the Khronos OpenCL 1.2 headers.
 *
 * Part of Oai. SPDX-License-Identifier: MIT
 */
#ifndef OAI_CL_MIN_H
#define OAI_CL_MIN_H

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

typedef int32_t  cl_int;
typedef uint32_t cl_uint;
typedef int64_t  cl_long;
typedef uint64_t cl_ulong;
typedef uint32_t cl_bool;
typedef cl_ulong cl_bitfield;
typedef cl_bitfield cl_mem_flags;
typedef cl_bitfield cl_device_type;
typedef cl_bitfield cl_command_queue_properties;
typedef intptr_t cl_context_properties;
typedef intptr_t cl_device_partition_property;
typedef cl_uint  cl_program_build_info;
typedef cl_uint  cl_device_info;
typedef cl_uint  cl_platform_info;
typedef cl_uint  cl_kernel_work_group_info;

typedef struct _cl_platform_id     *cl_platform_id;
typedef struct _cl_device_id       *cl_device_id;
typedef struct _cl_context         *cl_context;
typedef struct _cl_command_queue   *cl_command_queue;
typedef struct _cl_mem             *cl_mem;
typedef struct _cl_program         *cl_program;
typedef struct _cl_kernel          *cl_kernel;
typedef struct _cl_event           *cl_event;

#define CL_SUCCESS                          0
#define CL_DEVICE_NOT_FOUND                 (-1)
#define CL_FALSE                            0
#define CL_TRUE                             1

#define CL_DEVICE_TYPE_CPU                  (1 << 1)
#define CL_DEVICE_TYPE_GPU                  (1 << 2)
#define CL_DEVICE_TYPE_ACCELERATOR          (1 << 3)
#define CL_DEVICE_TYPE_ALL                  0xFFFFFFFF

#define CL_PLATFORM_NAME                    0x0902
#define CL_PLATFORM_VENDOR                  0x0903

#define CL_DEVICE_TYPE                      0x1000
#define CL_DEVICE_MAX_COMPUTE_UNITS         0x1002
#define CL_DEVICE_MAX_WORK_GROUP_SIZE       0x1004
#define CL_DEVICE_MAX_CLOCK_FREQUENCY       0x100C
#define CL_DEVICE_GLOBAL_MEM_SIZE           0x101F
#define CL_DEVICE_MAX_MEM_ALLOC_SIZE        0x1010
#define CL_DEVICE_LOCAL_MEM_SIZE            0x1023
#define CL_DEVICE_NAME                      0x102B
#define CL_DEVICE_VENDOR                    0x102C
#define CL_DRIVER_VERSION                   0x102D
#define CL_DEVICE_VERSION                   0x102F
#define CL_DEVICE_PARTITION_PROPERTIES      0x1044
#define CL_DEVICE_PARTITION_MAX_SUB_DEVICES 0x1043

#define CL_DEVICE_PARTITION_EQUALLY         0x1086
#define CL_DEVICE_PARTITION_BY_COUNTS       0x1087
#define CL_DEVICE_PARTITION_BY_COUNTS_LIST_END 0x0

#define CL_MEM_READ_WRITE                   (1 << 0)
#define CL_MEM_WRITE_ONLY                   (1 << 1)
#define CL_MEM_READ_ONLY                    (1 << 2)
#define CL_MEM_COPY_HOST_PTR                (1 << 5)

#define CL_PROGRAM_BUILD_LOG                0x1183
#define CL_KERNEL_WORK_GROUP_SIZE           0x11B0

#define CL_QUEUE_PROFILING_ENABLE           (1 << 1)

#ifdef __cplusplus
}
#endif
#endif /* OAI_CL_MIN_H */
