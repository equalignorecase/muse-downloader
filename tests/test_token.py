from __future__ import annotations

import hashlib
import unittest

from musescore_downloader.token import (
    build_script_urls,
    extract_secret,
    generate_token,
)

SECRET_SCRIPT = """
/* 1732584193 -271733879 -1732584194 271733878 */
var signature = a+b+c+"s3cr3t").substr(0, 4);
"""


class TokenTests(unittest.TestCase):
    def test_extracts_secret(self) -> None:
        self.assertEqual(extract_secret(SECRET_SCRIPT), "s3cr3t")
        self.assertIsNone(extract_secret("no constants here a+b+c+\"x\")"))

    def test_generates_md5_prefix(self) -> None:
        expected = hashlib.md5(b"123456img0s3cr3t").hexdigest()[:4]
        self.assertEqual(
            generate_token("123456", "img", 0, secret="s3cr3t"), expected
        )
        self.assertEqual(len(generate_token("123", "mp3", 1, secret="x")), 4)

    def test_build_script_urls(self) -> None:
        html = """
        <link rel="preload" href="https://musescore.com/static/public/build/app/12/34.main.js">
        <link rel="preload" href="https://musescore.com/static/public/build/app/12/34.main.js">
        <link rel="preload" href="https://musescore.com/static/public/build/app/12/34.main.js?v=2">
        <link rel="stylesheet" href="https://example.com/style.css">
        <script src="https://musescore.com/static/public/build/app/12/34.other.js"></script>
        """

        urls = build_script_urls(html)
        self.assertEqual(
            urls,
            [
                "https://musescore.com/static/public/build/app/12/34.main.js",
                "https://musescore.com/static/public/build/app/12/34.main.js?v=2",
                "https://musescore.com/static/public/build/app/12/34.other.js",
            ],
        )


if __name__ == "__main__":
    unittest.main()