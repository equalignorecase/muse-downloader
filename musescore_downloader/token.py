from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from .utils import Logger, default_cache_dir

if TYPE_CHECKING:
    from .http_client import HttpClient

# Same patterns as src/content/tokenAlgorithm.js.
BUILD_URL_RE = re.compile(
    r"^https://musescore\.com/static/public/build/[\w/]+/\d+/\d+\.\w+\.js(?:\?.*)?$"
)
SECRET_RE = re.compile(r'\w\+\w\+\w\+"([^"\\]{1,64})"\)\.substr\(0, *4\)')
MD5_INIT_CONSTANTS = ("1732584193", "-271733879", "-1732584194", "271733878")
_HREF_RE = re.compile(r'(?:href|src)=["\']([^"\']+)["\']', re.IGNORECASE)


def build_script_urls(html: str) -> list[str]:
    """Return unique build-script URLs referenced by the page."""
    urls: list[str] = []
    seen: set[str] = set()

    for match in _HREF_RE.finditer(html or ""):
        href = match.group(1)
        if BUILD_URL_RE.match(href) and href not in seen:
            seen.add(href)
            urls.append(href)

    return urls


def extract_secret(text: str) -> str | None:
    """Find the token secret inside a MuseScore build script."""
    if not all(constant in text for constant in MD5_INIT_CONSTANTS):
        return None

    match = SECRET_RE.search(text)
    return match.group(1) if match else None


def generate_token(
    score_id: str, media_type: str, index: int | str = 0, *, secret: str
) -> str:
    """Extract the first four MD5 characters, exactly like the extension."""
    return hashlib.md5(
        f"{score_id}{media_type}{index}{secret}".encode()
    ).hexdigest()[:4]


class TokenAlgorithm:
    """Derives the ``Authorization`` token from MuseScore's build script.

    The extension stores the discovered secret in ``browser.storage.local``.
    Here it is cached in a small JSON file under the user cache directory and
    validated against the build URL of the current page, mirroring
    ``loadSecret``/``setTokenSecretToCache``.
    """

    def __init__(
        self,
        http: HttpClient,
        *,
        cache_path: str | Path | None = None,
        logger: Logger | None = None,
    ) -> None:
        self.http = http
        self.logger = logger or Logger()
        self.cache_path = (
            Path(cache_path).expanduser()
            if cache_path is not None
            else default_cache_dir() / "token_secret.json"
        )
        self.secret: str | None = None
        self.secret_url: str | None = None
        self.available = True

    def _read_cache(self) -> dict[str, str] | None:
        try:
            data = json.loads(self.cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None

        if not isinstance(data, dict):
            return None
        url = data.get("url")
        secret = data.get("secret")
        if isinstance(url, str) and isinstance(secret, str):
            return {"url": url, "secret": secret}
        return None

    def _write_cache(self, url: str, secret: str) -> None:
        try:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(
                json.dumps({"url": url, "secret": secret}), encoding="utf-8"
            )
        except OSError as exc:
            self.logger.debug(f"Could not cache token secret: {exc}")

    def load_secret(self, html: str, page_url: str) -> str | None:
        """Find and cache the signing secret for the current page."""
        if self.secret:
            return self.secret

        urls = build_script_urls(html)
        cached = self._read_cache()

        if cached and cached["url"] in urls:
            self.secret = cached["secret"]
            self.secret_url = cached["url"]
            self.logger.debug(f"Using cached token secret from {cached['url']}")
            return self.secret

        for url in urls:
            try:
                script = self.http.fetch_text(url, referer=page_url)
            except Exception as exc:
                self.logger.debug(f"Could not fetch build script {url}: {exc}")
                continue

            found = extract_secret(script)
            if found:
                self.secret = found
                self.secret_url = url
                self._write_cache(url, found)
                self.logger.debug(f"Found token secret in {url}")
                return found

        self.available = False
        self.logger.warning(
            "Could not extract the token secret from the page build scripts; "
            "falling back to browser token capture."
        )
        return None

    def generate(self, score_id: str, media_type: str, index: int | str = 0) -> str | None:
        if not self.secret:
            return None
        return generate_token(score_id, media_type, index, secret=self.secret)

    def check_observed(self, key: str, observed_token: str) -> bool:
        """Self-check a token seen in network traffic (extension parity).

        If the locally generated token differs, MuseScore has changed its
        algorithm and callers should use the scrape/browser fallback.
        """
        if not self.secret:
            return True

        parts = key.split("_")
        if len(parts) < 3:
            return True

        score_id, media_type, index = parts[0], parts[1], "_".join(parts[2:])
        if generate_token(
            score_id, media_type, index, secret=self.secret
        ) != observed_token:
            self.logger.warning(
                "Local token differs from the one used by musescore.com; "
                "using browser capture instead"
            )
            self.available = False
            return False

        return True