#!/usr/bin/env sh
# install.sh -- build Oai and copy it onto your PATH.
#
#   ./scripts/install.sh                 -> /usr/local/bin (may need sudo)
#   PREFIX=~/.local ./scripts/install.sh -> ~/.local/bin
#
# Part of Oai. SPDX-License-Identifier: MIT
set -eu
cd "$(dirname "$0")/.."

PREFIX="${PREFIX:-/usr/local}"
bindir="$PREFIX/bin"
datadir="$PREFIX/share/oai"

make -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"

mkdir -p "$bindir" "$datadir"
cp bin/oai "$bindir/oai"
chmod 755 "$bindir/oai"
cp data/corpus.txt "$datadir/corpus.txt"

echo "installed $bindir/oai"
echo "corpus at $datadir/corpus.txt"
echo
echo "Run it with:  oai --corpus $datadir/corpus.txt"
case ":$PATH:" in
    *":$bindir:"*) ;;
    *) echo
       echo "note: $bindir is not on your PATH." ;;
esac
