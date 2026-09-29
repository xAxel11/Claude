"""AES v2 -- an experimental AES-style cipher with authenticated encryption.

EDUCATIONAL PROJECT. The block cipher at the core of this file ("the AES v2
core") is a new design and has NOT been reviewed by cryptographers. Do not use
it to protect real secrets; use AES-GCM or ChaCha20-Poly1305 for that.

Layout
------
1. Core block cipher: 128-bit block, 256-bit key, 20 rounds, substitution-
   permutation network (like AES, with its own S-box, diffusion matrix and key
   schedule).
2. AEAD mode (AES v2-CTR-HMAC): the core runs in counter mode so the same byte
   encrypts differently at every position, and HMAC-SHA256 provides a
   tamper-detection tag (encrypt-then-MAC).
3. Password support via scrypt, plus a small command-line interface.
"""

import argparse
import base64
import hashlib
import hmac
import os
import sys

BLOCK_SIZE = 16
KEY_SIZE = 32
ROUNDS = 20
NONCE_SIZE = 12
TAG_SIZE = 32
SALT_SIZE = 16
MAGIC = b"AV2\x01"

# ---------------------------------------------------------------------------
# Finite field GF(2^8) modulo x^8 + x^4 + x^3 + x^2 + 1 (0x11D).
# AES uses 0x11B; a different field gives a different S-box and matrix.
# ---------------------------------------------------------------------------
_POLY = 0x11D


def _gf_mul(a, b):
    r = 0
    while b:
        if b & 1:
            r ^= a
        a <<= 1
        if a & 0x100:
            a ^= _POLY
        b >>= 1
    return r


def _gf_inv(a):
    if a == 0:
        return 0
    # a^(2^8 - 2) = a^-1 in GF(2^8)
    r, p, e = 1, a, 254
    while e:
        if e & 1:
            r = _gf_mul(r, p)
        p = _gf_mul(p, p)
        e >>= 1
    return r


def _rotl8(x, n):
    return ((x << n) | (x >> (8 - n))) & 0xFF


# ---------------------------------------------------------------------------
# S-box: field inversion followed by the affine map y ^ rotl(y,2) ^ rotl(y,5)
# ^ 0xC3. Differential uniformity 4 and max linear bias 2^-4 (both optimal for
# an 8-bit S-box, same as AES), and no fixed or opposite-fixed points. The
# tests check all of these.
# ---------------------------------------------------------------------------
SBOX = []
for _x in range(256):
    _y = _gf_inv(_x)
    SBOX.append(_y ^ _rotl8(_y, 2) ^ _rotl8(_y, 5) ^ 0xC3)
INV_SBOX = [0] * 256
for _x, _s in enumerate(SBOX):
    INV_SBOX[_s] = _x

# ---------------------------------------------------------------------------
# Diffusion: circulant MDS matrix circ(1, 1, 2, 5) over GF(2^8)/0x11D.
# Every square submatrix is invertible (branch number 5), which the tests
# verify. The inverse matrix is computed at import time.
# ---------------------------------------------------------------------------
MIX_ROW = (1, 1, 2, 5)
MIX = [list(MIX_ROW[-i:] + MIX_ROW[:-i]) for i in range(4)]


def _mat_inv(m):
    n = len(m)
    a = [row[:] + [int(i == j) for j in range(n)] for i, row in enumerate(m)]
    for col in range(n):
        piv = next(r for r in range(col, n) if a[r][col])
        a[col], a[piv] = a[piv], a[col]
        f = _gf_inv(a[col][col])
        a[col] = [_gf_mul(v, f) for v in a[col]]
        for r in range(n):
            if r != col and a[r][col]:
                g = a[r][col]
                a[r] = [v ^ _gf_mul(g, w) for v, w in zip(a[r], a[col])]
    return [row[n:] for row in a]


INV_MIX = _mat_inv(MIX)

# Multiplication tables for every coefficient used by the two matrices.
_MUL = {c: [_gf_mul(c, x) for x in range(256)]
        for c in {v for row in MIX + INV_MIX for v in row}}

