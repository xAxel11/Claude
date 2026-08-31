#!/usr/bin/env sh
# build.sh -- build Oai on any POSIX system.
#
#   ./scripts/build.sh              release build -> bin/oai
#   ./scripts/build.sh debug        -O0, symbols, address + UB sanitizers
#   ./scripts/build.sh windows      cross-compile bin/Oai.exe with mingw-w64
#   ./scripts/build.sh clean
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

mode="${1:-release}"

have() { command -v "$1" >/dev/null 2>&1; }

if ! have make; then
    echo "build.sh: make is not installed." >&2
    echo "  Debian/Ubuntu: sudo apt-get install build-essential" >&2
    echo "  Fedora:        sudo dnf install make gcc" >&2
    echo "  macOS:         xcode-select --install" >&2
    exit 1
fi

case "$mode" in
    release)
        make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
        echo
        echo "built bin/oai -- run it with ./bin/oai"
        ;;
    debug)
        make debug -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
        echo
        echo "built bin/oai with sanitizers"
        ;;
    windows)
        if ! have x86_64-w64-mingw32-gcc; then
            echo "build.sh: mingw-w64 is not installed." >&2
            echo "  Debian/Ubuntu: sudo apt-get install gcc-mingw-w64" >&2
            echo "  macOS:         brew install mingw-w64" >&2
            exit 1
        fi
        make windows
        echo
        echo "built bin/Oai.exe"
        ;;
    clean)
        make clean
        echo "cleaned"
        ;;
    *)
        echo "build.sh: unknown mode \"$mode\" (release|debug|windows|clean)" >&2
        exit 2
        ;;
esac
