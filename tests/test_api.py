from __future__ import annotations

import unittest

from musescore_downloader.api import JmuseClient, JmuseError


class FakeResponse:
    def __init__(self, data, status_code=200):
        self._data = data
        self.status_code = status_code

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


class JmuseTests(unittest.TestCase):
    def test_returns_url_on_200(self) -> None:
        http = FakeHttp([FakeResponse({"status": 200, "info": {"url": "https://x/y.mp3"}})])
        client = JmuseClient(http, referer="https://musescore.com/score/1")

        url = client.get_media_url("1", "abcd", "mp3", 0)

        self.assertEqual(url, "https://x/y.mp3")
        _, kwargs = http.calls[0]
        self.assertEqual(kwargs["params"], {"id": "1", "index": 0, "type": "mp3"})
        self.assertEqual(kwargs["headers"]["Authorization"], "abcd")

    def test_retries_on_422(self) -> None:
        http = FakeHttp(
            [
                FakeResponse({"status": 422, "error": "not ready"}),
                FakeResponse({"status": 200, "info": {"url": "https://x/y.mid"}}),
            ]
        )
        client = JmuseClient(http, retry_delay=0, max_retries=2)

        self.assertEqual(client.get_media_url("1", "abcd", "midi", 0), "https://x/y.mid")
        self.assertEqual(len(http.calls), 2)

    def test_raises_on_other_status(self) -> None:
        http = FakeHttp([FakeResponse({"status": 404, "error": "missing"})])
        client = JmuseClient(http, retry_delay=0)

        with self.assertRaises(JmuseError):
            client.get_media_url("1", "abcd", "img", 5)

    def test_fails_fast_on_403(self) -> None:
        http = FakeHttp(
            [
                FakeResponse(
                    {"error": "cloudflare"}, status_code=403
                )
            ]
        )
        client = JmuseClient(http, retry_delay=0, max_retries=3)

        with self.assertRaises(JmuseError):
            client.get_media_url("1", "abcd", "img", 1)

        self.assertEqual(len(http.calls), 1)


if __name__ == "__main__":
    unittest.main()