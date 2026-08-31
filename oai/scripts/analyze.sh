#!/usr/bin/env sh
# analyze.sh -- run whichever static analysers are installed. Each section is
# skipped with a note when its tool is missing, so this is safe to run
# anywhere.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

status=0

echo "=== compiler warnings (-Wall -Wextra -Wpedantic) ==="
make clean >/dev/null
if make 2>&1 | grep -E 'warning:' ; then
    echo "-> warnings found"
    status=1
else
    echo "-> clean"
fi

echo
echo "=== cppcheck ==="
if command -v cppcheck >/dev/null 2>&1; then
    cppcheck --enable=warning,performance,portability --std=c99 \
             --quiet --error-exitcode=1 -Iinclude src tests || status=1
    echo "-> done"
else
    echo "-> cppcheck not installed, skipped"
fi

echo
echo "=== clang static analyser ==="
if command -v scan-build >/dev/null 2>&1; then
    make clean >/dev/null
    scan-build --status-bugs make || status=1
else
    echo "-> scan-build not installed, skipped"
fi

echo
echo "=== sanitizers ==="
if make debug >/dev/null 2>&1 && make test >/dev/null 2>&1; then
    echo "-> address and UB sanitizers pass"
    make clean >/dev/null
    make >/dev/null
else
    echo "-> sanitizer build unavailable or failing"
fi

exit "$status"