# ---------------------------------------------------------------------------
# Byte permutation. The state is 4x4 bytes, column-major (byte i sits in
# row i % 4, column i // 4). Row r is rotated left by (4 - r) % 4 columns: the
# mirror image of AES ShiftRows. It keeps the property that each column's
# bytes land in four different columns, so any active byte reaches all 16
# bytes within two rounds.
# ---------------------------------------------------------------------------
PERM = [0] * 16
for _c in range(4):
    for _r in range(4):
        PERM[4 * _c + _r] = 4 * ((_c + (4 - _r) % 4) % 4) + _r
INV_PERM = [0] * 16
for _i, _p in enumerate(PERM):
    INV_PERM[_p] = _i


def _mix(state, m):
    out = [0] * 16
    for c in range(4):
        col = state[4 * c:4 * c + 4]
        for r in range(4):
            row = m[r]
            out[4 * c + r] = (_MUL[row[0]][col[0]] ^ _MUL[row[1]][col[1]]
                              ^ _MUL[row[2]][col[2]] ^ _MUL[row[3]][col[3]])
    return out


def _round(state):
    """One keyless round: substitute, permute, mix."""
    state = [SBOX[b] for b in state]
    state = [state[PERM[i]] for i in range(16)]
    return _mix(state, MIX)


# ---------------------------------------------------------------------------
# Key schedule: a 4-round Feistel network over the two 128-bit key halves,
# using the cipher's own round function and per-round constants. Every round
# key depends non-linearly on all 256 key bits (unlike AES-256, whose
# schedule is almost linear and has related-key weaknesses).
# ---------------------------------------------------------------------------
_RC = [[SBOX[(16 * i + j) & 0xFF] ^ i for j in range(16)]
       for i in range(ROUNDS + 1)]


def expand_key(key):
    if len(key) != KEY_SIZE:
        raise ValueError("AES v2 key must be 32 bytes")
    left, right = list(key[:16]), list(key[16:])
    round_keys = []
    for i in range(ROUNDS + 1):
        t = [b ^ c for b, c in zip(right, _RC[i])]
        t = _round(_round(t))
        left, right = right, [a ^ b for a, b in zip(left, t)]
        round_keys.append(right)
    return round_keys


def encrypt_block(round_keys, block):
    s = [b ^ k for b, k in zip(block, round_keys[0])]
    for i in range(1, ROUNDS + 1):
        s = _round(s)
        s = [b ^ k for b, k in zip(s, round_keys[i])]
    return bytes(s)


def decrypt_block(round_keys, block):
    s = list(block)
    for i in range(ROUNDS, 0, -1):
        s = [b ^ k for b, k in zip(s, round_keys[i])]
        s = _mix(s, INV_MIX)
        s = [s[INV_PERM[i2]] for i2 in range(16)]
        s = [INV_SBOX[b] for b in s]
    return bytes(b ^ k for b, k in zip(s, round_keys[0]))


# ---------------------------------------------------------------------------
# AEAD mode: counter-mode encryption + HMAC-SHA256 tag (encrypt-then-MAC).
# Separate encryption and MAC keys are derived from the master key.
# ---------------------------------------------------------------------------
class InvalidTag(Exception):
    """Raised when a message was tampered with or the key is wrong."""


def _derive(key, label):
    return hmac.new(key, b"AESv2 " + label, hashlib.sha256).digest()


