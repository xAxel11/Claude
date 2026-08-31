#!/usr/bin/env sh
# format.sh -- run clang-format over the C sources if it is installed, and
# report anything that would change. Pass --fix to rewrite the files.
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

if ! command -v clang-format >/dev/null 2>&1; then
    echo "format.sh: clang-format is not installed; nothing to do."
    echo "  Debian/Ubuntu: sudo apt-get install clang-format"
    exit 0
fi

files="$(find src include tests -name '*.c' -o -name '*.h' | sort)"

if [ "${1:-}" = "--fix" ]; then
    # shellcheck disable=SC2086
    clang-format -i $files
    echo "formatted $(printf '%s\n' $files | wc -l) files"
else
    status=0
    for f in $files; do
        if ! clang-format "$f" | diff -q "$f" - >/dev/null; then
            echo "would reformat: $f"
            status=1
        fi
    done
    [ "$status" -eq 0 ] && echo "all files are formatted"
    exit "$status"
fi
