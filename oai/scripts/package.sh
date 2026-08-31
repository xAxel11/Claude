#!/usr/bin/env sh
# package.sh -- produce release archives in dist/.
#
#   ./scripts/package.sh          this platform
#   ./scripts/package.sh --all    this platform and Windows, plus a source zip
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

python3 tools/build_exe.py --zip --strip

if [ "${1:-}" = "--all" ]; then
    if command -v x86_64-w64-mingw32-gcc >/dev/null 2>&1; then
        python3 tools/build_exe.py --target windows --static --strip --zip
    else
        echo "package.sh: mingw-w64 not installed, skipping the Windows build"
    fi
    python3 tools/make_zip.py
fi

echo
ls -lh dist/*.zip 2>/dev/null || echo "no archives were produced"
