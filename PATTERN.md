# AES v2 Pattern

Turn a password into a **secret letter pattern**. Every letter and digit gets **exactly one unique replacement**, and no two characters share one, so every message can be decoded again.

```
password "1234"  →  a→h  b→N  c→0  d→W  e→H  ...  1→r  2→V  3→D  4→Z  ...

hello world 2026      →  UHJJs fsdJW VnV1
my password is 1234   →  Ez ChBBfsdW YB rVDZ
```

## How to use it

Show your full pattern table:
```bash
python pattern.py table -p 1234
```

Encode a message:
```bash
python pattern.py encode "my password is 1234" -p 1234
# Ez ChBBfsdW YB rVDZ
```

Decode it (only works with the same password):
```bash
python pattern.py decode "Ez ChBBfsdW YB rVDZ" -p 1234
# my password is 1234
```

If you leave out `-p`, it asks for the password without showing it on screen.

## How the pattern is made

1. The password is stretched into a 256-bit key with **scrypt**, which makes guessing passwords slow.
2. The **AES v2 cipher** turns that key into a stream of random-looking bytes.
3. Those bytes **shuffle the 62 characters** (`a–z`, `A–Z`, `0–9`) using a fair Fisher-Yates shuffle, where every possible pattern is equally likely.
4. Each character is paired with its shuffled partner, which makes the pattern one-to-one.

The results follow these rules:
- Same password gives the same pattern every time.
- A different password gives a completely different pattern.
- Spaces and punctuation (`! ? . , -`) stay the same.
- There are 62! ≈ 3 × 10⁸⁵ possible patterns.

## Is it secure?

**For fun, puzzles and notes between friends: yes. For real secrets: no.**

Because each letter always becomes the same symbol, an attacker can count which symbols appear most often (`e`, `t` and `a` are the most common English letters) and work backwards. This is **frequency analysis**. It breaks any pattern like this without the password, no matter how strong the password is. Run `python demo.py` to watch it happen.

For real secrets use full AES v2 encryption, where the same letter becomes something different every time:
```bash
python aesv2.py encrypt "my password is 1234" -p a-long-strong-passphrase
```

| | `pattern.py` | `aesv2.py encrypt` |
|---|---|---|
| Same letter → same output | ✅ always | ❌ never |
| Output readable/typeable | ✅ letters & digits | base64 |
| Resists frequency analysis | ❌ | ✅ |
| Detects tampering | ❌ | ✅ |

## Tests

```bash
python -m unittest tests.test_pattern
```

The tests check that every character has exactly one unique replacement, that the same password gives the same pattern, that different passwords give different patterns, and that encoding round-trips.
