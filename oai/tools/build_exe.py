#!/usr/bin/env python3
"""build_exe.py -- compile Oai into a single self-contained executable.

Oai is plain C99 with no runtime dependencies, so "packaging" here means what
it should mean: invoke a C compiler and produce one file you can copy to
another machine and run. There is no interpreter to bundle and nothing to
unpack at start-up.

    python3 tools/build_exe.py                  # native build -> dist/oai
    python3 tools/build_exe.py --target windows # cross-compile -> dist/Oai.exe
    python3 tools/build_exe.py --zip            # ... and a release archive
    python3 tools/build_exe.py --static --strip # portable, minimal binary
    python3 tools/build_exe.py --run            # build, then launch it

Toolchains, in the order each target prefers them:

    windows   x86_64-w64-mingw32-gcc  (cross)   or  cl.exe / gcc  (on Windows)
    linux     cc, gcc, clang
    macos     cc, clang

Nothing here needs a virtualenv, pip, or any package: only the standard
library and a C compiler.

Part of Oai. SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
INCLUDE = ROOT / "include"
BUILD = ROOT / "build"
DIST = ROOT / "dist"

SOURCES = [
    "main.c", "oai_app.c", "oai_chat.c", "oai_config.c", "oai_data.c",
    "oai_gpu.c", "oai_log.c", "oai_net.c", "oai_platform.c", "oai_pool.c",
    "oai_tensor.c", "oai_train.c", "oai_ui.c",
]

# What goes into a release archive alongside the binary.
#
# The sources ship with it on purpose: make_exe.py rebuilds the executable from
# them, so somebody who cannot run the prebuilt binary -- wrong architecture,
# wrong libc, or simply not trusting a binary from the internet -- has a
# one-command path to their own.
EXTRA_FILES = ["README.md", "LICENSE", "CHANGELOG.md", "oai.conf.example",
               "make_exe.py", "Makefile"]
EXTRA_DIRS = ["data", "src", "include", "kernels"]


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

class Colour:
    on = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None
    @staticmethod
    def _c(code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if Colour.on else text
    @staticmethod
    def bold(t): return Colour._c("1", t)
    @staticmethod
    def green(t): return Colour._c("32", t)
    @staticmethod
    def red(t): return Colour._c("31", t)
    @staticmethod
    def yellow(t): return Colour._c("33", t)
    @staticmethod
    def dim(t): return Colour._c("90", t)


def info(msg: str) -> None:
    print(f"{Colour.bold('oai:')} {msg}")


def warn(msg: str) -> None:
    print(f"{Colour.yellow('oai: warning:')} {msg}")


def die(msg: str, hint: str = "") -> "NoReturn":  # type: ignore[valid-type]
    print(f"{Colour.red('oai: error:')} {msg}", file=sys.stderr)
    if hint:
        print(f"      {hint}", file=sys.stderr)
    sys.exit(1)


def which(*names: str) -> str | None:
    for n in names:
        found = shutil.which(n)
        if found:
            return found
    return None


def read_version() -> str:
    """Reads OAI_VERSION_STRING out of include/oai.h so the archive name and
    the binary can never disagree."""
    header = (INCLUDE / "oai.h").read_text(encoding="utf-8")
    for line in header.splitlines():
        if "OAI_VERSION_STRING" in line and '"' in line:
            return line.split('"')[1]
    return "0.0.0"


# --------------------------------------------------------------------------
# toolchain selection
# --------------------------------------------------------------------------

class Toolchain:
    """Everything that differs between compilers, in one place."""

    def __init__(self, target: str):
        self.target = target
        self.msvc = False
        self.exe_suffix = ".exe" if target == "windows" else ""
        self.cc = self._pick_compiler()

    def _pick_compiler(self) -> str:
        env_cc = os.environ.get("CC")
        host_is_windows = platform.system() == "Windows"

        if self.target == "windows":
            if not host_is_windows:
                cc = which("x86_64-w64-mingw32-gcc", "i686-w64-mingw32-gcc")
                if not cc:
                    die("no mingw-w64 cross compiler found",
                        "install it with: apt-get install gcc-mingw-w64  "
                        "(Debian/Ubuntu)  |  brew install mingw-w64  (macOS)")
                return cc
            cc = env_cc or which("gcc", "clang", "cl")
            if not cc:
                die("no C compiler found on PATH",
                    "install MSYS2/mingw-w64, or run this from a Visual "
                    "Studio Developer Command Prompt so cl.exe is available")
            if Path(cc).name.lower().startswith("cl"):
                self.msvc = True
            return cc

        cc = env_cc or which("cc", "gcc", "clang")
        if not cc:
            die("no C compiler found on PATH",
                "install one with: apt-get install build-essential  |  "
                "xcode-select --install")
        return cc

    # -- flags ------------------------------------------------------------

    def compile_cmd(self, source: Path, obj: Path, opts) -> list[str]:
        if self.msvc:
            cmd = [self.cc, "/nologo", "/c", "/W3", f"/I{INCLUDE}",
                   "/D_CRT_SECURE_NO_WARNINGS"]
            cmd += ["/Od", "/Zi"] if opts.debug else ["/O2"]
            cmd += [str(source), f"/Fo{obj}"]
            return cmd

        cmd = [self.cc, "-std=c99", "-c", "-Wall", "-Wextra",
               "-Wno-unused-parameter", f"-I{INCLUDE}"]
        cmd += ["-O0", "-g"] if opts.debug else ["-O2"]
        if self.target != "windows":
            cmd += ["-D_POSIX_C_SOURCE=200809L"]
        if opts.native:
            cmd += ["-march=native"]
        cmd += [str(source), "-o", str(obj)]
        return cmd

    def link_cmd(self, objs: list[Path], out: Path, opts) -> list[str]:
        if self.msvc:
            cmd = [self.cc, "/nologo"] + [str(o) for o in objs]
            cmd += [f"/Fe{out}", "/link"]
            if opts.debug:
                cmd += ["/DEBUG"]
            return cmd

        cmd = [self.cc] + [str(o) for o in objs] + ["-o", str(out)]
        if self.target == "windows":
            # Threads and dynamic loading come from the Win32 API; only libm
            # is a separate library, and mingw folds it in anyway.
            cmd += ["-lm"]
            if opts.static:
                cmd += ["-static", "-static-libgcc"]
        else:
            cmd += ["-lm", "-lpthread", "-ldl"]
            if opts.static:
                # A fully static glibc build warns about dlopen; Oai only
                # dlopens OpenCL, which a static build cannot use anyway.
                cmd += ["-static"]
        return cmd

    def obj_suffix(self) -> str:
        return ".obj" if self.msvc else ".o"

    def describe(self) -> str:
        try:
            flag = "/?" if self.msvc else "--version"
            out = subprocess.run([self.cc, flag], capture_output=True,
                                 text=True, timeout=20)
            first = (out.stdout or out.stderr).splitlines()[0].strip()
            return f"{self.cc} ({first})"
        except Exception:
            return self.cc


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------

def compile_all(tc: Toolchain, opts) -> list[Path]:
    objdir = BUILD / f"{opts.target}{'-debug' if opts.debug else ''}"
    objdir.mkdir(parents=True, exist_ok=True)

    jobs: list[tuple[Path, Path, list[str]]] = []
    for name in SOURCES:
        source = SRC / name
        if not source.exists():
            die(f"missing source file {source}")
        obj = objdir / (Path(name).stem + tc.obj_suffix())
        if (not opts.force and obj.exists()
                and obj.stat().st_mtime > newest_header_mtime()
                and obj.stat().st_mtime > source.stat().st_mtime):
            continue                      # already up to date
        jobs.append((source, obj, tc.compile_cmd(source, obj, opts)))

    objs = [objdir / (Path(n).stem + tc.obj_suffix()) for n in SOURCES]

    if not jobs:
        info("all objects up to date")
        return objs

    info(f"compiling {len(jobs)} file(s) with {opts.jobs} job(s)")
    failures: list[tuple[str, str]] = []

    def run_one(job):
        source, obj, cmd = job
        if opts.verbose:
            print(Colour.dim("  " + " ".join(cmd)))
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              cwd=str(ROOT))
        return source, proc

    with ThreadPoolExecutor(max_workers=opts.jobs) as pool:
        for source, proc in pool.map(run_one, jobs):
            label = source.name
            if proc.returncode != 0:
                failures.append((label, proc.stdout + proc.stderr))
                print(f"  {Colour.red('FAIL')} {label}")
            else:
                if proc.stderr.strip() and opts.verbose:
                    print(Colour.dim(proc.stderr.rstrip()))
                print(f"  {Colour.green('ok')}   {label}")

    if failures:
        print()
        for label, output in failures:
            print(f"{Colour.red('--- ' + label)}")
            print(output.rstrip())
        die(f"{len(failures)} file(s) failed to compile")

    return objs


_header_mtime: float | None = None


def newest_header_mtime() -> float:
    """Any header change rebuilds everything -- the dependency graph is small
    enough that tracking it properly would cost more than it saves."""
    global _header_mtime
    if _header_mtime is None:
        times = [h.stat().st_mtime for h in INCLUDE.glob("*.h")]
        _header_mtime = max(times) if times else 0.0
    return _header_mtime


def link(tc: Toolchain, objs: list[Path], opts) -> Path:
    DIST.mkdir(parents=True, exist_ok=True)
    out = DIST / (opts.name + tc.exe_suffix)
    cmd = tc.link_cmd(objs, out, opts)
    if opts.verbose:
        print(Colour.dim("  " + " ".join(cmd)))
    proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        die("link failed")
    if proc.stderr.strip() and opts.verbose:
        print(Colour.dim(proc.stderr.rstrip()))

    if opts.strip and not tc.msvc:
        stripper = which(f"{tc.cc.rsplit('-', 1)[0]}-strip") if "-" in Path(tc.cc).name else None
        stripper = stripper or which("strip")
        if stripper:
            subprocess.run([stripper, str(out)], capture_output=True)
    return out


def verify(binary: Path, target: str) -> None:
    """Runs --version on a native build. A cross-compiled binary is only
    checked for existence and plausibility."""
    host = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(),
                                                         "linux")
    size_kb = binary.stat().st_size / 1024.0
    if target != host:
        info(f"built {binary.name} ({size_kb:.0f} KB) for {target} "
             f"-- cannot run it from {host}, so it was not smoke-tested")
        return
    try:
        proc = subprocess.run([str(binary), "--version"], capture_output=True,
                              text=True, timeout=30)
        if proc.returncode == 0 and "oai" in proc.stdout.lower():
            info(f"built {binary.name} ({size_kb:.0f} KB) -- "
                 f"{proc.stdout.strip()} runs")
        else:
            warn(f"{binary.name} did not answer --version as expected: "
                 f"{(proc.stdout + proc.stderr).strip()[:200]}")
    except Exception as exc:            # pragma: no cover - defensive
        warn(f"could not smoke-test the binary: {exc}")


# --------------------------------------------------------------------------
# packaging
# --------------------------------------------------------------------------

def make_zip(binary: Path, opts) -> Path:
    version = read_version()
    label = opts.target if opts.target != "native" else platform.system().lower()
    archive = DIST / f"Oai-{version}-{label}-x86_64.zip"

    write_example_config()

    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED,
                         compresslevel=9) as z:
        root = f"Oai-{version}"
        z.write(binary, f"{root}/{binary.name}")
        for name in EXTRA_FILES:
            path = ROOT / name
            if path.exists():
                z.write(path, f"{root}/{name}")
        for d in EXTRA_DIRS:
            base = ROOT / d
            if not base.exists():
                continue
            for path in sorted(base.rglob("*")):
                if path.is_file():
                    z.write(path, f"{root}/{path.relative_to(ROOT)}")
        z.writestr(f"{root}/RUNNING.txt", running_notes(binary.name))

    size_kb = archive.stat().st_size / 1024.0
    info(f"packaged {archive.relative_to(ROOT)} ({size_kb:.0f} KB)")
    return archive


def running_notes(binary_name: str) -> str:
    windows = binary_name.endswith(".exe")
    run_line = binary_name if windows else f"./{binary_name}"
    return f"""Oai {read_version()}
