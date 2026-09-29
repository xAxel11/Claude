# AES v2 (experimental)

A new AES-style cipher with authenticated encryption, written in plain Python (no dependencies).

> ⚠️ **Educational project.** The core block cipher is a new design that no cryptographers have reviewed. Real ciphers earn trust only after years of public attack attempts. For real secrets, use AES-GCM or ChaCha20-Poly1305.

## Quick start

```bash
python aesv2.py encrypt "hello world" -p mypassword     # prints base64
python aesv2.py decrypt "<base64 output>" -p mypassword

python aesv2.py encrypt -i photo.jpg -o photo.jpg.av2   # files
python aesv2.py decrypt -i photo.jpg.av2 -o photo.jpg

python demo.py                       # weak substitution cipher vs AES v2
python -m unittest discover tests    # 25 tests
```

In Python:

```python
import aesv2, os
key = os.urandom(32)
msg = aesv2.encrypt(key, b"secret", aad=b"optional header")
aesv2.decrypt(key, msg, aad=b"optional header")   # raises InvalidTag if tampered
```

## Why not just "a → p"?

A plain substitution cipher always turns the same letter into the same output. Counting letters breaks it (see `demo.py`). AES v2 instead:

1. Uses a **256-bit key** and a fresh random **nonce** for each message.
2. Runs its block cipher in **counter mode**: a different random-looking keystream at every position, so `aaaa` becomes four unrelated bytes.
3. Adds an **HMAC-SHA256 tag**, so any change to the message (even 1 bit) is rejected.

## Design of the core block cipher

| Part | AES-256 | AES v2 |
|---|---|---|
| Block / key | 128 / 256 bits | 128 / 256 bits |
| Rounds | 14 | **20** (extra safety margin) |
| Field | GF(2⁸) mod 0x11B | GF(2⁸) mod **0x11D** |
| S-box | inverse + affine | inverse + new affine `y⊕rotl(y,2)⊕rotl(y,5)⊕0xC3` |
| Byte permutation | ShiftRows | mirrored ShiftRows |
| Mixing | circ(2,3,1,1) MDS | circ(**1,1,2,5**) MDS |
| Key schedule | mostly linear | **non-linear Feistel** that reuses the round function |

The tests check these properties automatically:

- **S-box:** a bijection with no fixed points, differential uniformity 4 and max linear bias 2⁻⁴. These are optimal, the same level as AES.
- **Mixing matrix:** truly MDS (branch number 5).
- **Diffusion:** one changed byte changes all 16 bytes after 2 rounds.
- **Avalanche:** flipping one plaintext or key bit flips about 50% of output bits.
- **Output randomness:** passes a chi-square uniformity check.
- **Tamper detection:** flipping any bit, truncating, or using the wrong key or header is rejected.

## Known limitations

- **Not reviewed by cryptographers:** the design has had no outside review, which is the most important limitation.
- **Timing leaks:** Python table lookups are not constant-time, so an attacker who can measure timing on the same machine could learn about the key.
- **Speed:** slow (about 100 KB/s), since it is pure Python.
- **Nonce reuse:** reusing a nonce with the same key breaks confidentiality. `encrypt()` picks a random nonce for you, so don't pass your own unless you know why.
