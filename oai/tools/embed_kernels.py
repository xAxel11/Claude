#!/usr/bin/env python3
"""embed_kernels.py -- regenerate the OpenCL source embedded in oai_gpu.c.

Oai compiles its GPU kernels from a string held in the binary, so a single
executable needs no data files. kernels/matmul.cl is the readable copy of that
string; this script keeps the two in step.

    python3 tools/embed_kernels.py --check    fail if they have diverged
    python3 tools/embed_kernels.py            rewrite the embedded copy

Part of Oai. SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
KERNEL = ROOT / "kernels" / "matmul.cl"
TARGET = ROOT / "src" / "oai_gpu.c"

BEGIN = "static const char *k_kernel_source =\n"
END = ";\n"


def to_c_string(text: str) -> str:
    """Turns kernel source into a C string literal, one line at a time so the
    result stays diff-friendly."""
    out = []
    for line in text.splitlines():
        if line.strip().startswith("/*") or line.strip().startswith("*"):
            continue                      # comments do not need shipping
        if not line.strip():
            continue
        escaped = line.replace("\\", "\\\\").replace('"', '\\"')
        out.append(f'"{escaped}\\n"')
    return BEGIN + "\n".join(out) + END


def current_block(source: str) -> tuple[int, int] | None:
    start = source.find(BEGIN)
    if start == -1:
        return None
    end = source.find(END, start)
    if end == -1:
        return None
    return start, end + len(END)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true",
                    help="exit non-zero if the embedded copy is stale")
    opts = ap.parse_args()

    if not KERNEL.exists():
        raise SystemExit(f"embed_kernels: {KERNEL} is missing")

    source = TARGET.read_text(encoding="utf-8")
    span = current_block(source)
    if span is None:
        raise SystemExit("embed_kernels: could not find k_kernel_source in "
                         f"{TARGET}")

    wanted = to_c_string(KERNEL.read_text(encoding="utf-8"))
    existing = source[span[0]:span[1]]

    def squash(s: str) -> str:
        return re.sub(r"\s+", "", s)

    if squash(existing) == squash(wanted):
        print("embedded kernels are up to date")
        return 0

    if opts.check:
        print("embed_kernels: src/oai_gpu.c is out of step with "
              "kernels/matmul.cl", file=sys.stderr)
        print("  run: python3 tools/embed_kernels.py", file=sys.stderr)
        return 1

    TARGET.write_text(source[:span[0]] + wanted + source[span[1]:],
                      encoding="utf-8")
    print(f"updated the embedded kernel source in {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
