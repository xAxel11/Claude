# Changelog

All notable changes to Oai are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the version
numbers follow [semantic versioning](https://semver.org/).

## [1.0.2] - 2026-09-01

### Fixed

- **Long lines in the learning feed were cut off at the pane edge instead of
  wrapping.** The panes now fold text at draw time, on word boundaries, and
  break mid-word only when a word is wider than the pane. Folding at draw time
  rather than when the text is written also means the layout stays correct
  after the terminal is resized.
- The device status on the header line is now short enough to fit one; the full
  explanation goes to the feed, where it can wrap.
- A calibration result that picks the CPU is no longer printed in red. It is
  Oai working as intended, and red read as something having gone wrong.
- The start-up line still described a duty-cycled budget as "N of N compute
  units". It now says what is actually happening in each mode.
- Below about 70 columns the two clamps on the pane split contradicted each
  other -- the lower bound pushed the divider right and the upper bound then
  pushed it further left than it began -- collapsing the learning feed to a
  dozen columns. The minimum now scales with the terminal width.
- The footer no longer runs off the edge of an 80- or 100-column terminal.

## [1.0.1] - 2026-09-01

### Fixed

- **GPU training was roughly five times slower than it should have been on
  Windows.** The duty cycle slept after every dispatch, and `Sleep()` cannot
  wait for less than the system timer tick -- 15.6 ms by default -- so each
  ~1 ms yield cost a full tick. Two dispatches per step turned a 2 ms yield
  into ~31 ms. The yield is now accumulated and paid in fewer, longer sleeps,
  and on Windows it uses a high-resolution waitable timer rather than
  `Sleep()`, without changing the machine's global timer resolution.
- The single-sleep cap is now derived from what the dispatch actually owed. A
  fixed 50 ms cap could not keep up with a slow kernel at a low budget, which
  would have let occupancy settle well above the requested share.
- Calibration no longer scales a partitioned device's time by the budget.
  A device limited by fission pays no duty cycle, so scaling double-counted it.
- Cross-compiled binaries were stripped with the host `strip`, which silently
  did nothing. `Oai.exe` drops from 372 KB to 110 KB.

### Added

- **`auto` now measures instead of assuming.** At start-up Oai times the
  model's real matmul shapes on both the GPU and the CPU, including the duty
  cycle, and keeps whichever is faster -- a small model on a modest card is
  usually faster on the CPU. `--backend gpu` overrides it and says plainly
  that it is the slower choice.
- `gpu` in the chat box reports measured occupancy against the budget, so the
  duty cycle can be verified rather than trusted.
- `OAI_OPENCL_ALLOW_CPU=1` includes CPU OpenCL devices, so the whole OpenCL
  backend can be exercised on a machine with no GPU (e.g. with pocl). CI now
  does exactly that.

### Changed

- The device line no longer reads "14/14 compute units, budget 50%", which
  made a duty-cycled budget look like it was being ignored. It now reads
  "14 compute units at a 50% duty cycle", and a partitioned device reports the
  units actually reserved.

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
- **`make_exe.py`** — a standalone, zero-argument build script at the top of the
  tree. `python make_exe.py` finds a compiler, builds `Oai.exe` or `oai` beside
  itself, and names the exact package to install if there is no compiler. It is
  double-clickable on Windows and ships inside every release archive along with
  the C sources, so a downloaded binary can always be rebuilt from source in one
  command.
- **`tools/build_exe.py`** — the release-engineering version: incremental
  rebuilds, cross-compilation to `Oai.exe` with mingw-w64, stripping, and
  packaging a release archive.
- **`tools/make_zip.py`**, **`tools/gen_corpus.py`**, **`tools/embed_kernels.py`**.
- **Scripts** for building, running, testing, benchmarking, static analysis,
  packaging and installing, on POSIX and Windows.
- **Test suite** covering the matmuls against naive references, the backward
  pass against finite differences, the checkpoint round trip, the ring-buffer
  streams, and the trainer's start/cancel/resume lifecycle.
