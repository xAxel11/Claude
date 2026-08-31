# How Oai is put together

A tour of the code in the order data moves through it. Roughly 4,500 lines
across twelve translation units, no dependency that is not in the standard
library.

## The shape of a run

```
main.c
  └── oai_config.c        defaults, oai.conf, then the command line
  └── oai_app.c           brings up the pool, the backend and the trainer
       ├── oai_pool.c     N-1 workers park on a condition variable
       ├── oai_gpu.c      dlopen the OpenCL ICD, claim a share of a device
       └── oai_train.c    load the corpus, build or restore the model
  └── oai_ui.c            the event loop, until you quit
```

Three threads exist while training:

| Thread | Owns | Talks to the others through |
| --- | --- | --- |
| interface | the screen buffers, the input line | atomics, the two stream ring buffers |
| trainer | the training loop | `model_lock`, `stats_lock`, the cancel flag |
| pool workers | a slice of one matmul | a condition variable and a chunk counter |

## Data (`oai_data.c`)

The corpus is read once and converted to one byte per character, where the byte
is an index into a vocabulary built from the characters the file actually
contains. English prose gives about 70 symbols rather than a full 256-entry
byte table, which matters: the output layer and the softmax are both `O(vocab)`.

The last 10% is held out. Training batches are drawn uniformly at random from
the first 90%, which is simpler than shuffling an epoch and, for a model this
size, indistinguishable.

If the corpus file is missing or under 256 bytes, a small corpus compiled into
the binary is used instead, so a fresh clone trains immediately.

## Model (`oai_net.c`)

```
ctx[B][C] ─gather─▶ x[B][C·E] ─W1,b1─▶ tanh ─▶ h[B][H] ─W2,b2─▶ softmax ─▶ p[B][V]
```

The forward pass is two matmuls, two bias adds, a tanh and a softmax. The
backward pass is the same shapes in reverse:

| Forward | Backward |
| --- | --- |
| `h = x·W1` | `dW1 += xᵀ·dhpre`, `dx = dhpre·W1ᵀ` |
| `tanh` | `dhpre = (1 − h²) ⊙ dh` |
| `logits = h·W2` | `dW2 += hᵀ·dlogits`, `dh = dlogits·W2ᵀ` |
| `softmax` + cross-entropy | `dlogits = (p − onehot) / B` |

That last line is why softmax and cross-entropy are always written as one unit:
composed, their derivative collapses to a subtraction.

The embedding gradient is a scatter-add — each row of `dx` is `C` slices, and
each slice is added back to the row of the embedding table for the character at
that position. A character appearing twice in one window correctly accumulates
twice.

Three matmul shapes cover all of it, and they are the only thing worth
optimising: `oai_matmul` (NN), `oai_matmul_nt` (N·Tᵀ) and `oai_matmul_tn_acc`
(Tᵀ·N, accumulating).

## Training loop (`oai_train.c`)

```c
for (;;) {
    if (oai_atomic_load(&t->cancel)) break;      /* the cancellation point */
    if (oai_atomic_load(&t->pause))  { sleep; continue; }
    ...
    forward → backward → clip → adam → report
}
save_checkpoint();                                /* always, on every exit */
```

Cancellation is one flag, polled once per iteration. The worst-case latency is
one batch — under a millisecond at the defaults. The checkpoint write happens
after the loop, so a cancel, a step limit and a `Ctrl+C` all leave the same
state behind.

`model_lock` is held for exactly the forward/backward/step sequence. The UI
takes the same lock when it samples for the chat box, which is why you can ask
the model to write something in the middle of a run without stopping it.

## Compute (`oai_gpu.c`, `oai_pool.c`)

`oai_gpu_or_cpu_matmul` is the only entry point the model uses. It goes to the
GPU when a device is active and the shape is worth the round trip
(`M·K·N ≥ 200,000`); otherwise, and on any failure at all, it falls through to
the CPU path. A dispatch that fails is never fatal.

The budget has three layers, described in `include/oai_gpu.h`: device fission
where the driver offers it, duty cycling where it does not, and a memory cap
either way.

OpenCL is opened with `dlopen`/`LoadLibrary` and its symbols resolved by name.
Nothing links against it, `include/cl_min.h` vendors only the declarations Oai
calls, and no vendor SDK is needed to build.

The CPU pool splits the NN and NT matmuls by output row. `oai_matmul_tn_acc` is
split by output *column* instead — its natural loop order accumulates into every
row of C on each pass over K, so two threads owning different rows would still
share cache lines. Column bands never touch the same float.

## Interface (`oai_ui.c`)

The screen is two arrays of `{code point, style}`. Every frame is drawn into
the back buffer from scratch, then compared with the front buffer, and only the
runs of cells that changed are written out with a cursor move and an SGR
sequence. A frame in which nothing moved costs one hidden-cursor escape.

That is what makes a feed scrolling at 100 lines a second look smooth rather
than torn: the terminal never sees a clear-and-redraw.

Everything else follows from having a cell grid — the sparkline is eighth-block
characters, the panes are just rectangles, the resize path reallocates and
invalidates the front buffer to force a full repaint, and a terminal that
cannot do Unicode gets an ASCII box-drawing fallback.

Lines newer than 0.45 seconds render in a brighter style, which is what makes
new output read as arriving rather than appearing.

## Platform (`oai_platform.c`)

The only file with `#ifdef _WIN32` in it: threads, mutexes, condition
variables, atomics, monotonic time, dynamic loading, raw-mode terminal input
and signal handling. Everything above it is plain C99.

## Where to change things

| You want to | Start in |
| --- | --- |
| change the architecture | `oai_net.c`, and update `oai_net_save`/`load` |
| add an optimiser | `oai_net_adam_step` |
| add a compute backend | mirror `oai_gpu.h`; only `oai_gpu_or_cpu_matmul` is called |
| change what the feed reports | `report_sample` and the reporting block in `train_worker` |
| add a chat command | the verb table at the bottom of `oai_chat.c` |
| change the layout | `draw_frame` in `oai_ui.c` |
