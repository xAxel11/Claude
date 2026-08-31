#!/usr/bin/env sh
# test.sh -- build and run the whole test suite.
#
#   ./scripts/test.sh            the unit tests
#   ./scripts/test.sh --all      unit tests, a sanitizer pass, and a short
#                                end-to-end training run
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
all=0
[ "${1:-}" = "--all" ] && all=1

echo "=== unit tests ==="
make -j"$jobs" test

if [ "$all" -eq 1 ]; then
    echo
    echo "=== sanitizer pass ==="
    if make -j"$jobs" debug >/dev/null 2>&1; then
        make -j"$jobs" test
        make clean >/dev/null
        make -j"$jobs" >/dev/null
    else
        echo "(sanitizers unavailable on this toolchain -- skipped)"
    fi

    echo
    echo "=== end-to-end: 300 steps, headless ==="
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    ./bin/oai --no-ui --train --steps 300 --sample-every 300 \
              --checkpoint "$tmp/ckpt.bin" </dev/null | tail -20
    if [ ! -s "$tmp/ckpt.bin" ]; then
        echo "FAILED: no checkpoint was written" >&2
        exit 1
    fi
    echo
    echo "=== resume from that checkpoint ==="
    ./bin/oai --no-ui --train --steps 50 --sample-every 0 \
              --checkpoint "$tmp/ckpt.bin" </dev/null | grep -E "resumed|stopped"
fi

echo
echo "all good"
