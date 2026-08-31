#!/usr/bin/env sh
# bench.sh -- measure training throughput across thread counts and, if a GPU
# is present, across GPU budgets. Every run trains the same number of steps
# from the same seed, so the losses should match exactly and only the speed
# should differ.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

steps="${1:-400}"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

[ -x bin/oai ] || make -j4 >/dev/null

run() {
    label="$1"; shift
    out="$(./bin/oai --no-ui --train --steps "$steps" --sample-every 0 \
           --checkpoint "$tmp/bench.bin" "$@" </dev/null 2>&1 || true)"
    rm -f "$tmp/bench.bin"
    rate="$(printf '%s' "$out" | grep -oE '[0-9]+/s$' | tail -1)"
    loss="$(printf '%s' "$out" | grep -oE 'average loss [0-9.]+' | tail -1)"
    printf '  %-26s %-14s %s\n' "$label" "${rate:-n/a}" "${loss:-n/a}"
}

echo "Oai benchmark -- $steps steps per configuration"
echo "The losses should be identical across every row: the seed is fixed and"
echo "none of these knobs changes the arithmetic, only how fast it runs."
echo
printf '  %-26s %-14s %s\n' "configuration" "steps/s" "result"
printf '  %-26s %-14s %s\n' "-------------" "-------" "------"

run "cpu, 1 thread"  --backend cpu --threads 1
run "cpu, 2 threads" --backend cpu --threads 2
run "cpu, all cores" --backend cpu --threads 0

if ./bin/oai --list-devices | grep -q '^\s*\['; then
    run "gpu, 25% budget" --backend gpu --gpu-budget 0.25
    run "gpu, 50% budget" --backend gpu --gpu-budget 0.5
    run "gpu, 100% budget" --backend gpu --gpu-budget 1.0
else
    echo
    echo "  (no OpenCL device visible, so the GPU rows were skipped)"
fi
