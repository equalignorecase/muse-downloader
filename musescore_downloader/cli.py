from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from . import __version__
from .api import JmuseError
from .browser import BrowserUnavailable
from .downloader import DownloadError, MuseScoreDownloader
from .http_client import (
    DEFAULT_USER_AGENT,
    CookieLoadError,
    HttpClient,
)
from .pdf import PdfError
from .utils import ConsoleLogger, is_musescore_url

BROWSERS = (
    "chrome",
    "chromium",
    "firefox",
    "edge",
    "brave",
    "opera",
    "vivaldi",
    "safari",
)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("url", help="MuseScore score URL or numeric score id")
    parser.add_argument(
        "-o",
        "--output",
        help="Output file or directory (default: current directory)",
    )
    parser.add_argument("--cookie", help="Cookie header, e.g. 'a=b; c=d'")
    parser.add_argument("--cookie-file", help="cookies.txt, JSON, or raw cookie header")
    parser.add_argument(
        "--cookies-from-browser",
        choices=BROWSERS,
        help="Read musescore.com cookies from a browser profile",
    )
    parser.add_argument(
        "--browser",
        action="store_true",
        help="Use Chromium/Playwright to load the page and capture tokens",
    )
    parser.add_argument(
        "--headless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run Chromium headless (default: true)",
    )
    parser.add_argument(
        "--scrape-wait",
        type=float,
        default=10.0,
        help="Seconds to wait while capturing tokens (default: 10)",
    )
    parser.add_argument(
        "--timeout", type=float, default=30.0, help="HTTP timeout in seconds"
    )
    parser.add_argument(
        "--user-agent",
        default=None,
        help="Override the User-Agent (match your browser when reusing cookies)",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Show debug output"
    )
    parser.add_argument(
        "-q", "--quiet", action="store_true", help="Only print errors"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="musescore-downloader",
        description=(
            "Download sheet music (PDF), audio (MP3), and MIDI from musescore.com. "
            "Python port of the Music Score Downloader extension."
        ),
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    info = subparsers.add_parser("info", help="Show score metadata")
    _add_common_arguments(info)
    info.add_argument("--json", action="store_true", help="Print JSON")

    sheet = subparsers.add_parser("sheet", help="Download sheet music as PDF")
    _add_common_arguments(sheet)
    sheet.add_argument(
        "--pages",
        type=int,
        help="Override the detected page count",
    )
    sheet.add_argument(
        "--max-pages",
        type=int,
        default=100,
        help="Maximum number of pages to probe when unknown (default: 100)",
    )
    sheet.add_argument(
        "--allow-partial",
        action="store_true",
        help="Save a PDF even when some pages could not be downloaded",
    )

    audio = subparsers.add_parser("audio", help="Download the MP3 audio")
    _add_common_arguments(audio)

    midi = subparsers.add_parser("midi", help="Download the MIDI file")
    _add_common_arguments(midi)

    all_parser = subparsers.add_parser(
        "all", help="Download sheet, audio, and MIDI"
    )
    _add_common_arguments(all_parser)
    all_parser.add_argument("--pages", type=int, help="Override the page count")
    all_parser.add_argument(
        "--max-pages", type=int, default=100, help="Maximum pages to probe"
    )
    all_parser.add_argument(
        "--allow-partial",
        action="store_true",
        help="Save a PDF even when some pages could not be downloaded",
    )

    return parser


def _make_http(args: argparse.Namespace):
    if args.browser:
        from .browser import BrowserHttpClient

        return BrowserHttpClient(
            cookie=args.cookie,
            cookie_file=args.cookie_file,
            cookies_from_browser_name=args.cookies_from_browser,
            headless=args.headless,
            timeout=args.timeout,
            user_agent=args.user_agent or DEFAULT_USER_AGENT,
        )

    return HttpClient(
        cookie=args.cookie,
        cookie_file=args.cookie_file,
        cookies_from_browser_name=args.cookies_from_browser,
        timeout=args.timeout,
        user_agent=args.user_agent or DEFAULT_USER_AGENT,
    )


def _run(args: argparse.Namespace, logger: ConsoleLogger) -> int:
    if not is_musescore_url(args.url) and not args.url.strip().isdigit():
        logger.warning(
            "The URL does not look like a musescore.com score URL; trying anyway"
        )

    http = _make_http(args)
    try:
        return _dispatch(args, logger, http)
    finally:
        close = getattr(http, "close", None)
        if close:
            close()


def _dispatch(
    args: argparse.Namespace, logger: ConsoleLogger, http
) -> int:
    downloader = MuseScoreDownloader(
        args.url,
        http=http,
        logger=logger,
        use_browser=args.browser,
        headless=args.headless,
        scrape_wait=args.scrape_wait,
        max_pages=getattr(args, "max_pages", 100),
        timeout=args.timeout,
    )

    if args.command == "info":
        score = downloader.load()
        data = score.to_dict()

        if args.json:
            print(json.dumps(data, indent=2, ensure_ascii=False))
        else:
            print(f"Title:       {score.name or '(unknown)'}")
            print(f"Composer:    {score.composer or '(unknown)'}")
            print(f"Score ID:    {score.score_id or '(unknown)'}")
            print(f"Pages:       {score.pages or '(unknown)'}")
            print(f"First page:  {score.first_image or '(unknown)'}")
            print(f"URL:         {score.url}")
        return 0

    if args.command == "sheet":
        destination = downloader.download_sheet(
            args.output,
            page_count=args.pages,
            allow_partial=args.allow_partial,
        )
        logger.info(f"Done: {destination}")
        return 0

    if args.command == "audio":
        destination = downloader.download_audio(args.output)
        logger.info(f"Done: {destination}")
        return 0

    if args.command == "midi":
        destination = downloader.download_midi(args.output)
        logger.info(f"Done: {destination}")
        return 0

    if args.command == "all":
        results = downloader.download_all(
            output_dir=args.output,
            page_count=args.pages,
            allow_partial=args.allow_partial,
        )

        failed = [name for name, value in results.items() if isinstance(value, Exception)]
        for name, value in results.items():
            if isinstance(value, Exception):
                logger.error(f"{name}: {value}")
            else:
                logger.info(f"{name}: {value}")

        return 1 if failed else 0

    return 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    logger = ConsoleLogger(verbose=args.verbose, quiet=args.quiet)

    try:
        return _run(args, logger)
    except KeyboardInterrupt:
        logger.error("Interrupted")
        return 130
    except (DownloadError, JmuseError, PdfError, CookieLoadError, BrowserUnavailable, ValueError) as exc:
        logger.error(str(exc))
        return 1
    except Exception as exc:
        if args.verbose:
            raise
        logger.error(f"Unexpected error: {exc}")
        return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())