#!/usr/bin/env python3
"""Easy entry point for the MuseScore Downloader.

Usage
-----
    python main.py                 # interactive menu
    python main.py sheet 123456 --browser
    python main.py info 123456 --browser

All arguments are passed straight to the ``musescore-downloader`` CLI.
"""

from __future__ import annotations

import os
import sys

SETUP_HINT = """\
The Python dependencies are not installed yet.

Run these commands from this folder:

    python3 -m venv .venv
    source .venv/bin/activate
    pip install -e '.[pdf,browser,browsers]'
    playwright install chromium

Then start again with:  python main.py
"""

BANNER = r""" __  __                ___                  _              _
|  \/  |_  _ ___ ___  |   \ _____ __ ___ _ | |___  __ _ __| |___ _ _
| |\/| | || (_-</ -_) | |) / _ \ V  V / ' \| / _ \/ _` / _` / -_) '_|
|_|  |_|\_,_/__/\___| |___/\___/\_/\_/|_||_|_\___/\__,_\__,_\___|_|
"""

BANNER_COLORS = ("\033[1;96m", "\033[1;94m", "\033[1;95m", "\033[1;91m")
RESET = "\033[0m"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM", "") == "dumb":
        return False
    return bool(getattr(sys.stdout, "isatty", lambda: False)())


def _print_banner() -> None:
    print()
    lines = BANNER.rstrip("\n").splitlines()
    if _supports_color():
        for line, color in zip(lines, BANNER_COLORS, strict=False):
            print(f"{color}{line}{RESET}")
    else:
        print("\n".join(lines))
    print()


def _load_cli():
    try:
        from musescore_downloader.cli import main as cli_main
    except ModuleNotFoundError as exc:
        print(f"Missing Python package: {exc.name or exc}", file=sys.stderr)
        print(SETUP_HINT, file=sys.stderr)
        raise SystemExit(1) from exc

    return cli_main


def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    try:
        answer = input(f"{prompt}{suffix}: ").strip()
    except EOFError:
        return default
    return answer or default


def _ask_yes_no(prompt: str, default: bool = True) -> bool:
    default_text = "Y/n" if default else "y/N"
    answer = _ask(f"{prompt} [{default_text}]", "").lower()
    if not answer:
        return default
    return answer in ("y", "yes", "1", "true")


def _output_argument(folder: str) -> str:
    folder = os.path.expanduser(folder.strip())
    if not folder:
        folder = "."
    if not folder.endswith(("/", os.sep)):
        folder += os.sep
    return folder


def interactive() -> int:
    cli_main = _load_cli()

    _print_banner()
    print("1) Show score information")
    print("2) Download sheet music (PDF)")
    print("3) Download audio (MP3)")
    print("4) Download MIDI")
    print("5) Download everything")
    print("0) Exit")
    print()

    choice = _ask("Choose an option", "0")
    commands = {
        "1": "info",
        "2": "sheet",
        "3": "audio",
        "4": "midi",
        "5": "all",
    }

    if choice in ("0", "q", "quit", "exit"):
        print("Bye.")
        return 0

    if choice not in commands:
        print("Invalid option.")
        return 2

    command = commands[choice]

    url = _ask("Score URL or numeric ID")
    if not url:
        print("A score URL or ID is required.", file=sys.stderr)
        return 2

    args = [command, url]

    if command != "info":
        folder = _ask("Output folder (blank = current folder)")
        if folder:
            args += ["-o", _output_argument(folder)]

    if command in ("sheet", "all"):
        pages = _ask("Page count override (blank = auto-detect)")
        if pages:
            args += ["--pages", pages]

    use_browser = _ask_yes_no(
        "Use Chromium/Playwright? (recommended for Cloudflare)", True
    )
    if use_browser:
        args.append("--browser")

    browser_name = _ask(
        "Read cookies from browser (chrome/firefox/edge/brave/safari, blank = none)"
    )
    if browser_name:
        args += ["--cookies-from-browser", browser_name.lower()]

    print()
    print("Running:", " ".join(args), flush=True)
    print()

    try:
        return cli_main(args)
    except KeyboardInterrupt:
        print("\nCancelled.", file=sys.stderr)
        return 130


def main() -> int:
    # Any arguments = non-interactive CLI mode.
    if len(sys.argv) > 1:
        cli_main = _load_cli()
        try:
            return cli_main(sys.argv[1:])
        except KeyboardInterrupt:
            print("\nCancelled.", file=sys.stderr)
            return 130

    return interactive()


if __name__ == "__main__":
    raise SystemExit(main())