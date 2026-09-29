"""AES v2 Pattern: a password-generated one-to-one letter pattern.

Every letter and digit gets exactly one unique replacement, chosen by
shuffling the alphabet with the AES v2 cipher. The same password always gives
the same pattern; a different password gives a different one.

This is a substitution cipher: fun for puzzles and secret notes, but letter
counting breaks it (see demo.py). For real secrets use `aesv2.py encrypt`.

Run:  python pattern.py table  -p mypassword
      python pattern.py encode "hello world" -p mypassword
      python pattern.py decode "<encoded text>" -p mypassword
"""

import argparse
import os
import string
import sys

import aesv2

ALPHABET = string.ascii_lowercase + string.ascii_uppercase + string.digits
PATTERN_SALT = b"AESv2 pattern v1"


def _random_bytes(password):
    """Endless stream of key-dependent bytes from AES v2 in counter mode."""
    key = aesv2.password_to_key(password, PATTERN_SALT)
    round_keys = aesv2.expand_key(aesv2._derive(key, b"pattern"))
    counter = 0
    while True:
        counter += 1
        yield from aesv2.encrypt_block(round_keys, counter.to_bytes(16, "big"))


def make_pattern(password):
    """Return a dict mapping every character in ALPHABET to a unique one."""
    stream = _random_bytes(password)
    chars = list(ALPHABET)
    # Fisher-Yates shuffle with rejection sampling, so every ordering is
    # equally likely.
    for i in range(len(chars) - 1, 0, -1):
        limit = 256 - 256 % (i + 1)
        while True:
            r = next(stream)
            if r < limit:
                break
        j = r % (i + 1)
        chars[i], chars[j] = chars[j], chars[i]
    return dict(zip(ALPHABET, chars))


def encode(text, pattern):
    return "".join(pattern.get(ch, ch) for ch in text)


def decode(text, pattern):
    reverse = {v: k for k, v in pattern.items()}
    return "".join(reverse.get(ch, ch) for ch in text)


def format_table(pattern):
    lines = []
    for group in (string.ascii_lowercase, string.ascii_uppercase, string.digits):
        lines.append("  ".join(f"{ch}→{pattern[ch]}" for ch in group))
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="AES v2 one-to-one letter pattern")
    p.add_argument("cmd", choices=("table", "encode", "decode"))
    p.add_argument("text", nargs="?", help="text to encode or decode")
    p.add_argument("-p", "--password",
                   help="password (default: $AESV2_PASSWORD or prompt)")
    args = p.parse_args(argv)

    password = args.password or os.environ.get("AESV2_PASSWORD")
    if not password:
        import getpass
        password = getpass.getpass("Password: ")
    pattern = make_pattern(password)

    if args.cmd == "table":
        print(format_table(pattern))
        return 0
    text = args.text if args.text is not None else sys.stdin.read().rstrip("\n")
    print(encode(text, pattern) if args.cmd == "encode" else decode(text, pattern))
    return 0


if __name__ == "__main__":
    sys.exit(main())
