#!/usr/bin/env sh
# demo.sh -- watch the model learn, without the full-screen interface.
#
# Trains for a while and prints a sample every few hundred steps, so you can
# see the output go from noise, to letter frequencies, to words, to something
# that looks like a sentence.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

steps="${1:-4000}"
[ -x bin/oai ] || make -j4 >/dev/null

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "Training for $steps steps. Press Ctrl+C at any point -- it will stop"
echo "cleanly and save what it has learned."
echo

./bin/oai --no-ui --train --steps "$steps" \
          --sample-every 500 --sample-len 220 \
          --checkpoint "$tmp/demo.bin" </dev/null \
    | grep -vE '^step [0-9]+ .*steps/s$'
