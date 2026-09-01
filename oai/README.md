# Oai

**An open-source AI agent written in C. It learns to write, shows you what it
is learning while it does it, and stops the moment you ask.**

Oai trains a character-level neural language model from scratch — no
framework, no dependencies, no model download. The left half of the screen is a
live feed of what it is learning: the loss falling, the gradients, and samples
of the text it can produce right now. The right half is a chat box you can talk
to while it trains.

It uses **part of your GPU, never all of it**, and cancelling training costs you
nothing but the batch that was in flight.

```
╭──────────────────────────────────────────────────────────────────────────────╮
 Oai 1.0.0  ~  an agent that learns to write, in C          / training step 1840
 loss 1.8123   avg 1.9044   best 1.9044   perplexity 6.7   112 steps/s   16s
 NVIDIA GeForce RTX 3060   14/28 compute units   budget 50%   duty cycled
 loss ▇▇▆▆▆▅▅▅▄▄▄▄▃▃▃▃▃▂▂▂▂▂▁▁▁            training
─────────────────────────────────────────┬────────────────────────────────────
 LEARNING                                │ CHAT
 step 1820  loss 1.9441  avg 1.9102 ...  │ you  train
 --- step 1800: this is what it writes---│ oai  Training. Watch the left pane
 the model reads a window of the loss    │      -- it shows the loss falling
 and the gradient of the network the     │      and samples of what the model
 sample of what it learns to the model   │      can write. Say "stop" whenever
 after "the " -> ' ' 31%, m 12%, l 9%    │      you want; nothing is lost.
 held-out loss 2.2140  (perplexity 9.2)  │ you  what have you learned
 step 1840  loss 1.8123  avg 1.9044 ...  │ oai  At step 1840 its strongest
                                         ├────────────────────────────────────
                                         │ > _
─────────────────────────────────────────┴────────────────────────────────────
 ^T train   ^X stop   ^P pause   Tab focus   PgUp/PgDn scroll   ^C quit
╰──────────────────────────────────────────────────────────────────────────────╯
```

---

## Quick start

```sh
git clone <this repo> && cd oai
make                    # or: ./scripts/build.sh
./bin/oai
```

Then press **`Ctrl+T`** (or type `train`) and watch the left pane. Press
**`Ctrl+X`** to cancel at any point — it stops within one batch and writes a checkpoint before
the thread returns, so starting again resumes exactly where you left off.

### Or build the executable with one Python command

If you would rather not deal with `make`:

```sh
python make_exe.py
```

That is the entire command — no arguments, no virtualenv, nothing to install
from pip. It finds a C compiler, compiles `src/`, and leaves `Oai.exe` (Windows)
or `oai` (Linux, macOS) right beside itself. On Windows you can double-click
`make_exe.py` instead of typing anything; if no compiler is installed it prints
exactly what to install for your system and stops rather than failing quietly.

```sh
python make_exe.py --run       # build it, then start it
python make_exe.py --windows   # cross-compile Oai.exe from Linux or macOS
python make_exe.py --clean
```

`make_exe.py` ships inside every release archive alongside the C sources, so
you never have to take a downloaded binary on trust — you can always rebuild it
in one command.

For release engineering — incremental rebuilds, stripping, packaging archives —
`tools/build_exe.py` is the fuller version:

```sh
python3 tools/build_exe.py                     # -> dist/oai
python3 tools/build_exe.py --target windows    # -> dist/Oai.exe (cross)
python3 tools/build_exe.py --zip --strip       # -> dist/Oai-1.0.0-linux-x86_64.zip
```

Either way the result is one self-contained file. There is no interpreter to
bundle and nothing to unpack at start-up, because Oai is C: Python only drives
the compiler. The corpus fallback and the GPU kernels are both compiled into
the binary.

---

## What you can say to it

Anything you type that is not a command is fed to the model as a prompt and it
continues your text. Early in training the continuation is noise. That is not a
bug — it is the most direct read-out there is of how far it has got.

| Command | What it does |
| --- | --- |
| `train` | start learning |
| `stop` | cancel — checkpoints first, loses nothing |
| `pause` | freeze the loop without tearing it down |
| `status` | step, loss, perplexity, throughput, device |
| `learned` | the characters it currently expects after several prefixes |
| `sample <seed>` | generate text from a prompt |
| `lr 0.001` | change the learning rate mid-run |
| `temp 0.6` | sampling temperature |
| `gpu 0.25` | change the GPU budget mid-run |
| `corpus` | what it is learning from |
| `save` | write a checkpoint now |
| `help` | all of the above |

Keys: `Ctrl+T` train, `Ctrl+X` stop, `Ctrl+P` pause, `Ctrl+L` redraw, `Ctrl+C`
quit. `Tab` moves focus between the panes; `PgUp`/`PgDn` scroll the learning
feed and `End` returns to the live tail.

The shortcuts are control combinations rather than plain letters on purpose:
letters always type. (An earlier version treated `t`/`s`/`p` as hotkeys whenever
the input line was empty, so typing "status" fired *stop* and then *train*
before the third character arrived.) Quitting always cancels training, writes a
checkpoint and restores the terminal rather than dying mid-write.

With focus on the learning pane (`Tab`), single letters do work as shortcuts —
`t`, `s`, `p`, `q` — because there is nothing to type into over there.

---

## Using part of the GPU

Most programs take the whole device and hold it. Oai asks for a share and
sticks to it, using three mechanisms in order of preference:

