from __future__ import annotations

import os
import re
import sys
from pathlib import Path


class Logger:
    """No-op logger used as the library default."""

    def status(self, message: str) -> None:
        pass

    def info(self, message: str) -> None:
        pass

    def debug(self, message: str) -> None:
        pass

    def warning(self, message: str) -> None:
        pass

    def error(self, message: str) -> None:
        pass


class ConsoleLogger(Logger):
    """Small console logger with optional verbose/quiet modes."""

    def __init__(self, verbose: bool = False, quiet: bool = False) -> None:
        self.verbose = verbose
        self.quiet = quiet

    def status(self, message: str) -> None:
        if not self.quiet:
            print(f"[*] {message}", flush=True)

    def info(self, message: str) -> None:
        if not self.quiet:
            print(message, flush=True)

    def debug(self, message: str) -> None:
        if self.verbose and not self.quiet:
            print(f"[debug] {message}", flush=True)

    def warning(self, message: str) -> None:
        if not self.quiet:
            print(f"[!] {message}", file=sys.stderr, flush=True)

    def error(self, message: str) -> None:
        print(f"[x] {message}", file=sys.stderr, flush=True)


_INVALID_FILENAME_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_SCORE_URL_RE = re.compile(r"^https?://\S*musescore\.com/", re.IGNORECASE)
_SCORE_PAGE_RE = re.compile(
    r"^https?://musescore\.com/(?:user/\d+|\w+)/scores/\d+", re.IGNORECASE
)


def sanitize_filename(name: str, fallback: str = "score", max_length: int = 150) -> str:
    """Make a string safe to use as a file name on every major platform."""
    cleaned = _INVALID_FILENAME_CHARS.sub(" ", name or "")
    cleaned = re.sub(r"\s+", " ", cleaned).strip(" .")

    if len(cleaned) > max_length:
        cleaned = cleaned[:max_length].rstrip(" .")

    return cleaned or fallback


def resolve_output_path(
    output: str | os.PathLike[str] | None,
    default_name: str,
    suffix: str,
) -> Path:
    """Resolve a user-supplied output argument to a concrete file path."""
    if output is None or str(output).strip() == "":
        path = Path.cwd() / default_name
    else:
        raw = str(output)
        path = Path(raw).expanduser()

        if path.is_dir() or raw.endswith(("/", os.sep)):
            path = path / default_name
        elif not path.suffix:
            path = path.with_suffix(suffix)

    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def normalize_score_url(url: str) -> str:
    """Accept a full URL, a path, or a bare score id."""
    value = (url or "").strip()

    if not value:
        raise ValueError("A score URL or score id is required")

    if re.fullmatch(r"\d+", value):
        return f"https://musescore.com/score/{value}"

    if value.startswith("//"):
        return "https:" + value

    if re.match(r"^(?:www\.)?musescore\.com(?:/|$)", value, re.IGNORECASE):
        return "https://" + value

    if not re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", value):
        return "https://musescore.com/" + value.lstrip("/")

    return value


def is_musescore_url(url: str) -> bool:
    return _SCORE_URL_RE.match(url or "") is not None


def is_score_url(url: str) -> bool:
    return _SCORE_PAGE_RE.match(url or "") is not None


def default_cache_dir() -> Path:
    """Return the per-user cache directory used for the token secret."""
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))

    return base / "musescore-downloader"


def format_size(byte_count: int) -> str:
    size = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024 or unit == "GiB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{byte_count} B"