#!/usr/bin/env python3
"""make_exe.py -- turn Oai into an executable. One command, no arguments.

    python make_exe.py

That is the whole thing. It finds a C compiler, compiles the sources next to
this file, and leaves a single executable beside it:

    Oai.exe     on Windows
    oai         on Linux and macOS

On Windows you can just double-click this file. If no compiler is installed it
will tell you exactly what to install and stop -- it never fails silently.

Options, none of which you need:

    python make_exe.py --windows    cross-compile Oai.exe from Linux/macOS
    python make_exe.py --run        build it, then start it
    python make_exe.py --clean      delete the objects and the executable
    python make_exe.py --debug      unoptimised build with debug symbols
    python make_exe.py --help

The result is self-contained. Oai is written in C, so there is no interpreter
to bundle and nothing to unpack at start-up -- Python is used here only to
drive the compiler. You can copy the finished executable to another machine of
the same kind and run it, with or without a GPU.

This script is deliberately standalone: it needs nothing but the Python
standard library and the src/ and include/ folders shipped alongside it. For
the fuller build (cross-compiling, release archives, incremental rebuilds) see
tools/build_exe.py.

Part of Oai. SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent

SOURCES = [
    "main.c", "oai_app.c", "oai_chat.c", "oai_config.c", "oai_data.c",
    "oai_gpu.c", "oai_log.c", "oai_net.c", "oai_platform.c", "oai_pool.c",
    "oai_tensor.c", "oai_train.c", "oai_ui.c",
]

HOST = {"Windows": "windows", "Darwin": "macos"}.get(platform.system(), "linux")

INSTALL_HINTS = {
    "windows": (
        "Install a C compiler, then run this again:\n"
        "  - Easiest: MSYS2 from https://www.msys2.org, then in its terminal\n"
        "      pacman -S mingw-w64-ucrt-x86_64-gcc\n"
        "    and add C:\\msys64\\ucrt64\\bin to your PATH.\n"
        "  - Or: Visual Studio Build Tools with the \"Desktop development\n"
        "    with C++\" workload, then run this from a\n"
        "    \"Developer Command Prompt\" so cl.exe is on PATH."
    ),
    "linux": (
        "Install a C compiler, then run this again:\n"
        "  Debian/Ubuntu:  sudo apt-get install build-essential\n"
        "  Fedora:         sudo dnf install gcc\n"
        "  Arch:           sudo pacman -S base-devel\n"
        "  Alpine:         apk add build-base"
    ),
    "macos": (
        "Install the command line tools, then run this again:\n"
        "  xcode-select --install"
    ),
    "cross": (
        "Cross-compiling to Windows needs mingw-w64:\n"
        "  Debian/Ubuntu:  sudo apt-get install gcc-mingw-w64-x86-64\n"
        "  Fedora:         sudo dnf install mingw64-gcc\n"
        "  macOS:          brew install mingw-w64"
    ),
}


# --------------------------------------------------------------------------

def colour(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def step(msg: str) -> None:
    print(f"{colour('1;36', '::')} {msg}")


def fail(msg: str, hint: str = "") -> None:
    print()
    print(colour("1;31", "Could not build Oai."))
    print(f"  {msg}")
    if hint:
        print()
        for line in hint.splitlines():
            print(f"  {line}")
    pause_if_double_clicked()
    sys.exit(1)


def pause_if_double_clicked() -> None:
    """Keeps the window open when this was launched from Explorer rather than
    from a terminal, so the message is actually readable."""
    if HOST != "windows" or not sys.stdin.isatty():
        return
    if os.environ.get("PROMPT") or os.environ.get("OAI_NO_PAUSE"):
        return          # started from an existing command prompt
    try:
        input("\nPress Enter to close this window...")
    except (EOFError, KeyboardInterrupt):
        pass


def find_compiler(target: str) -> tuple[str, bool]:
    """Returns (compiler path, is_msvc)."""
    if target == "windows" and HOST != "windows":
        cc = shutil.which("x86_64-w64-mingw32-gcc")
        if not cc:
            fail("no mingw-w64 cross compiler was found on PATH.",
                 INSTALL_HINTS["cross"])
        return cc, False

    env_cc = os.environ.get("CC")
    if env_cc and shutil.which(env_cc):
        return shutil.which(env_cc), Path(env_cc).name.lower().startswith("cl")

    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found, False
    if HOST == "windows":
        found = shutil.which("cl")
        if found:
            return found, True
    fail("no C compiler was found on PATH.", INSTALL_HINTS[HOST])
    raise AssertionError("unreachable")


def compiler_version(cc: str, msvc: bool) -> str:
    try:
        out = subprocess.run([cc, "/?" if msvc else "--version"],
                             capture_output=True, text=True, timeout=20)
        return (out.stdout or out.stderr).splitlines()[0].strip()
    except Exception:
        return cc


# --------------------------------------------------------------------------

def build(target: str, debug: bool) -> Path:
    src, include = HERE / "src", HERE / "include"
    if not src.is_dir() or not include.is_dir():
        fail(f"the src/ and include/ folders are not next to {Path(__file__).name}.",
             "Unzip the whole archive and run this from inside the folder it\n"
             "creates -- not from a copy of this one file.")

    missing = [n for n in SOURCES if not (src / n).exists()]
    if missing:
        fail(f"these source files are missing from src/: {', '.join(missing)}",
             "The archive looks incomplete. Unzip it again.")

    cc, msvc = find_compiler(target)
    step(f"compiler: {compiler_version(cc, msvc)}")

    objdir = HERE / "build" / f"{target}{'-debug' if debug else ''}"
    objdir.mkdir(parents=True, exist_ok=True)
    obj_ext = ".obj" if msvc else ".o"

    def compile_cmd(name: str) -> tuple[str, list[str], Path]:
        source, obj = src / name, objdir / (Path(name).stem + obj_ext)
        if msvc:
            cmd = [cc, "/nologo", "/c", "/W3", f"/I{include}",
                   "/D_CRT_SECURE_NO_WARNINGS",
                   *(["/Od", "/Zi"] if debug else ["/O2"]),
                   str(source), f"/Fo{obj}"]
        else:
            cmd = [cc, "-std=c99", "-c", "-Wall", "-Wextra",
                   "-Wno-unused-parameter", f"-I{include}",
                   *(["-O0", "-g"] if debug else ["-O2"])]
            if target != "windows":
                cmd.append("-D_POSIX_C_SOURCE=200809L")
            cmd += [str(source), "-o", str(obj)]
        return name, cmd, obj

    jobs = [compile_cmd(n) for n in SOURCES]
    step(f"compiling {len(jobs)} files")

    failures = []

    def run_one(job):
        name, cmd, _ = job
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=str(HERE))
        return name, proc

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        for name, proc in pool.map(run_one, jobs):
            if proc.returncode != 0:
                failures.append((name, proc.stdout + proc.stderr))
                print(f"   {colour('1;31', 'failed')}  {name}")
            else:
                print(f"   {colour('32', 'ok')}      {name}")

    if failures:
        print()
        for name, output in failures:
            print(colour("1;31", f"--- {name}"))
            print(output.rstrip())
        fail(f"{len(failures)} file(s) did not compile.",
             "If your compiler is very old, it may not support C99. Please\n"
             "report this with the output above.")

    out = HERE / ("Oai.exe" if target == "windows" else "oai")
    objs = [str(o) for _, _, o in jobs]

    step(f"linking {out.name}")
    if msvc:
        link = [cc, "/nologo", *objs, f"/Fe{out}", "/link"] + (["/DEBUG"] if debug else [])
    elif target == "windows":
        link = [cc, *objs, "-o", str(out), "-lm", "-static", "-static-libgcc"]
    else:
        link = [cc, *objs, "-o", str(out), "-lm", "-lpthread", "-ldl"]

    proc = subprocess.run(link, capture_output=True, text=True, cwd=str(HERE))
    if proc.returncode != 0:
        print(proc.stdout + proc.stderr, file=sys.stderr)
        fail("linking failed.",
             "This usually means a library is missing. On Linux, "
             "build-essential\nprovides everything Oai needs.")

    if not debug and not msvc:
        # A cross-compiled binary needs its own toolchain's strip; the host
        # one does not understand the object format.
        prefix = Path(cc).name
        stripper = None
        if "-gcc" in prefix:
            stripper = shutil.which(prefix.rsplit("-gcc", 1)[0] + "-strip")
        if stripper is None and target == HOST:
            stripper = shutil.which("strip")
        if stripper:
            subprocess.run([stripper, str(out)], capture_output=True)
    return out


def smoke_test(binary: Path, target: str) -> None:
    if target != HOST:
        step(f"built for {target}; it cannot be run from {HOST}, so it was "
             f"not tested here")
        return
    try:
        proc = subprocess.run([str(binary), "--version"], capture_output=True,
                              text=True, timeout=30)
        if proc.returncode == 0 and "oai" in proc.stdout.lower():
            step(f"{proc.stdout.strip()} runs")
            return
        print(colour("33", f"   warning: --version returned "
                           f"{(proc.stdout + proc.stderr).strip()[:120]}"))
    except Exception as exc:
        print(colour("33", f"   warning: could not test the binary: {exc}"))


def clean() -> None:
    removed = []
    for path in (HERE / "build", HERE / "Oai.exe", HERE / "oai"):
        if path.is_dir():
            shutil.rmtree(path)
            removed.append(path.name + "/")
        elif path.exists():
            path.unlink()
            removed.append(path.name)
    print("removed " + (", ".join(removed) if removed else "nothing"))


# --------------------------------------------------------------------------

def main() -> int:
    args = sys.argv[1:]

    if any(a in ("-h", "--help") for a in args):
        print(__doc__)
        return 0

    unknown = [a for a in args
               if a not in ("--windows", "--run", "--clean", "--debug")]
    if unknown:
        print(f"make_exe.py: I do not understand {' '.join(unknown)}")
        print("Run  python make_exe.py --help  to see the options,")
        print("or just  python make_exe.py  with no options at all.")
        return 2

    if "--clean" in args:
        clean()
        return 0

    target = "windows" if "--windows" in args else HOST
    debug = "--debug" in args

    print(colour("1", f"Building Oai for {target}"))
    started = time.time()
    binary = build(target, debug)
    size_kb = binary.stat().st_size / 1024.0
    step(f"done in {time.time() - started:.1f}s")
    smoke_test(binary, target)

    print()
    print(colour("1;32", f"  {binary.name}") + f"  ({size_kb:.0f} KB)")
    print()
    if target == "windows":
        print("  Run it by double-clicking Oai.exe, or from a command prompt:")
        print("      Oai.exe")
    else:
        print("  Run it with:")
        print(f"      ./{binary.name}")
    print()
    print("  Then press Ctrl+T to start training, Ctrl+X to stop.")
    print("  Ctrl+X always saves first, so stopping never loses progress.")

    if "--run" in args:
        if target != HOST:
            print("\n(--run skipped: that executable is not for this machine)")
        else:
            print()
            return subprocess.call([str(binary)])
    else:
        pause_if_double_clicked()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nstopped")
        sys.exit(130)