1. **Device fission.** It asks the driver for a sub-device holding
   `ceil(compute_units × budget)` units and runs only inside it. Where the
   driver supports this the limit is a hard partition and the rest of the GPU
   is genuinely untouched. Most datacentre and CPU OpenCL devices support it;
   most consumer GPUs do not.
2. **Duty cycling.** Where fission is unavailable, Oai measures how long each
   dispatch occupied the device and then deliberately yields for
   `elapsed × (1/budget − 1)` afterwards. Over a few seconds the occupancy
   settles at the budget you asked for, and other work on the machine keeps
   running.
3. **A memory cap.** Device allocations are capped at the same fraction of
   global memory. If the batch will not fit inside that cap, Oai declines the
   device for that call and runs it on the CPU instead.

```sh
./bin/oai --gpu-budget 0.25          # a quarter of the GPU
./bin/oai --gpu-budget 1.0           # all of it, if you want
./bin/oai --backend cpu              # ignore the GPU entirely
./bin/oai --list-devices             # what OpenCL can see from here
```

Oai never links against OpenCL. It loads the runtime by name at start-up
(`libOpenCL.so.1`, `OpenCL.dll`, …) and resolves the two dozen symbols it
needs. The same binary therefore runs on a machine with a GPU and one without,
and building it needs no vendor SDK. With no runtime, no device, or a kernel
that fails to build, it says so and trains on the CPU.

**The CPU path is not an afterthought.** The matmuls are cache-blocked and
split across a worker pool (`--threads`, default one per core), which is worth
roughly 1.8× on four cores.

---

## What it actually is

A feedforward character model — the architecture from Bengio et al. (2003),
not a transformer:

```
  ids[context] ──▶ embedding table ──▶ concatenate
                                          │
                                     W1 ──┴──▶ tanh ──▶ W2 ──▶ softmax ──▶ loss
```

At the defaults that is a 12-character window, 24-dimensional embeddings, 256
hidden units, and about 82,000 parameters. Trained with Adam, decoupled weight
decay, and gradient-norm clipping.

There is no attention here and no pretraining. It is in this repository because
everything in a modern system is present in it in miniature — a learned
representation, a nonlinear mixing layer, a distribution over a vocabulary, a
loss that measures surprise, gradients from running the computation backwards,
and an optimiser that decides how far to move — in about 4,500 lines you can
read in an afternoon.

### About the bundled corpus

`data/corpus.txt` is roughly 12 KB of original prose about how the thing works.
It is big enough to watch words appear and small enough that **the model will
memorise it**: around step 2,000 the held-out loss turns upward while the
training loss keeps falling. That is textbook overfitting, and being able to
watch it happen live is arguably the most useful thing this program does.

For real training, give it more text:

```sh
python3 tools/gen_corpus.py --from-files ~/notes/*.txt --out data/mine.txt --lower
./bin/oai --corpus data/mine.txt --train
```

---

## Options

```
Data        --corpus FILE   --checkpoint FILE   --log FILE
Model       --context N     --embed N           --hidden N
Training    --batch N       --lr RATE           --weight-decay W
            --clip NORM     --steps N           --seed N        --train
Compute     --backend auto|cpu|gpu              --gpu-budget 0.05-1.0
            --gpu INDEX     --threads N         --list-devices
Interface   --no-ui         --sample-every N    --sample-len N
            --temp T        --quiet
Other       --config FILE   --version           --help
```

Settings can also live in an `oai.conf` beside the binary (see
`oai.conf.example`); command-line flags always win.

Headless mode works properly, which makes it usable from CI and pipes:

```sh
./bin/oai --no-ui --train --steps 5000 --sample-every 1000 > run.log
```

---

## Layout

```
make_exe.py   one-command build; ships in every release archive
include/      public headers, one per module
src/          the implementation
  oai_tensor.c    matrices and the three matmuls
  oai_net.c       forward, backward, Adam, checkpoints
  oai_train.c     the loop, its cancellation and its reporting
  oai_gpu.c       runtime-loaded OpenCL with the resource budget
  oai_pool.c      the CPU worker pool behind --threads
  oai_ui.c       double-buffered terminal rendering
  oai_chat.c      commands and replies
  oai_platform.c  the only file that knows about POSIX vs Win32
kernels/      the OpenCL source, embedded into the binary at build time
tests/        unit tests, including a finite-difference gradient check
tools/        build_exe.py, make_zip.py, gen_corpus.py, embed_kernels.py
scripts/      build, run, test, bench, package, analyze, install
data/         the bundled corpus
docs/         architecture notes
```

---

## Building and testing

```sh
python make_exe.py        # ./oai or Oai.exe, no arguments needed
make                      # bin/oai
make test                 # unit tests
make debug                # address + UB sanitizers
make windows              # cross-compile with mingw-w64
./scripts/test.sh --all   # tests, sanitizers, and an end-to-end run
./scripts/bench.sh        # throughput across thread counts and GPU budgets
./scripts/analyze.sh      # cppcheck, scan-build, sanitizers, warnings
./scripts/package.sh --all
```

The test suite checks the backward pass against finite differences, which is
the one thing unit tests can do that watching the loss cannot: a sign error or a
transposed matrix still produces a loss that falls, just toward the wrong thing.

Requirements: a C99 compiler and `make`. Everything else is optional.

---

## Licence

MIT. See [LICENSE](LICENSE).
