# Contributing to Oai

Patches are welcome. The project is small on purpose, and the aim is to keep it
readable by someone who has never seen it before.

## Before you send a change

```sh
make clean && make          # must build with no warnings
make test                   # must pass
./scripts/analyze.sh        # whatever analysers you have installed
```

The build uses `-Wall -Wextra -Wpedantic -Wshadow -Wstrict-prototypes
-Wmissing-prototypes` and is warning-free today. Please keep it that way.

## House style

- **C99, portable.** No compiler extensions outside `src/oai_platform.c`, which
  is the only file allowed to know whether it is on POSIX or Win32.
- **No new dependencies.** The standard library, pthreads on POSIX, and the
  Win32 API. OpenCL is loaded at runtime by name and must stay optional — every
  GPU failure path falls back to the CPU rather than failing the run.
- **Declarations at the top of a block**, four-space indent, 80 columns.
- **Comments explain why.** A comment that restates the code is worse than no
  comment. If a line looks odd and is deliberate, say what would break without
  it.
- **Every allocation has an owner.** If a function returns memory, its header
  comment says who frees it.

## Threading rules

There are three threads: the interface, the trainer, and the worker pool.

- `net` is guarded by `trainer->model_lock`. Anything reading weights — the UI
  sampling for the chat box included — takes it.
- `oai_train_stats` is guarded by `trainer->stats_lock`. Never hold both locks
  in the opposite order to the trainer, which takes `model_lock` first.
- Cancellation is one atomic flag polled at the top of the loop. Do not add a
  second stop path; add a reason to the existing one.
- The streams in `oai_log.c` are already thread-safe. Push from anywhere.

## Testing

New behaviour needs a test in `tests/`. The harness is `tests/test_util.h` —
`CHECK`, `CHECK_NEAR`, `TEST`, and no framework. If you touch the forward or
backward pass, the finite-difference check in `test_net.c` is the one that
matters: a wrong gradient still produces a falling loss.

## Things that would genuinely help

- Kernels for the backward matmuls, so more of a step runs on the GPU.
- A CUDA or Metal backend behind the same budget interface as `oai_gpu.h`.
- Mouse support and selection in the interface.
- A larger public-domain corpus that ships without bloating the repository.
