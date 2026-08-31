#!/usr/bin/env sh
# run.sh -- build if needed, then launch Oai. Arguments are passed straight
# through, so `./scripts/run.sh --train --gpu-budget 0.25` works.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

if [ ! -x bin/oai ]; then
    echo "run.sh: building first..."
    make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" >/dev/null
fi
exec ./bin/oai "$@"
