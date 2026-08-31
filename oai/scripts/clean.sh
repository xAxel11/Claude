#!/usr/bin/env sh
# clean.sh -- remove build products. Pass --all to remove checkpoints and logs
# as well.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

rm -rf build bin dist
echo "removed build/ bin/ dist/"

if [ "${1:-}" = "--all" ]; then
    rm -f oai-checkpoint.bin oai.log ./*.tmp
    echo "removed checkpoints and logs"
fi
