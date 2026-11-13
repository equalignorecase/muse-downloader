from __future__ import annotations

import unittest
from typing import ClassVar

from musescore_downloader.browser import BrowserHttpError, BrowserResponse


class FakeApiResponse:
    status = 200
    ok = True
    headers: ClassVar[dict[str, str]] = {"Content-Type": "application/json"}
    url = "https://musescore.com/api/jmuse"

    def text(self):
        return '{"status": 200}'

    def body(self):
        return b'{"status": 200}'

    def json(self):
        return {"status": 200}


class BrowserResponseTests(unittest.TestCase):
    def test_adapter(self) -> None:
        response = BrowserResponse(FakeApiResponse())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.ok)
        self.assertEqual(response.headers["content-type"], "application/json")
        self.assertEqual(response.json()["status"], 200)
        self.assertEqual(
            list(response.iter_content(chunk_size=4)),
            [b'{"st', b'atus', b'": 2', b'00}'],
        )

    def test_error_status(self) -> None:
        class ErrorResponse(FakeApiResponse):
            status = 403
            ok = False

        with self.assertRaises(BrowserHttpError):
            BrowserResponse(ErrorResponse()).raise_for_status()


if __name__ == "__main__":
    unittest.main()