=========================================

Run it:

    {run_line}

That opens the interface. Press Ctrl+T, or type "train" in the chat box, to
start learning; Ctrl+X or "stop" cancels it. Cancelling always writes a
checkpoint first, so starting again picks up where you left off.

Useful flags:

    {run_line} --corpus mytext.txt --train
    {run_line} --gpu-budget 0.25       use a quarter of the GPU
    {run_line} --backend cpu           ignore the GPU entirely
    {run_line} --no-ui --steps 5000    headless, for logs and CI
    {run_line} --list-devices          what OpenCL can see
    {run_line} --help                  everything else


Rebuilding it yourself
----------------------

The C sources are in this archive too, so you never have to take the bundled
binary on trust. With Python 3 and a C compiler installed:

    python make_exe.py

That is the whole command -- no arguments, no virtualenv, nothing to install
from pip. It compiles src/ and leaves a fresh {binary_name} right here. If no
compiler is found it tells you exactly what to install for your system and
stops. On Windows you can double-click make_exe.py instead of typing anything.

    python make_exe.py --run       build it and start it immediately
    python make_exe.py --clean     remove the build files
    python make_exe.py --help      the rest

If you prefer make, "make" works too and produces bin/{binary_name}.

The binary is self-contained: it needs no runtime, no model download and no
data files. data/corpus.txt is included because it is nicer to learn from than
the small corpus compiled into the binary, but Oai runs without it.

