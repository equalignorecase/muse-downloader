from __future__ import annotations

import base64
import json as json_module
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

from .http_client import (
    DEFAULT_USER_AGENT,
    CurlResponse,
    curl_requests,
    resolve_cookies,
)
from .utils import Logger

EXPAND_PAGES_JS = """
() => {
  const el = document.querySelector('#jmuse-scroller-component');
  const container = el && el.parentElement;
  if (container) {
    document.body.style.overflow = 'hidden';
    container.style.height = '200000px';
    return true;
  }
  return false;
}
"""

SCROLL_JS = """
() => {
  window.scrollTo(0, document.body.scrollHeight);
  window.scrollTo(0, 0);
  return document.body.scrollHeight;
}
"""

MIDI_CLICK_JS = """
() => {
  const path = document.querySelector('path[d^="M2 3.875V20.125C2 0.6223"]');
  const button = path && path.parentElement && path.parentElement.parentElement;
  if (button) { button.click(); return true; }
  return false;
}
"""

TOGGLE_PLAY_CLICK_JS = """
() => {
  const button = document.querySelector('button[title="Toggle Play"]');
  if (button) { button.click(); return true; }
  return false;
}
"""

PAGE_FETCH_JS = """
async ({url, headers}) => {
  const response = await fetch(url, {
    headers,
    credentials: 'include',
    redirect: 'follow'
  });
  const buffer = await response.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = '';
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return {
    status: response.status,
    ok: response.ok,
    url: response.url,
    headers: Object.fromEntries(response.headers.entries()),
    body: btoa(binary)
  };
}
"""

DOM_IMAGES_JS = """
() => {
  const urls = [];
  for (const img of document.querySelectorAll('img')) {
    const url = img.currentSrc || img.src || img.getAttribute('data-src');
    if (url) urls.push(url);
  }
  return urls;
}
"""


class BrowserUnavailable(RuntimeError):
    pass


class BrowserHttpError(RuntimeError):
    def __init__(self, status_code: int, url: str) -> None:
        super().__init__(f"HTTP {status_code} for {url}")
        self.status_code = status_code
        self.url = url