def _keystream_xor(round_keys, nonce, data):
    out = bytearray(len(data))
    for block_index in range((len(data) + BLOCK_SIZE - 1) // BLOCK_SIZE):
        counter = block_index + 1
        if counter >= 1 << 32:
            raise ValueError("message too long")
        ks = encrypt_block(round_keys, nonce + counter.to_bytes(4, "big"))
        start = block_index * BLOCK_SIZE
        chunk = data[start:start + BLOCK_SIZE]
        out[start:start + len(chunk)] = bytes(a ^ b for a, b in zip(chunk, ks))
    return bytes(out)


def _tag(mac_key, nonce, aad, ciphertext):
    mac = hmac.new(mac_key, digestmod=hashlib.sha256)
    for part in (MAGIC, nonce, aad, ciphertext):
        mac.update(len(part).to_bytes(8, "big"))
        mac.update(part)
    return mac.digest()


def encrypt(key, plaintext, aad=b"", nonce=None):
    """Encrypt and authenticate. Returns nonce || ciphertext || tag."""
    if nonce is None:
        nonce = os.urandom(NONCE_SIZE)
    if len(nonce) != NONCE_SIZE:
        raise ValueError("nonce must be 12 bytes")
    round_keys = expand_key(_derive(key, b"enc"))
    ciphertext = _keystream_xor(round_keys, nonce, plaintext)
    return nonce + ciphertext + _tag(_derive(key, b"mac"), nonce, aad, ciphertext)


def decrypt(key, message, aad=b""):
    """Verify and decrypt. Raises InvalidTag if anything was changed."""
    if len(message) < NONCE_SIZE + TAG_SIZE:
        raise InvalidTag("message too short")
    nonce = message[:NONCE_SIZE]
    ciphertext = message[NONCE_SIZE:-TAG_SIZE]
    tag = message[-TAG_SIZE:]
    expected = _tag(_derive(key, b"mac"), nonce, aad, ciphertext)
    if not hmac.compare_digest(tag, expected):
        raise InvalidTag("authentication failed: wrong key or tampered data")
    return _keystream_xor(expand_key(_derive(key, b"enc")), nonce, ciphertext)


# ---------------------------------------------------------------------------
# Passwords: scrypt stretches a password into a 256-bit key.
# ---------------------------------------------------------------------------
def password_to_key(password, salt):
    return hashlib.scrypt(password.encode(), salt=salt, n=2 ** 15, r=8, p=1,
                          maxmem=64 * 1024 * 1024, dklen=KEY_SIZE)


def encrypt_with_password(password, plaintext):
    salt = os.urandom(SALT_SIZE)
    return MAGIC + salt + encrypt(password_to_key(password, salt), plaintext,
                                  aad=MAGIC + salt)


def decrypt_with_password(password, blob):
    if blob[:4] != MAGIC:
        raise InvalidTag("not an AES v2 message")
    salt = blob[4:4 + SALT_SIZE]
    return decrypt(password_to_key(password, salt), blob[4 + SALT_SIZE:],
                   aad=MAGIC + salt)


# ---------------------------------------------------------------------------
# Command-line interface
# ---------------------------------------------------------------------------
def main(argv=None):
    p = argparse.ArgumentParser(description="AES v2 (experimental) encryption")
    sub = p.add_subparsers(dest="cmd", required=True)
    for name in ("encrypt", "decrypt"):
        sp = sub.add_parser(name)
        sp.add_argument("text", nargs="?",
                        help="text (encrypt) or base64 (decrypt); omit to use -i")
        sp.add_argument("-i", "--input", help="read from file instead")
        sp.add_argument("-o", "--output", help="write to file instead of stdout")
        sp.add_argument("-p", "--password",
                        help="password (default: $AESV2_PASSWORD or prompt)")
    args = p.parse_args(argv)

    password = args.password or os.environ.get("AESV2_PASSWORD")
    if not password:
        import getpass
        password = getpass.getpass("Password: ")

    if args.input:
        with open(args.input, "rb") as f:
            data = f.read()
    elif args.text is not None:
        data = args.text.encode()
    else:
        data = sys.stdin.buffer.read()

    try:
        if args.cmd == "encrypt":
            result = encrypt_with_password(password, data)
            if not args.output:
                result = base64.b64encode(result) + b"\n"
        else:
            if not args.input:
                data = base64.b64decode(data)
            result = decrypt_with_password(password, data)
    except InvalidTag as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    if args.output:
        with open(args.output, "wb") as f:
            f.write(result)
    else:
        sys.stdout.buffer.write(result)
        if args.cmd == "decrypt":
            sys.stdout.buffer.write(b"\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
