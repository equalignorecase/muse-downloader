from __future__ import annotations

import unittest

from musescore_downloader.score import parse_score

LD_JSON_HTML = """<!doctype html>
<html>
<head>
  <meta property="og:title" content="Meta Title">
  <meta property="musescore:composer" content="Meta Composer">
  <meta property="al:android:url" content="musescore://scores/123456">
  <link rel="preload" href="https://musescore.com/static/public/build/app/12/34.main.js" as="script">
  <link type="image/svg+xml" href="https://cdn.example/score_0.svg">
  <script type="application/ld+json">
  {
    "@type": "MusicComposition",
    "name": "Fur Elise",
    "composer": {"@type": "Person", "name": "Ludwig van Beethoven"},
    "url": "https://musescore.com/user/1/scores/123456",
    "thumbnailUrl": "https://cdn.example/score_0.png",
    "numberOfPages": 4
  }
  </script>
</head>
<body><p>1 of 4 pages</p></body>
</html>
"""


class ScoreParsingTests(unittest.TestCase):
    def test_parses_ld_json_metadata(self) -> None:
        score = parse_score(LD_JSON_HTML, "https://musescore.com/user/1/scores/123456")

        self.assertEqual(score.score_id, "123456")
        self.assertEqual(score.name, "Fur Elise")
        self.assertEqual(score.composer, "Ludwig van Beethoven")
        self.assertEqual(score.pages, 4)
        self.assertEqual(score.first_image, "https://cdn.example/score_0.svg")
        self.assertTrue(score.is_valid)
        self.assertEqual(score.base_filename, "Ludwig van Beethoven - Fur Elise")

    def test_meta_fallbacks(self) -> None:
        html = """
        <html><head>
          <meta property="og:title" content="Moonlight Sonata">
          <meta property="musescore:composer" content="Beethoven">
          <meta property="og:url" content="https://musescore.com/score/987654">
          <link type="image/png" href="https://cdn.example/score_0.png">
        </head><body>
          <h3>Pages</h3></th><td><div class="pages">7</div>
        </body></html>
        """
        score = parse_score(html, "https://musescore.com/score/987654")

        self.assertEqual(score.name, "Moonlight Sonata")
        self.assertEqual(score.composer, "Beethoven")
        self.assertEqual(score.score_id, "987654")
        self.assertEqual(score.pages, 7)
        self.assertEqual(score.first_image, "https://cdn.example/score_0.png")

    def test_page_count_from_json_and_images(self) -> None:
        html = '{"pageCount": 12} <img src="score_0.png"><img src="score_1.png">'
        score = parse_score(html, "https://musescore.com/score/1")
        self.assertEqual(score.pages, 12)

        html = '<img src="score_0.png"><img src="score_1.png"><img src="score_2.png">'
        score = parse_score(html, "https://musescore.com/score/1")
        self.assertEqual(score.pages, 3)

    def test_page_count_from_escaped_metadata(self) -> None:
        html = (
            '<div data-score="{&quot;pages&quot;:4,'
            '&quot;dimensions&quot;:&quot;210x297&quot;}"></div>'
            '<img src="score_0.svg">'
        )
        score = parse_score(html, "https://musescore.com/score/1")
        self.assertEqual(score.pages, 4)

    def test_invalid_score_id(self) -> None:
        score = parse_score("<html></html>", "https://musescore.com/")
        self.assertFalse(score.is_valid)


if __name__ == "__main__":
    unittest.main()