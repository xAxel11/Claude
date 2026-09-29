"""Side-by-side demo: the weak "a always becomes p" cipher vs AES v2.

Run:  python demo.py
"""

import os
import random
import string
from collections import Counter

import aesv2

MESSAGE = (
    "the secret meeting is at the old bridge near the river at seven tonight "
    "bring the documents and tell nobody about the plan because the enemy is "
    "watching every street and every message that leaves the city this week"
)

# English letters from most to least common.
ENGLISH_ORDER = "etaoinshrdlcumwfgypbvkjxqz"


def substitution_encrypt(text, table):
    return "".join(table.get(ch, ch) for ch in text)


def frequency_attack(ciphertext):
    """Guess the table by matching letter frequencies to normal English."""
    counts = Counter(ch for ch in ciphertext if ch.isalpha())
    ranked = [ch for ch, _ in counts.most_common()]
    guess = dict(zip(ranked, ENGLISH_ORDER))
    return "".join(guess.get(ch, ch) for ch in ciphertext)


def main():
    rng = random.Random(7)
    letters = list(string.ascii_lowercase)
    shuffled = letters[:]
    rng.shuffle(shuffled)
    table = dict(zip(letters, shuffled))

    print("=" * 72)
    print("1) WEAK: substitution cipher (every letter always maps to the same one)")
    print("=" * 72)
    print(f"'a' always becomes '{table['a']}'")
    print("aaaaaaaa ->", substitution_encrypt("aaaaaaaa", table))
    secret = substitution_encrypt(MESSAGE, table)
    print("\nEncrypted:  ", secret[:70], "...")
    cracked = frequency_attack(secret)
    right = sum(a == b for a, b in zip(cracked, MESSAGE) if b.isalpha())
    total = sum(ch.isalpha() for ch in MESSAGE)
    print("Attack only counting letters, no key needed:")
    print("Recovered:  ", cracked[:70], "...")
    print(f"-> {right}/{total} letters already correct after ONE frequency guess.")
    print("   A human finishes the rest in minutes ('tke' -> 'the', ...).")

    print()
    print("=" * 72)
    print("2) STRONG: AES v2 (counter mode + tamper tag)")
    print("=" * 72)
    key = os.urandom(32)
    blob = aesv2.encrypt(key, b"aaaaaaaa")
    print("aaaaaaaa ->", blob[aesv2.NONCE_SIZE:-aesv2.TAG_SIZE].hex(" "))
    print("            (every 'a' became a different byte)")
    blob2 = aesv2.encrypt(key, b"aaaaaaaa")
    print("again    ->", blob2[aesv2.NONCE_SIZE:-aesv2.TAG_SIZE].hex(" "))
    print("            (same message, same key, totally different output)")

    ct = aesv2.encrypt(key, MESSAGE.encode())[aesv2.NONCE_SIZE:-aesv2.TAG_SIZE]
    counts = Counter(ct)
    print(f"\nFrequency attack on AES v2: {len(counts)} different byte values, "
          f"top count only {counts.most_common(1)[0][1]} -> nothing to latch onto.")

    tampered = bytearray(aesv2.encrypt(key, b"pay 100 dollars"))
    tampered[aesv2.NONCE_SIZE + 4] ^= 0x01
    try:
        aesv2.decrypt(key, bytes(tampered))
    except aesv2.InvalidTag as e:
        print(f"Tampering with 1 bit: rejected ({e}).")


if __name__ == "__main__":
    main()
