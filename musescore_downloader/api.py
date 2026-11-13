from __future__ import annotations

import time
from typing import Any, Protocol

from .utils import Logger

MEDIA_TYPES = ("mp3", "midi", "img")
API_URL = "https://musescore.com/api/jmuse"


class JmuseError(RuntimeError):
    pass


class HttpResponse(Protocol):
    def json(self) -> Any: ...


class HttpLike(Protocol):
    def get(self, url: str, **kwargs: Any) -> HttpResponse: ...


class JmuseClient:
    """Minimal client for ``https://musescore.com/api/jmuse``.

    This is the Python equivalent of ``fetchApiUrl`` in
    ``src/modules/utils.js``.
    """

    def __init__(
        self,
        http: HttpLike,
        *,
        referer: str | None = None,
        logger: Logger | None = None,
        retry_delay: float = 2.0,
        max_retries: int = 8,
    ) -> None:
        self.http = http
        self.referer = referer
        self.logger = logger or Logger()
        self.retry_delay = retry_delay
        self.max_retries = max_retries

    def get_media_url(
        self,
        score_id: str,
        token: str,
        media_type: str,
        index: int = 0,
    ) -> str | None:
        if media_type not in MEDIA_TYPES:
            raise ValueError(f"Unsupported media type: {media_type}")

        headers = {
            "Authorization": token,
            "Accept": "application/json, text/plain, */*",
        }
        params = {"id": score_id, "index": index, "type": media_type}

        for attempt in range(self.max_retries + 1):
            try:
                response = self.http.get(
                    API_URL,
                    headers=headers,
                    params=params,
                    referer=self.referer,
                )
            except Exception as exc:
                if attempt >= self.max_retries:
                    self.logger.debug(f"jmuse request failed: {exc}")
                    return None
                time.sleep(self.retry_delay)
                continue

            status_code = getattr(response, "status_code", None)

            if status_code in (401, 403):
                raise JmuseError(
                    f"jmuse refused the request (HTTP {status_code}). This is "
                    "usually a Cloudflare challenge or a missing login session. "
                    "Try --browser, or pass cookies from a logged-in browser."
                )

            try:
                data = response.json()
            except Exception as exc:
                raise JmuseError(
                    "jmuse returned a non-JSON response (Cloudflare challenge, "
                    "login wall, or a MuseScore change). Try --browser or pass "
                    "session cookies."
                ) from exc

            if not isinstance(data, dict):
                raise JmuseError("Unexpected response from the jmuse API")

            status = data.get("status")

            if status == 200:
                info = data.get("info") or {}
                url = info.get("url")
                if isinstance(url, str) and url:
                    return url
                raise JmuseError("jmuse API returned status 200 without a URL")

            if status == 422:
                # MuseScore returns 422 while a file is still being prepared;
                # the extension simply retries every two seconds.
                self.logger.debug(
                    f"jmuse is not ready for {media_type} #{index} "
                    f"(attempt {attempt + 1}/{self.max_retries + 1})"
                )
                if attempt < self.max_retries:
                    time.sleep(self.retry_delay)
                    continue
                raise JmuseError(
                    f"jmuse did not prepare {media_type} #{index}: {data.get('error')}"
                )

            raise JmuseError(
                f"jmuse API error (status {status}): {data.get('error') or data}"
            )

        return None