from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import requests

try:  # optional dependency: real Chrome TLS fingerprint for Cloudflare
    from curl_cffi import requests as curl_requests
except ImportError:  # pragma: no cover - exercised only without the extra
    curl_requests = None

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/131.0.0.0 Safari/537.36"
)


class CookieLoadError(RuntimeError):
    pass


def parse_cookie_header(header: str) -> dict[str, str]:
    """Parse a ``name=value; name2=value2`` cookie header."""
    cookies: dict[str, str] = {}

    for part in (header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue

        name, value = part.split("=", 1)
        name = name.strip()
        if name:
            cookies[name] = value.strip().strip('"')

    return cookies


def _iter_cookie_records(data: Any) -> Iterable[dict[str, Any]]:
    if isinstance(data, dict):
        if isinstance(data.get("cookies"), list):
            yield from _iter_cookie_records(data["cookies"])
            return

        # Plain mapping of name -> value.
        if all(isinstance(value, (str, int, float)) for value in data.values()):
            for name, value in data.items():
                yield {"name": str(name), "value": str(value)}
            return

        if "name" in data and "value" in data:
            yield data
        return

    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield from _iter_cookie_records(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                yield {"name": str(item[0]), "value": str(item[1])}


def load_cookie_file(path: str | Path) -> dict[str, str]:
    """Load cookies from JSON, Netscape cookies.txt, or a raw header string."""
    cookie_path = Path(path).expanduser()
    try:
        text = cookie_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise CookieLoadError(f"Cannot read cookie file {cookie_path}: {exc}") from exc

    stripped = text.strip()
    if not stripped:
        return {}

    if stripped.startswith(("{", "[")):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise CookieLoadError(f"Invalid JSON cookie file {cookie_path}: {exc}") from exc

        cookies: dict[str, str] = {}
        for record in _iter_cookie_records(data):
            name = str(record.get("name", "")).strip()
            if not name:
                continue
            domain = str(record.get("domain", ""))
            if domain and "musescore" not in domain.lower():
                continue
            cookies[name] = str(record.get("value", "")).strip().strip('"')
        return cookies

    lines = text.splitlines()
    is_netscape = any(line.count("\t") >= 6 for line in lines if line.strip())
    if is_netscape:
        cookies = {}
        for line in lines:
            if not line.strip() or line.startswith("#"):
                continue
            fields = line.split("\t")
            if len(fields) < 7:
                continue
            domain, _flag, _path, _secure, _expiry, name, value = fields[:7]
            if "musescore" not in domain.lower():
                continue
            cookies[name] = value
        return cookies

    return parse_cookie_header(text)


def cookies_from_browser(browser: str) -> dict[str, str]:
    """Read musescore.com cookies from a local browser profile.

    Requires the optional ``browser-cookie3`` package.
    """
    try:
        import browser_cookie3
    except ImportError as exc:
        raise CookieLoadError(
            "Reading cookies from a browser requires the optional dependency. "
            "Install it with: python3 -m pip install browser-cookie3"
        ) from exc

    loader = getattr(browser_cookie3, browser, None)
    if loader is None:
        raise CookieLoadError(f"Unsupported browser: {browser}")

    try:
        jar = loader(domain_name=".musescore.com")
    except TypeError:
        jar = loader()

    cookies = {}
    for cookie in jar:
        if "musescore" in (cookie.domain or "").lower() or not cookie.domain:
            cookies[cookie.name] = cookie.value
    return cookies


def resolve_cookies(
    *,
    cookie: str | None = None,
    cookie_file: str | None = None,
    cookies_from_browser_name: str | None = None,
) -> dict[str, str]:
    """Build a cookie mapping from all supported sources."""
    cookies: dict[str, str] = {}
    if cookie_file:
        cookies.update(load_cookie_file(cookie_file))
    if cookie:
        cookies.update(parse_cookie_header(cookie))
    if cookies_from_browser_name:
        cookies.update(cookies_from_browser(cookies_from_browser_name))
    return cookies


class CurlResponse:
    """Adapter for ``curl_cffi`` responses.

    Mirrors the small subset of ``requests.Response`` used by the downloader,
    including context-manager and streaming support.
    """

    def __init__(self, response: Any) -> None:
        self._response = response
        self.status_code = int(response.status_code)
        self.ok = bool(200 <= self.status_code < 400)
        self.headers = {
            str(name).lower(): str(value)
            for name, value in (response.headers or {}).items()
        }
        self.url = str(response.url)

    @property
    def content(self) -> bytes:
        return self._response.content

    @property
    def text(self) -> str:
        return self._response.text

    def json(self) -> Any:
        return self._response.json()

    def raise_for_status(self) -> None:
        if not self.ok:
            raise requests.HTTPError(
                f"HTTP {self.status_code} for {self.url}", response=self  # type: ignore[arg-type]
            )

    def iter_content(self, chunk_size: int = 64 * 1024) -> Iterator[bytes]:
        yield from self._response.iter_content(chunk_size=chunk_size)

    def close(self) -> None:
        try:
            self._response.close()
        except Exception:
            pass

    def __enter__(self) -> CurlResponse:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class HttpClient:
    """Thin ``requests`` wrapper mirroring the extension's fetch helper."""

    def __init__(
        self,
        *,
        cookie: str | None = None,
        cookie_file: str | None = None,
        cookies_from_browser_name: str | None = None,
        timeout: float = 30.0,
        user_agent: str = DEFAULT_USER_AGENT,
        headers: dict[str, str] | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.update(
            {
                "User-Agent": user_agent,
                "Accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        if headers:
            self.session.headers.update(headers)

        cookies = resolve_cookies(
            cookie=cookie,
            cookie_file=cookie_file,
            cookies_from_browser_name=cookies_from_browser_name,
        )

        for name, value in cookies.items():
            self.session.cookies.set(name, value, domain=".musescore.com", path="/")

        self._explicit_cookies = cookies
        self._curl_session: Any | None = None

    @property
    def has_curl(self) -> bool:
        return curl_requests is not None

    def _get_curl_session(self) -> Any | None:
        if curl_requests is None:
            return None

        if self._curl_session is None:
            try:
                session = curl_requests.Session(impersonate="chrome")
            except Exception:
                return None

            session.headers.update(dict(self.session.headers))
            if self._explicit_cookies:
                try:
                    session.cookies.update(self._explicit_cookies)
                except Exception:
                    pass
            self._curl_session = session

        return self._curl_session

    @property
    def cookie_header(self) -> str:
        """Cookie header usable with Playwright or a raw HTTP request."""
        cookies: dict[str, str] = dict(self._explicit_cookies)
        for cookie in self.session.cookies:
            if "musescore" in (cookie.domain or "").lower() or not cookie.domain:
                cookies[cookie.name] = cookie.value
        return "; ".join(f"{name}={value}" for name, value in cookies.items())

    def get(
        self,
        url: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
        referer: str | None = None,
        stream: bool = False,
        allow_redirects: bool = True,
    ) -> requests.Response | CurlResponse:
        request_headers = dict(headers or {})
        if referer:
            request_headers["Referer"] = referer

        # curl_cffi can pass Cloudflare by impersonating Chrome's TLS stack.
        # Fall back to plain requests when it is missing or rejected.
        curl_session = self._get_curl_session()
        if curl_session is not None:
            try:
                response = curl_session.get(
                    url,
                    headers=request_headers,
                    params=params,
                    timeout=self.timeout,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            except Exception:
                response = None

            if response is not None:
                if response.status_code not in (403, 429, 503):
                    return CurlResponse(response)
                try:
                    response.close()
                except Exception:
                    pass

        return self.session.get(
            url,
            headers=request_headers,
            params=params,
            timeout=self.timeout,
            stream=stream,
            allow_redirects=allow_redirects,
        )

    def close(self) -> None:
        if self._curl_session is not None:
            try:
                self._curl_session.close()
            except Exception:
                pass
            self._curl_session = None
        try:
            self.session.close()
        except Exception:
            pass

    def fetch_text(self, url: str, *, referer: str | None = None) -> str:
        response = self.get(url, referer=referer)
        response.raise_for_status()
        return response.text