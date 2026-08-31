#!/usr/bin/env python3
"""gen_corpus.py -- build a bigger training corpus for Oai.

The corpus that ships with Oai is about 12 KB. That is enough to watch the loss
fall and to see words appear, but it is small enough that the model memorises it
-- you will see the held-out loss turn upward after a couple of thousand steps.
Feed it more text and that stops happening.

    python3 tools/gen_corpus.py --from-files notes/*.txt --out data/mine.txt
    python3 tools/gen_corpus.py --gutenberg 1342 --out data/austen.txt
    python3 tools/gen_corpus.py --from-files book.txt --clean --lower

Then point Oai at it:

    ./bin/oai --corpus data/mine.txt --train

--gutenberg fetches a public-domain book from Project Gutenberg by its id and
strips the licence header and footer. It needs a network connection; everything
else here works offline.

Part of Oai. SPDX-License-Identifier: MIT
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from pathlib import Path

GUTENBERG_URL = "https://www.gutenberg.org/files/{id}/{id}-0.txt"
GUTENBERG_ALT = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"

START_MARKERS = ("*** START OF THE PROJECT GUTENBERG",
                 "*** START OF THIS PROJECT GUTENBERG")
END_MARKERS = ("*** END OF THE PROJECT GUTENBERG",
               "*** END OF THIS PROJECT GUTENBERG")


def strip_gutenberg(text: str) -> str:
    """Removes the licence header and footer, keeping only the book."""
    upper = text.upper()
    start = 0
    for marker in START_MARKERS:
        i = upper.find(marker)
        if i != -1:
            start = text.find("\n", i) + 1
            break
    end = len(text)
    for marker in END_MARKERS:
        i = upper.find(marker, start)
        if i != -1:
            end = i
            break
    return text[start:end].strip()


def fetch_gutenberg(book_id: int) -> str:
    from urllib.request import urlopen, Request
    from urllib.error import URLError, HTTPError

    for template in (GUTENBERG_URL, GUTENBERG_ALT):
        url = template.format(id=book_id)
        try:
            req = Request(url, headers={"User-Agent": "oai-gen-corpus/1.0"})
            with urlopen(req, timeout=45) as resp:
                raw = resp.read()
            print(f"fetched {url} ({len(raw)/1024:.0f} KB)", file=sys.stderr)
            return strip_gutenberg(raw.decode("utf-8", errors="replace"))
        except (URLError, HTTPError) as exc:
            print(f"  {url}: {exc}", file=sys.stderr)
    raise SystemExit(f"gen_corpus: could not fetch Gutenberg book {book_id}. "
                     f"Check the id and your network, or use --from-files.")


def normalise(text: str, do_clean: bool, do_lower: bool,
              keep_unicode: bool) -> str:
    if do_lower:
        text = text.lower()

    if not keep_unicode:
        # Fold accents and curly punctuation down to ASCII. A character-level
        # model spends vocabulary slots on every distinct byte, and a handful
        # of stray typographic quotes is a poor use of them.
        replacements = {
            "‘": "'", "’": "'", "“": '"', "”": '"',
            "–": "-", "—": "-", "…": "...", " ": " ",
        }
        for src, dst in replacements.items():
            text = text.replace(src, dst)
        text = unicodedata.normalize("NFKD", text)
        text = text.encode("ascii", "ignore").decode("ascii")

    if do_clean:
        text = text.replace("\r\n", "\n").replace("\r", "\n")
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r" *\n *", "\n", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = "".join(c for c in text if c == "\n" or c >= " ")
    return text.strip() + "\n"


def summarise(text: str) -> str:
    vocab = sorted(set(text))
    printable = "".join(c for c in vocab if c.isprintable())
    return (f"{len(text)} characters, {len(text.split())} words, "
            f"{len(vocab)} distinct symbols\n"
            f"vocabulary: {printable[:80]}"
            f"{'...' if len(printable) > 80 else ''}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from-files", nargs="+", type=Path,
                     help="text files to concatenate")
    src.add_argument("--gutenberg", type=int, metavar="ID",
                     help="Project Gutenberg book id to download")
    ap.add_argument("--out", type=Path, default=Path("data/corpus.generated.txt"))
    ap.add_argument("--clean", action="store_true", default=True,
                    help="collapse whitespace and drop control bytes (default)")
    ap.add_argument("--raw", dest="clean", action="store_false",
                    help="keep the text exactly as it is")
    ap.add_argument("--lower", action="store_true",
                    help="lower-case everything; halves the vocabulary and "
                         "makes a small model learn noticeably faster")
    ap.add_argument("--keep-unicode", action="store_true",
                    help="do not fold to ASCII")
    ap.add_argument("--limit", type=int, default=0, metavar="CHARS",
                    help="truncate to this many characters")
    opts = ap.parse_args()

    if opts.gutenberg:
        text = fetch_gutenberg(opts.gutenberg)
    else:
        parts = []
        for path in opts.from_files:
            if not path.exists():
                raise SystemExit(f"gen_corpus: no such file: {path}")
            parts.append(path.read_text(encoding="utf-8", errors="replace"))
            print(f"read {path} ({path.stat().st_size/1024:.0f} KB)",
                  file=sys.stderr)
        text = "\n\n".join(parts)

    text = normalise(text, opts.clean, opts.lower, opts.keep_unicode)
    if opts.limit > 0:
        text = text[:opts.limit]

    if len(text) < 1000:
        print("gen_corpus: warning: under 1000 characters, Oai will memorise "
              "this almost immediately", file=sys.stderr)

    opts.out.parent.mkdir(parents=True, exist_ok=True)
    opts.out.write_text(text, encoding="utf-8")

    print(f"\nwrote {opts.out}")
    print(summarise(text))
    print(f"\ntrain on it with:\n  ./bin/oai --corpus {opts.out} --train")
    return 0


if __name__ == "__main__":
    sys.exit(main())
