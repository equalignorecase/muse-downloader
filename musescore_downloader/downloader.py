from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Any

from .api import JmuseClient, JmuseError
from .browser import BrowserUnavailable, capture, fetch_html
from .http_client import HttpClient
from .pdf import build_pdf
from .score import ScoreInfo, parse_score
from .token import TokenAlgorithm
from .utils import (
    Logger,
    format_size,
    normalize_score_url,
    resolve_output_path,
    sanitize_filename,
)

_HTML_START_RE = re.compile(rb"^\s*(?:<!doctype html|<html)", re.IGNORECASE)


def _header(response: Any, name: str) -> str:
    """Case-insensitive header lookup for requests and curl_cffi responses."""
    headers = getattr(response, "headers", {}) or {}
    value = headers.get(name)
    if value is None:
        lowered = name.lower()
        for key, item in headers.items():
            if str(key).lower() == lowered:
                value = item
                break
    return str(value or "").lower()


class DownloadError(RuntimeError):
    pass


class MuseScoreDownloader:
    """Python equivalent of the extension's content script."""

    def __init__(
        self,
        url: str,
        *,
        http: HttpClient | None = None,
        logger: Logger | None = None,
        use_browser: bool = False,
        headless: bool = True,
        scrape_wait: float = 10.0,
        max_pages: int = 100,
        timeout: float = 30.0,
    ) -> None:
        self.url = normalize_score_url(url)
        self.logger = logger or Logger()
        self.http = http or HttpClient(timeout=timeout)
        self.use_browser = use_browser
        self.headless = headless
        self.scrape_wait = scrape_wait
        self.max_pages = max_pages

        self.score: ScoreInfo | None = None
        self.html: str | None = None
        self.token = TokenAlgorithm(self.http, logger=self.logger)
        self.jmuse = JmuseClient(self.http, referer=self.url, logger=self.logger)

        self._scraped_tokens: dict[str, str] = {}
        self._scraped_media: dict[str, str] = {}
        self._scraped_images: dict[int, str] = {}
        self._scrape_attempted = False

    # ------------------------------------------------------------------ page

    def load(self) -> ScoreInfo:
        if self.score is not None:
            return self.score

        python_error: Exception | None = None

        if hasattr(self.http, "fetch_page_html"):
            self.logger.status("Loading score page in Chromium")
            try:
                self.html = self.http.fetch_page_html(self.url)
            except Exception as exc:
                raise DownloadError(f"Could not load the score page: {exc}") from exc
        else:
            try:
                response = self.http.get(self.url)
                response.raise_for_status()
                self.html = response.text
            except Exception as exc:
                python_error = exc
                self.logger.debug(f"HTTP load failed: {exc}")

            if self.html is None:
                if not self.use_browser:
                    raise DownloadError(
                        "Could not load the score page. musescore.com usually "
                        "answers plain HTTP clients with a Cloudflare challenge; "
                        "retry with --browser.\n"
                        f"Reason: {python_error}"
                    )
                self.logger.status("Loading score page in Chromium")
                try:
                    self.html = fetch_html(
                        self.url,
                        headless=self.headless,
                        cookie_header=self.http.cookie_header,
                        logger=self.logger,
                    )
                except BrowserUnavailable as exc:
                    raise DownloadError(str(exc)) from exc

        self.score = parse_score(self.html, self.url)

        if not self.score.is_valid:
            self.logger.warning(
                "Could not detect a numeric score id; downloading may fail"
            )
        else:
            self.logger.debug(
                f"Score {self.score.score_id}: {self.score.base_filename!r}, "
                f"{self.score.pages} page(s)"
            )

        return self.score

    # ------------------------------------------------------------- media urls

    def get_media_url(self, media_type: str, index: int = 0) -> str:
        score = self.load()

        if not score.score_id:
            raise DownloadError("Could not determine the score id")

        if self.token.available:
            try:
                secret = self.token.load_secret(self.html or "", self.url)
                if secret:
                    token = self.token.generate(score.score_id, media_type, index)
                    if token:
                        media_url = self.jmuse.get_media_url(
                            score.score_id, token, media_type, index
                        )
                        if media_url:
                            return media_url
            except Exception as exc:
                self.logger.debug(f"Token algorithm failed: {exc}")

            self.token.available = False

        return self._scrape_media_url(media_type, index)

    def _scrape_media_url(self, media_type: str, index: int) -> str:
        key = f"{self.score.score_id}_{media_type}_{index}"

        for attempt in range(2):
            media_url = self._scraped_media.get(key)
            if media_url:
                return media_url

            if media_type == "img" and index in self._scraped_images:
                return self._scraped_images[index]

            token = self._scraped_tokens.get(key)
            if token:
                try:
                    url = self.jmuse.get_media_url(
                        self.score.score_id, token, media_type, index
                    )
                    if url:
                        return url
                except JmuseError as exc:
                    self.logger.debug(f"Captured token did not work: {exc}")

            if attempt == 0:
                self._capture_tokens({key}, media_type)
            else:
                time.sleep(0.5)

        raise DownloadError(
            f"Could not obtain a download link for {media_type} #{index}. "
            "The score may be official/paywalled, or your session cookies are "
            "missing (try --cookie-file or --cookies-from-browser)."
        )

    def _capture_tokens(
        self, expected_keys: set[str], media_type: str | None = None
    ) -> None:
        if self._scrape_attempted:
            return

        self._scrape_attempted = True
        self.logger.status("Capturing download tokens in Chromium")

        try:
            if hasattr(self.http, "capture_tokens"):
                expected = set(expected_keys)
                score = self.load()
                if media_type == "img" and score.pages > 0:
                    expected.update(
                        f"{score.score_id}_img_{index}"
                        for index in range(score.pages)
                    )

                native_result = self.http.capture_tokens(
                    self.url,
                    expected_keys=expected,
                    wait=self.scrape_wait,
                    capture_html=self.html is None,
                )
                tokens = native_result.tokens
                media_urls = native_result.media_urls
                images = native_result.images
                if native_result.html and not self.html:
                    self.html = native_result.html
                    if self.score is None:
                        self.score = parse_score(self.html, self.url)
            else:
                result = capture(
                    self.url,
                    headless=self.headless,
                    cookie_header=self.http.cookie_header,
                    wait=self.scrape_wait,
                    expected_keys=expected_keys,
                    logger=self.logger,
                )
                tokens = result.tokens
                media_urls = result.media_urls
                images = result.images
                if result.html and not self.html:
                    self.html = result.html
                    if self.score is None:
                        self.score = parse_score(self.html, self.url)
        except BrowserUnavailable as exc:
            raise DownloadError(str(exc)) from exc

        self._scraped_tokens.update(tokens)
        self._scraped_media.update(media_urls)
        self._scraped_images.update(images)
        self.logger.debug(
            f"Captured {len(tokens)} token(s), {len(media_urls)} media URL(s), "
            f"{len(images)} page image(s)"
        )

        for key, observed_token in tokens.items():
            self.token.check_observed(key, observed_token)

    # --------------------------------------------------------------- downloads

    def _default_name(self, suffix: str) -> str:
        score = self.load()
        return sanitize_filename(score.base_filename) + suffix

    def download_audio(self, output: str | None = None) -> Path:
        return self._download_media("mp3", "audio", output, ".mp3")

    def download_midi(self, output: str | None = None) -> Path:
        return self._download_media("midi", "midi", output, ".mid")

    def _download_media(
        self, media_type: str, label: str, output: str | None, suffix: str
    ) -> Path:
        self.load()
        self.logger.status(f"Retrieving {label} link")

        url = self.get_media_url(media_type)
        destination = resolve_output_path(
            output, self._default_name(suffix), suffix
        )

        self.logger.status(f"Downloading {label}")
        self._download_file(url, destination)
        self.logger.info(f"{label.capitalize()} saved to {destination}")
        return destination

    def download_sheet(
        self,
        output: str | None = None,
        *,
        page_count: int | None = None,
        allow_partial: bool = False,
    ) -> Path:
        pages = self.fetch_sheet_pages(
            page_count=page_count, allow_partial=allow_partial
        )
        score = self.load()
        destination = resolve_output_path(output, self._default_name(".pdf"), ".pdf")

        self.logger.status("Generating PDF file")
        build_pdf(
            pages,
            destination,
            title=score.name,
            author=score.composer,
            logger=self.logger,
        )
        self.logger.info(f"PDF saved to {destination}")
        return destination

    def fetch_sheet_pages(
        self, *, page_count: int | None = None, allow_partial: bool = False
    ) -> list[bytes]:
        score = self.load()
        self.logger.status("Retrieving media links")

        count = page_count or score.pages
        if count <= 0:
            self.logger.warning(
                "Could not detect the page count; probing until downloads stop"
            )

        pages: list[bytes] = []

        self.logger.status(
            f"Retrieving page 1/{count if count > 0 else '?'}"
        )
        first_page = self._fetch_first_page()
        if first_page:
            pages.append(first_page)

        index = 1
        consecutive_failures = 0
        limit = count if count > 0 else self.max_pages

        while index < limit:
            self.logger.status(
                f"Retrieving page {index + 1}/{count if count > 0 else '?'}"
            )
            try:
                media_url = self.get_media_url("img", index)
            except DownloadError as exc:
                self.logger.debug(f"Page {index + 1} failed: {exc}")
                media_url = None

            image = self._fetch_image(media_url) if media_url else b""

            if image:
                pages.append(image)
                consecutive_failures = 0
            else:
                consecutive_failures += 1
                if count <= 0 and consecutive_failures >= 2:
                    self.logger.debug("No more pages found")
                    break
                self.logger.warning(f"Skipping page {index + 1} (no image)")

            index += 1

        if count > 0 and len(pages) < count and not allow_partial:
            raise DownloadError(
                f"Only {len(pages)} of {count} pages could be downloaded. "
                "The remaining pages may need a logged-in session; retry with "
                "--cookie-file/--cookies-from-browser, or pass --allow-partial "
                "to save the pages that were retrieved."
            )

        return pages

    def _fetch_first_page(self) -> bytes:
        score = self.load()

        candidates: list[str] = []
        if score.first_image:
            candidates.append(score.first_image)

        for candidate in candidates:
            try:
                data = self._fetch_image(candidate)
            except Exception as exc:
                self.logger.debug(f"First page fetch failed: {exc}")
                data = b""

            if data and not _HTML_START_RE.match(data):
                return data

            if ".svg" in candidate:
                png_url = candidate.replace(".svg", ".png")
                try:
                    data = self._fetch_image(png_url)
                    if data and not _HTML_START_RE.match(data):
                        return data
                except Exception as exc:
                    self.logger.debug(f"PNG fallback failed: {exc}")

        # Last resort: the jmuse API also serves page index 0.
        try:
            return self._fetch_image(self.get_media_url("img", 0))
        except Exception as exc:
            self.logger.debug(f"API first page failed: {exc}")
            return b""

    def _fetch_image(self, url: str) -> bytes:
        response = self.http.get(url, headers={"Accept": "*/*"}, referer=self.url)
        if not response.ok:
            self.logger.debug(f"Image request failed with HTTP {response.status_code}")
            return b""

        content_type = _header(response, "Content-Type")

        if "application/xml" in content_type:
            return b""

        if "text/html" in content_type:
            return b""

        return response.content or b""

    def _download_file(self, url: str, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary = destination.with_name(destination.name + ".part")

        try:
            with self.http.get(
                url, headers={"Accept": "*/*"}, referer=self.url, stream=True
            ) as response:
                if not response.ok:
                    raise DownloadError(
                        f"Download failed with HTTP {response.status_code}"
                    )

                content_type = _header(response, "Content-Type")
                if "text/html" in content_type:
                    raise DownloadError(
                        "The server returned an HTML page instead of a file. "
                        "Log in with --cookie-file/--cookies-from-browser, or the "
                        "score may be restricted."
                    )

                downloaded = 0
                with temporary.open("wb") as handle:
                    for chunk in response.iter_content(chunk_size=64 * 1024):
                        if chunk:
                            handle.write(chunk)
                            downloaded += len(chunk)

            os.replace(temporary, destination)
            self.logger.debug(
                f"Downloaded {format_size(downloaded)} to {destination}"
            )
        except Exception:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
            raise

    # -------------------------------------------------------------------- all

    def download_all(
        self,
        output_dir: str | None = None,
        *,
        page_count: int | None = None,
        allow_partial: bool = False,
    ) -> dict[str, Any]:
        """Download sheet, audio and MIDI; failures don't stop the others."""
        directory = Path(output_dir).expanduser() if output_dir else Path.cwd()
        directory.mkdir(parents=True, exist_ok=True)

        results: dict[str, Any] = {}
        for label, operation in (
            (
                "sheet",
                lambda: self.download_sheet(
                    str(directory),
                    page_count=page_count,
                    allow_partial=allow_partial,
                ),
            ),
            ("audio", lambda: self.download_audio(str(directory))),
            ("midi", lambda: self.download_midi(str(directory))),
        ):
            try:
                results[label] = operation()
            except Exception as exc:
                self.logger.error(f"{label} failed: {exc}")
                results[label] = exc

        return results