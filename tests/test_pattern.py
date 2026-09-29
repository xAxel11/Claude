import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import pattern  # noqa: E402


class TestPattern(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = pattern.make_pattern("1234")

    def test_every_character_has_exactly_one_unique_replacement(self):
        self.assertEqual(set(self.p), set(pattern.ALPHABET))
        self.assertEqual(sorted(self.p.values()), sorted(pattern.ALPHABET))

    def test_same_password_same_pattern(self):
        self.assertEqual(pattern.make_pattern("1234"), self.p)

    def test_different_password_different_pattern(self):
        self.assertNotEqual(pattern.make_pattern("12345"), self.p)

    def test_known_pattern(self):
        self.assertEqual(pattern.encode("hello world 2026", self.p),
                         "UHJJs fsdJW VnV1")

    def test_round_trip(self):
        text = "My password is 1234! Meet me at 7pm."
        self.assertEqual(
            pattern.decode(pattern.encode(text, self.p), self.p), text)

    def test_other_characters_unchanged(self):
        self.assertEqual(pattern.encode(" !?.,-", self.p), " !?.,-")


if __name__ == "__main__":
    unittest.main()
