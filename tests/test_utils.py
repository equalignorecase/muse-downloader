from __future__ import annotations

import unittest

from musescore_downloader.utils import (
    is_musescore_url,
    is_score_url,
    normalize_score_url,
    sanitize_filename,
)


class UtilsTests(unittest.TestCase):
    def test_normalize_score_url(self) -> None:
        self.assertEqual(
            normalize_score_url("123456"), "https://musescore.com/score/123456"
        )
        self.assertEqual(
            normalize_score_url("/user/1/scores/123456"),
            "https://musescore.com/user/1/scores/123456",
        )
        self.assertEqual(
            normalize_score_url("musescore.com/score/123456"),
            "https://musescore.com/score/123456",
        )
        self.assertEqual(
            normalize_score_url("https://musescore.com/score/123456?x=1"),
            "https://musescore.com/score/123456?x=1",
        )

    def test_url_checks(self) -> None:
        self.assertTrue(is_musescore_url("https://musescore.com/score/1"))
        self.assertTrue(is_score_url("https://musescore.com/user/1/scores/2"))
        self.assertFalse(is_score_url("https://musescore.com/score/2"))

    def test_sanitize_filename(self) -> None:
        self.assertEqual(
            sanitize_filename('Composer: "Song" / Part 1?'),
            "Composer Song Part 1",
        )
        self.assertEqual(sanitize_filename("   ...   "), "score")
        self.assertEqual(len(sanitize_filename("a" * 500)), 150)


if __name__ == "__main__":
    unittest.main()