class ResponseAdapter:
    """Common response API used by the downloader."""

    status_code: int
    ok: bool
    headers: dict[str, str]
    url: str

    @property
    def content(self) -> bytes:  # pragma: no cover - overridden
        raise NotImplementedError

    @property
    def text(self) -> str:
        return self.content.decode("utf-8", errors="replace")

    def json(self) -> Any:
        return json_module.loads(self.text)

    def raise_for_status(self) -> None:
        if not self.ok:
            raise BrowserHttpError(self.status_code, self.url)

    def iter_content(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        body = self.content
        for offset in range(0, len(body), chunk_size):
            yield body[offset : offset + chunk_size]

    def __enter__(self) -> ResponseAdapter:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None


class BrowserResponse(ResponseAdapter):
    """Adapts a Playwright ``APIResponse``."""

    def __init__(self, response: Any) -> None:
        self._response = response
        self._body: bytes | None = None
        self.status_code = int(response.status)
        self.ok = bool(response.ok)
        self.headers = {
            str(name).lower(): str(value)
            for name, value in (response.headers or {}).items()
        }
        self.url = str(response.url)

    @property
    def content(self) -> bytes:
        if self._body is None:
            self._body = self._response.body()
        return self._body

    @property
    def text(self) -> str:
        return self._response.text()


class InPageResponse(ResponseAdapter):
    """Response produced by ``fetch`` executed inside the browser page."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.status_code = int(data.get("status", 0))
        self.ok = bool(data.get("ok", False))
        self.url = str(data.get("url", ""))
        self.headers = {
            str(name).lower(): str(value)
            for name, value in (data.get("headers") or {}).items()
        }
        self._body = base64.b64decode(data.get("body") or "")

    @property
    def content(self) -> bytes:
        return self._body


@dataclass
class BrowserCapture:
    tokens: dict[str, str] = field(default_factory=dict)
    media_urls: dict[str, str] = field(default_factory=dict)
    images: dict[int, str] = field(default_factory=dict)
    html: str | None = None
    final_url: str | None = None

    @property
    def page_url(self) -> str | None:
        return self.final_url


class BrowserHttpClient:
    """Playwright-backed HTTP client used for ``--browser`` mode.

    The extension has an advantage over a plain HTTP client: it runs inside a
    real browser, so Cloudflare's JavaScript challenge passes and the
    ``webRequest`` listener can see the real ``Authorization`` headers. This
    class recreates that environment:

    * page navigation loads the score HTML (solving the challenge);
    * API and static requests reuse the browser's cookies through
      ``curl_cffi`` (which impersonates Chrome's TLS fingerprint), because
      Playwright's in-page ``fetch`` is refused by Cloudflare for
      ``api/jmuse``;
    * if ``curl_cffi`` is unavailable or rejected, requests fall back to an
      in-page ``fetch``;
    * ``api/jmuse`` requests are observed to capture tokens;
    * external CDN downloads use Playwright's request context as a fallback.
    """

    def __init__(
        self,
        *,
        cookie: str | None = None,
        cookie_file: str | None = None,
        cookies_from_browser_name: str | None = None,
        headless: bool = True,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        logger: Logger | None = None,
    ) -> None:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise BrowserUnavailable(
                "Browser mode requires Playwright. Install it with:\n"
                "  python3 -m pip install playwright\n"
                "  python3 -m playwright install chromium\n"
                "\nOr, from this project folder, create a venv and use the extras:\n"
                "  python3 -m venv .venv\n"
                "  source .venv/bin/activate\n"
                "  pip install -e '.[pdf,browser,browsers]'\n"
                "  playwright install chromium"
            ) from exc

        self.logger = logger or Logger()
        self.timeout = timeout
        self.user_agent = user_agent
        self.tokens: dict[str, str] = {}
        self.media_urls: dict[str, str] = {}
        self.images: dict[int, str] = {}

        cookies = resolve_cookies(
            cookie=cookie,
            cookie_file=cookie_file,
            cookies_from_browser_name=cookies_from_browser_name,
        )

        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=headless,
            args=[
                "--autoplay-policy=no-user-gesture-required",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        self._context = self._browser.new_context(
            user_agent=user_agent,
            locale="en-US",
            viewport={"width": 1440, "height": 900},
        )
        self._context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', "
            "{get: () => undefined});"
        )

        if cookies:
            self._context.add_cookies(
                [
                    {
                        "name": name,
                        "value": value,
                        "domain": ".musescore.com",
                        "path": "/",
                    }
                    for name, value in cookies.items()
                ]
            )

        self._page: Any | None = None
        self._curl_session: Any | None = None

    # -------------------------------------------------------------- curl_cffi

    @property
    def has_curl(self) -> bool:
        return curl_requests is not None

    def _sync_curl_cookies(self, session: Any) -> None:
        """Copy the live browser cookies into a ``curl_cffi`` session."""
        try:
            cookies = self._context.cookies()
        except Exception:
            return

        values = {
            cookie["name"]: cookie["value"]
            for cookie in cookies
            if cookie.get("name") and "musescore" in cookie.get("domain", "").lower()
        }
        if values:
            try:
                session.cookies.update(values)
            except Exception as exc:
                self.logger.debug(f"Could not sync cookies to curl_cffi: {exc}")

    def _get_curl_session(self) -> Any | None:
        if curl_requests is None:
            return None

        if self._curl_session is None:
            try:
                self._curl_session = curl_requests.Session(impersonate="chrome")
            except Exception as exc:
                self.logger.debug(f"Could not start curl_cffi session: {exc}")
                return None

        self._sync_curl_cookies(self._curl_session)
        return self._curl_session

    def _curl_get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> Any | None:
        session = self._get_curl_session()
        if session is None:
            return None

        try:
            return session.get(
                url,
                headers=headers,
                params=params,
                timeout=self.timeout,
                stream=stream,
                allow_redirects=allow_redirects,
            )
        except Exception as exc:
            self.logger.debug(f"curl_cffi request failed: {exc}")
            return None

    # ------------------------------------------------------------- transport

    @property
    def cookie_header(self) -> str:
        try:
            cookies = self._context.cookies()
        except Exception:
            return ""

        pairs = [
            f"{cookie['name']}={cookie['value']}"
            for cookie in cookies
            if "musescore" in cookie.get("domain", "").lower()
        ]
        return "; ".join(pairs)

    @staticmethod
    def _is_musescore_host(url: str) -> bool:
        hostname = (urlparse(url).hostname or "").lower()
        return hostname == "musescore.com" or hostname.endswith(".musescore.com")

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> ResponseAdapter:
        request_url = url
        if params:
            separator = "&" if "?" in request_url else "?"
            request_url += separator + urlencode(params, doseq=True)

        request_headers = dict(headers or {})
        if referer:
            request_headers["Referer"] = referer

        # Prefer curl_cffi: it reuses the browser's session cookies while
        # presenting a genuine Chrome TLS fingerprint. Cloudflare accepts that
        # for api/jmuse, whereas Playwright's in-page fetch is refused.
        curl_response = self._curl_get(
            url,
            headers=request_headers,
            params=params,
            stream=stream,
            allow_redirects=allow_redirects,
        )

        if curl_response is not None:
            status_code = int(curl_response.status_code)
            content_type = str(
                (curl_response.headers or {}).get("Content-Type", "")
            ).lower()
            challenge = status_code in (403, 429, 503) or (
                status_code == 200
                and "/api/" in url
                and "text/html" in content_type
            )

            if not challenge:
                return CurlResponse(curl_response)

            self.logger.debug(
                f"curl_cffi got HTTP {status_code} for {url}; "
                "falling back to the browser"
            )
            try:
                curl_response.close()
            except Exception:
                pass

        # Same-site fallback: an in-page fetch carries the browser session.
        if self._is_musescore_host(request_url):
            return self._page_fetch(request_url, request_headers)

        response = BrowserResponse(
            self._context.request.get(
                request_url,
                headers=request_headers,
                timeout=int(self.timeout * 1000),
                max_redirects=20 if allow_redirects else 0,
            )
        )

        if response.status_code == 403 and self._page is not None:
            try:
                return self._page_fetch(request_url, request_headers)
            except Exception as exc:
                self.logger.debug(f"In-page fallback failed: {exc}")

        return response

    def fetch_text(self, url: str, *, referer: str | None = None) -> str:
        response = self.get(url, referer=referer)
        response.raise_for_status()
        return response.text

    def fetch_page_html(self, url: str) -> str:
        """Navigate to a page and return HTML after the challenge is solved."""
        page = self._ensure_page()

        try:
            page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=max(30_000, int(self.timeout * 1000)),
            )
        except Exception as exc:
            self.logger.debug(f"Navigation warning: {exc}")

        deadline = time.monotonic() + max(self.timeout, 15.0)
        html = ""

        while time.monotonic() < deadline:
            html = page.content()
            lowered = html.lower()
            if "just a moment" not in lowered and "cf-challenge" not in lowered:
                break
            page.wait_for_timeout(500)

        # Let client-side metadata settle.
        page.wait_for_timeout(500)
        html = page.content()

        lowered = html.lower()
        if "just a moment" in lowered or "cf-challenge" in lowered:
            # Cloudflare sometimes keeps challenging a fresh Playwright
            # profile even though the same cookies work with curl_cffi.
            if self.has_curl:
                self.logger.debug(
                    "Cloudflare challenge in Chromium; trying curl_cffi"
                )
                try:
                    fallback = self.fetch_text(url, referer=url)
                except Exception as exc:
                    self.logger.debug(f"curl_cffi page fetch failed: {exc}")
                else:
                    fallback_lower = fallback.lower()
                    if (
                        fallback
                        and "just a moment" not in fallback_lower
                        and "cf-challenge" not in fallback_lower
                    ):
                        return fallback

            raise BrowserUnavailable(
                "Cloudflare did not let the page load. Try --no-headless, or "
                "pass cookies from a browser that already has access "
                "(--cookies-from-browser chrome)."
            )

        return html

    def _page_fetch(self, url: str, headers: dict[str, str]) -> InPageResponse:
        page = self._ensure_page()

        if urlparse(page.url).hostname in (None, "", "about:blank"):
            page.goto("https://musescore.com/", wait_until="domcontentloaded")

        # Forbidden browser headers (Cookie/Referer) are ignored by fetch.
        safe_headers = {
            name: value
            for name, value in headers.items()
            if name.lower() not in ("cookie", "referer", "host", "content-length")
        }

        data = page.evaluate(
            PAGE_FETCH_JS, {"url": url, "headers": safe_headers}
        )
        return InPageResponse(data)

    # ------------------------------------------------------------------ page

    @property
    def page(self) -> Any:
        return self._ensure_page()

    def _ensure_page(self) -> Any:
        if self._page is not None:
            return self._page

        self._page = self._context.new_page()
        self._page.set_default_timeout(max(30_000, int(self.timeout * 1000)))
        self._page.on("request", lambda request: self._record_request(request))
        self._page.on("response", lambda response: self._record_response(response))
        return self._page

    def _record_request(self, request: Any) -> None:
        try:
            if "/api/jmuse" not in request.url:
                return

            query = parse_qs(urlparse(request.url).query)
            score_id = (query.get("id") or [""])[0]
            media_type = (query.get("type") or [""])[0]
            index = (query.get("index") or [""])[0]

            if not score_id or not media_type or not index:
                return

            headers = request.headers or {}
            token = headers.get("authorization") or headers.get("Authorization")

            if token:
                key = f"{score_id}_{media_type}_{index}"
                self.tokens[key] = token
                self.logger.debug(f"Captured token for {key}")
        except Exception as exc:
            self.logger.debug(f"Could not inspect request: {exc}")

    def _record_response(self, response: Any) -> None:
        try:
            if "/api/jmuse" not in response.url:
                return

            query = parse_qs(urlparse(response.url).query)
            score_id = (query.get("id") or [""])[0]
            media_type = (query.get("type") or [""])[0]
            index = (query.get("index") or [""])[0]

            if not score_id or not media_type or not index:
                return

            data = response.json()
            if not isinstance(data, dict) or data.get("status") != 200:
                return

            url = (data.get("info") or {}).get("url")
            if isinstance(url, str) and url:
                key = f"{score_id}_{media_type}_{index}"
                self.media_urls[key] = url
                self.logger.debug(f"Captured media URL for {key}")
        except Exception as exc:
            self.logger.debug(f"Could not inspect response: {exc}")

    def _collect_dom_images(self, page: Any) -> None:
        try:
            urls = page.evaluate(DOM_IMAGES_JS) or []
        except Exception as exc:
            self.logger.debug(f"Could not read images from the page: {exc}")
            return

        for url in urls:
            match = re.search(r"score_(\d+)\.(?:png|svg)", str(url), re.IGNORECASE)
            if match:
                self.images[int(match.group(1))] = str(url)

    def capture_tokens(
        self,
        url: str,
        *,
        expected_keys: set[str] | None = None,
        wait: float = 10.0,
        capture_html: bool = False,
    ) -> BrowserCapture:
        page = self._ensure_page()

        current = urlparse(page.url)
        target = urlparse(url)
        same_page = (
            current.scheme in ("http", "https")
            and current.netloc
            and current.netloc == target.netloc
            and current.path.rstrip("/") == target.path.rstrip("/")
        )

        if not same_page:
            try:
                page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(30_000, int(wait * 1000) + 30_000),
                )
            except Exception as exc:
                self.logger.debug(f"Navigation warning: {exc}")

        page.wait_for_timeout(750)
        self._trigger_media_loads(page)

        deadline = time.monotonic() + max(wait, 1.0)
        while time.monotonic() < deadline:
            if expected_keys and all(key in self.tokens for key in expected_keys):
                page.wait_for_timeout(1000)
                break
            page.wait_for_timeout(250)

        result = BrowserCapture(tokens=dict(self.tokens))
        if "/scores/" in page.url:
            self._collect_dom_images(page)
        result.media_urls = dict(self.media_urls)
        result.images = dict(self.images)
        if capture_html:
            result.html = page.content()
        result.final_url = page.url
        return result

    def _trigger_media_loads(self, page: Any) -> None:
        """Best-effort actions matching ``src/content/scrape.js``."""
        try:
            if page.evaluate(EXPAND_PAGES_JS):
                self.logger.debug("Expanded score page container")
        except Exception as exc:
            self.logger.debug(f"Could not expand score container: {exc}")

        try:
            page.evaluate(SCROLL_JS)
        except Exception as exc:
            self.logger.debug(f"Could not scroll page: {exc}")

        page.wait_for_timeout(1000)
        self._collect_dom_images(page)

        try:
            if page.evaluate(TOGGLE_PLAY_CLICK_JS):
                page.wait_for_timeout(300)
                page.evaluate(TOGGLE_PLAY_CLICK_JS)
                self.logger.debug("Toggled the playback button")
        except Exception as exc:
            self.logger.debug(f"Could not toggle playback: {exc}")

        try:
            if page.evaluate(MIDI_CLICK_JS):
                self.logger.debug("Clicked the MIDI button")
        except Exception as exc:
            self.logger.debug(f"Could not click the MIDI button: {exc}")

    def close(self) -> None:
        if self._curl_session is not None:
            try:
                self._curl_session.close()
            except Exception:
                pass
            self._curl_session = None
        try:
            if self._page is not None:
                self._page.close()
        except Exception:
            pass
        try:
            self._context.close()
        except Exception:
            pass
        try:
            self._browser.close()
        except Exception:
            pass
        try:
            self._playwright.stop()
        except Exception:
            pass


def capture(
    url: str,
    *,
    headless: bool = True,
    cookie_header: str = "",
    wait: float = 10.0,
    expected_keys: set[str] | None = None,
    capture_html: bool = False,
    logger: Logger | None = None,
    user_agent: str = DEFAULT_USER_AGENT,
) -> BrowserCapture:
    """Standalone token capture (equivalent of the extension's scrape flow)."""
    client = BrowserHttpClient(
        cookie=cookie_header or None,
        headless=headless,
        user_agent=user_agent,
        logger=logger,
        timeout=max(wait, 30.0),
    )
    try:
        return client.capture_tokens(
            url,
            expected_keys=expected_keys,
            wait=wait,
            capture_html=capture_html,
        )
    finally:
        client.close()


def fetch_html(
    url: str,
    *,
    headless: bool = True,
    cookie_header: str = "",
    wait: float = 3.0,
    logger: Logger | None = None,
) -> str:
    client = BrowserHttpClient(
        cookie=cookie_header or None,
        headless=headless,
        logger=logger,
        timeout=max(wait, 30.0),
    )
    try:
        return client.fetch_page_html(url)
    finally:
        client.close()