A GPU is optional. Oai loads the OpenCL runtime by name at start-up; if there
is none, or no device, it trains on the processor and says so.
"""


def write_example_config() -> None:
    path = ROOT / "oai.conf.example"
    if path.exists():
        return
    path.write_text(
        "# Copy to oai.conf next to the binary; command-line flags win.\n"
        "corpus = data/corpus.txt\n"
        "checkpoint = oai-checkpoint.bin\n"
        "hidden = 256\n"
        "context = 12\n"
        "embed = 24\n"
        "batch = 64\n"
        "lr = 0.003\n"
        "gpu-budget = 0.5\n"
        "temp = 0.8\n"
        "train = 0\n", encoding="utf-8")


# --------------------------------------------------------------------------
# entry point
# --------------------------------------------------------------------------

def main() -> int:
    host = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(),
                                                         "linux")

    ap = argparse.ArgumentParser(
        prog="build_exe.py",
        description="Compile Oai into a single self-contained executable.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Toolchains")[0].strip())
    ap.add_argument("--target", choices=["native", "windows", "linux", "macos"],
                    default="native",
                    help="what to build for (default: this machine)")
    ap.add_argument("--name", default=None,
                    help="output name without the extension "
                         "(default: Oai for Windows, oai elsewhere)")
    ap.add_argument("--jobs", "-j", type=int, default=os.cpu_count() or 4,
                    help="parallel compile jobs")
    ap.add_argument("--debug", action="store_true",
                    help="build unoptimised with debug info")
    ap.add_argument("--static", action="store_true",
                    help="link statically where the platform allows it")
    ap.add_argument("--strip", action="store_true",
                    help="strip symbols from the finished binary")
    ap.add_argument("--native", action="store_true",
                    help="optimise for this exact CPU (-march=native); the "
                         "binary may not run elsewhere")
    ap.add_argument("--zip", action="store_true",
                    help="also write dist/Oai-<version>-<platform>.zip")
    ap.add_argument("--run", action="store_true",
                    help="launch the binary once it is built")
    ap.add_argument("--clean", action="store_true",
                    help="remove build/ and dist/ first")
    ap.add_argument("--force", "-B", action="store_true",
                    help="rebuild every object even if it looks current")
    ap.add_argument("--verbose", "-v", action="store_true")
    opts = ap.parse_args()

    if opts.target == "native":
        opts.target = host
    if opts.name is None:
        opts.name = "Oai" if opts.target == "windows" else "oai"

    if opts.clean:
        for d in (BUILD, DIST):
            if d.exists():
                shutil.rmtree(d)
        info("removed build/ and dist/")

    tc = Toolchain(opts.target)
    info(f"Oai {read_version()} -> {opts.target}")
    info(f"compiler: {tc.describe()}")

    started = time.time()
    objs = compile_all(tc, opts)
    binary = link(tc, objs, opts)
    info(f"linked in {time.time() - started:.1f}s")

    verify(binary, opts.target)

    if opts.zip:
        make_zip(binary, opts)

    if opts.run:
        if opts.target != host:
            warn("--run ignored: this binary is not for this machine")
        else:
            info(f"launching {binary}")
            return subprocess.call([str(binary)])

    print()
    print(f"  {Colour.bold(str(binary.relative_to(ROOT)))}")
    print(f"  {Colour.dim('run it with: ' + str(binary.relative_to(ROOT)))}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\noai: interrupted", file=sys.stderr)
        sys.exit(130)
