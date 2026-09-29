import itertools
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import aesv2  # noqa: E402


def _parity(x):
    return bin(x).count("1") & 1


def _bit_diff(a, b):
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def _flip_bit(data, bit):
    out = bytearray(data)
    out[bit // 8] ^= 1 << (bit % 8)
    return bytes(out)


class TestSBox(unittest.TestCase):
    def test_bijective(self):
        self.assertEqual(sorted(aesv2.SBOX), list(range(256)))
        for x in range(256):
            self.assertEqual(aesv2.INV_SBOX[aesv2.SBOX[x]], x)

    def test_no_fixed_points(self):
        for x in range(256):
            self.assertNotEqual(aesv2.SBOX[x], x)
            self.assertNotEqual(aesv2.SBOX[x], x ^ 0xFF)

    def test_differential_uniformity_is_4(self):
        worst = max(
            sum(1 for x in range(256) if aesv2.SBOX[x] ^ aesv2.SBOX[x ^ a] == b)
            for a in range(1, 256) for b in range(256))
        self.assertEqual(worst, 4)

    def test_linear_bias_is_2_pow_minus_4(self):
        worst = max(
            abs(sum(1 for x in range(256)
                    if _parity(x & a) == _parity(aesv2.SBOX[x] & b)) - 128)
            for a in range(256) for b in range(1, 256))
        self.assertEqual(worst, 16)


class TestDiffusion(unittest.TestCase):
    def test_mix_matrix_is_mds(self):
        def det(m):
            if len(m) == 1:
                return m[0][0]
            d = 0
            for j in range(len(m)):
                sub = [row[:j] + row[j + 1:] for row in m[1:]]
                d ^= aesv2._gf_mul(m[0][j], det(sub))
            return d
        for k in range(1, 5):
            for rows in itertools.combinations(range(4), k):
                for cols in itertools.combinations(range(4), k):
                    sub = [[aesv2.MIX[r][c] for c in cols] for r in rows]
                    self.assertNotEqual(det(sub), 0, (rows, cols))

    def test_inverse_mix(self):
        state = list(os.urandom(16))
        mixed = aesv2._mix(state, aesv2.MIX)
        self.assertEqual(aesv2._mix(mixed, aesv2.INV_MIX), state)

    def test_permutation_spreads_each_column(self):
        # Output column c must draw from four different input columns.
        for c in range(4):
            sources = {aesv2.PERM[4 * c + r] // 4 for r in range(4)}
            self.assertEqual(len(sources), 4)

    def test_full_diffusion_after_two_rounds(self):
        base = [0] * 16
        for i in range(16):
            changed = list(base)
            changed[i] = 1
            a = aesv2._round(aesv2._round(base))
            b = aesv2._round(aesv2._round(changed))
            self.assertTrue(all(x != y for x, y in zip(a, b)), i)


class TestBlockCipher(unittest.TestCase):
    KEY = bytes(range(32))

    def test_known_answers(self):
        rk = aesv2.expand_key(self.KEY)
        self.assertEqual(aesv2.encrypt_block(rk, bytes(16)).hex(),
                         "7d4b583620d674651128a0a54ee4a465")
        self.assertEqual(aesv2.encrypt_block(rk, bytes(range(16))).hex(),
                         "ef90a6b89d1fa90469fdc7ea0e40dc9a")

    def test_round_trip(self):
        for _ in range(50):
            rk = aesv2.expand_key(os.urandom(32))
            block = os.urandom(16)
            self.assertEqual(
                aesv2.decrypt_block(rk, aesv2.encrypt_block(rk, block)), block)

    def test_rejects_bad_key_size(self):
        with self.assertRaises(ValueError):
            aesv2.expand_key(bytes(16))

    def test_round_keys_distinct(self):
        rks = [bytes(k) for k in aesv2.expand_key(bytes(32))]
        self.assertEqual(len(set(rks)), len(rks))

    def test_plaintext_avalanche(self):
        rk = aesv2.expand_key(self.KEY)
        total = trials = 0
        for _ in range(40):
            p = os.urandom(16)
            c = aesv2.encrypt_block(rk, p)
            for bit in range(0, 128, 8):
                total += _bit_diff(c, aesv2.encrypt_block(rk, _flip_bit(p, bit)))
                trials += 1
        # Ideal is 64 of 128 bits (50%).
        self.assertAlmostEqual(total / trials / 128, 0.5, delta=0.02)

    def test_key_avalanche(self):
        p = bytes(16)
        total = trials = 0
        for _ in range(10):
            k = os.urandom(32)
            c = aesv2.encrypt_block(aesv2.expand_key(k), p)
            for bit in range(0, 256, 16):
                c2 = aesv2.encrypt_block(aesv2.expand_key(_flip_bit(k, bit)), p)
                total += _bit_diff(c, c2)
                trials += 1
        self.assertAlmostEqual(total / trials / 128, 0.5, delta=0.03)


class TestAEAD(unittest.TestCase):
    KEY = bytes(32)

    def test_known_answer(self):
        out = aesv2.encrypt(self.KEY, b"hello", nonce=bytes(12))
        self.assertEqual(
            out.hex(),
            "000000000000000000000000" "2b0d04bd96"
            "b66807ac7cb5328f67a1381927d685307dc2a097abd956ac9bbb0b5e1a4fa24a")

    def test_round_trip_various_lengths(self):
        for n in (0, 1, 15, 16, 17, 100, 1000):
            msg = os.urandom(n)
            self.assertEqual(aesv2.decrypt(self.KEY, aesv2.encrypt(self.KEY, msg)),
                             msg)

    def test_same_letter_encrypts_differently(self):
        ct = aesv2.encrypt(self.KEY, b"a" * 4096)[aesv2.NONCE_SIZE:-aesv2.TAG_SIZE]
        # 'a' repeated must not give a repeated output byte pattern.
        self.assertGreater(len(set(ct)), 240)

    def test_ciphertext_bytes_look_uniform(self):
        n = 256 * 64
        ct = aesv2.encrypt(self.KEY, b"a" * n)[aesv2.NONCE_SIZE:-aesv2.TAG_SIZE]
        counts = [0] * 256
        for b in ct:
            counts[b] += 1
        expected = n / 256
        chi2 = sum((c - expected) ** 2 / expected for c in counts)
        # 255 degrees of freedom: 99.9% of random data scores below ~330.
        self.assertLess(chi2, 330)

    def test_same_message_twice_differs(self):
        self.assertNotEqual(aesv2.encrypt(self.KEY, b"secret"),
                            aesv2.encrypt(self.KEY, b"secret"))

    def test_tamper_detected_everywhere(self):
        msg = aesv2.encrypt(self.KEY, b"attack at dawn")
        for i in range(len(msg)):
            with self.assertRaises(aesv2.InvalidTag):
                aesv2.decrypt(self.KEY, _flip_bit(msg, i * 8))

    def test_truncation_detected(self):
        msg = aesv2.encrypt(self.KEY, b"attack at dawn")
        for cut in (1, 10, len(msg)):
            with self.assertRaises(aesv2.InvalidTag):
                aesv2.decrypt(self.KEY, msg[:-cut])

    def test_wrong_key_rejected(self):
        msg = aesv2.encrypt(self.KEY, b"hi")
        with self.assertRaises(aesv2.InvalidTag):
            aesv2.decrypt(b"\x01" * 32, msg)

    def test_associated_data_bound(self):
        msg = aesv2.encrypt(self.KEY, b"hi", aad=b"header")
        self.assertEqual(aesv2.decrypt(self.KEY, msg, aad=b"header"), b"hi")
        with self.assertRaises(aesv2.InvalidTag):
            aesv2.decrypt(self.KEY, msg, aad=b"other")


class TestPassword(unittest.TestCase):
    def test_round_trip_and_wrong_password(self):
        blob = aesv2.encrypt_with_password("correct horse", b"top secret")
        self.assertEqual(aesv2.decrypt_with_password("correct horse", blob),
                         b"top secret")
        with self.assertRaises(aesv2.InvalidTag):
            aesv2.decrypt_with_password("wrong", blob)

    def test_salt_tamper_detected(self):
        blob = aesv2.encrypt_with_password("pw", b"x")
        with self.assertRaises(aesv2.InvalidTag):
            aesv2.decrypt_with_password("pw", _flip_bit(blob, 5 * 8))


if __name__ == "__main__":
    unittest.main()
