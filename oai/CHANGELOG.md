# Changelog

All notable changes to Oai are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [semantic versioning](https://semver.org/).

## [1.0.0] - 2026-08-31

The first release.

### Added

- **Character-level language model in C99.** Embedding table, one tanh hidden
  layer over a fixed context window, softmax over the vocabulary. Trained with
  Adam, decoupled weight decay and gradient-norm clipping.
- **Cancellable training.** The loop runs on a worker thread and polls an
  atomic flag between batches, so a stop is honoured within one batch. It
  always writes a checkpoint on the way out, including on `Ctrl+C`.
- **Resumable checkpoints.** Weights, optimiser moments and the vocabulary are
  saved together, so a resumed run continues rather than restarting.
- **Live learning feed.** Loss, moving average, gradient norm, throughput,
  held-out loss, generated samples, and the characters the model currently
  expects after a given prefix.
- **Full-screen terminal interface.** Double-buffered and diff-rendered, so
  only changed cells are written; a loss sparkline, a progress bar, scrollback,
  input history and an ASCII fallback for terminals without Unicode.
- **Chat box.** Commands (`train`, `stop`, `lr`, `gpu`, `learned`, …) plus
  free-form prompts that the model continues.
- **Optional OpenCL backend with a resource budget.** The runtime is loaded by
  name at start-up and never linked, so one binary runs with or without a GPU.
  The budget is enforced by device fission where the driver supports it,
  otherwise by duty cycling, and device memory is capped at the same fraction.
- **CPU worker pool** behind `--threads`, splitting the matmuls across cores.
- **`tools/build_exe.py`** — compiles the whole project into one self-contained
  executable, cross-compiles to `Oai.exe` with mingw-w64, and packages a
  release archive.
- **`tools/make_zip.py`**, **`tools/gen_corpus.py`**, **`tools/embed_kernels.py`**.
- **Scripts** for building, running, testing, benchmarking, static analysis,
  packaging and installing, on POSIX and Windows.
- **Test suite** covering the matmuls against naive references, the backward
  pass against finite differences, the checkpoint round trip, the ring-buffer
  streams, and the trainer's start/cancel/resume lifecycle.
