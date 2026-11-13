from __future__ import annotations

import html as html_module
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

_PAGES_TEXT_RE = re.compile(r"\b\d+\s+of\s+(\d+)\s+pages\b", re.IGNORECASE)
_PAGES_TABLE_RE = re.compile(r'Pages</h3></th><td><div[\w ="]+>(\d+)<')
_PAGES_JSON_RES = (
    re.compile(r'["\'](?:numberOfPages|page_count|pageCount|totalPages)["\']\s*:\s*(\d+)'),
    re.compile(r'"pages"\s*:\s*(\d+)(?!\s*[,\]}])'),
)
_PAGES_META_RE = re.compile(r'"pages"\s*:\s*(\d+)')
_SCORE_IMAGE_RE = re.compile(r"score_(\d+)\.(?:png|svg)", re.IGNORECASE)
_FIRST_PAGE_RE = re.compile(r"score_0\.(?:png|svg)(?:[?#]|$)", re.IGNORECASE)
_LD_JSON_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


@dataclass
class ScoreInfo:
    url: str
    score_id: str = ""
    name: str = ""
    composer: str = ""
    first_image: str = ""
    pages: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def is_valid(self) -> bool:
        return bool(self.score_id) and self.score_id.isdigit() and int(self.score_id) > 0

    @property
    def display_name(self) -> str:
        return self.name or (f"musescore-{self.score_id}" if self.score_id else "score")

    @property
    def base_filename(self) -> str:
        if self.composer and self.name:
            return f"{self.composer} - {self.name}"
        return self.name or self.display_name

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["is_valid"] = self.is_valid
        return data


class _PageCollector(HTMLParser):
    """Collects the bits of the page that the extension reads from the DOM."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.metas: list[dict[str, str]] = []
        self.links: list[dict[str, str]] = []
        self.ld_json_blocks: list[str] = []
        self._inside_ld_json = False
        self._ld_json_buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name.lower(): (value or "") for name, value in attrs}

        if tag == "meta":
            self.metas.append(attributes)
        elif tag == "link":
            self.links.append(attributes)
        elif tag == "script" and attributes.get("type", "").lower() == "application/ld+json":
            self._inside_ld_json = True
            self._ld_json_buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "script" and self._inside_ld_json:
            self.ld_json_blocks.append("".join(self._ld_json_buffer))
            self._inside_ld_json = False
            self._ld_json_buffer = []

    def handle_data(self, data: str) -> None:
        if self._inside_ld_json:
            self._ld_json_buffer.append(data)


def _iter_ld_objects(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, list):
        for item in value:
            yield from _iter_ld_objects(item)
        return

    if not isinstance(value, dict):
        return

    if isinstance(value.get("@graph"), (list, dict)):
        yield from _iter_ld_objects(value["@graph"])

    yield value

    for key in ("mainEntity", "itemListElement", "hasPart"):
        if key in value:
            yield from _iter_ld_objects(value[key])


def _iter_meta_values(metas: list[dict[str, str]], prop: str) -> Iterable[str]:
    for meta in metas:
        if meta.get("property") == prop or meta.get("name") == prop:
            content = meta.get("content", "")
            if content:
                yield content


def _clean_url(url: str) -> str:
    url = (url or "").strip()
    if url.startswith("//"):
        return "https:" + url
    return url


def _json_composer_name(composer: Any) -> str:
    if isinstance(composer, str):
        return composer.strip()
    if isinstance(composer, dict):
        if isinstance(composer.get("name"), str):
            return composer["name"].strip()
        for value in composer.values():
            if isinstance(value, dict) and isinstance(value.get("name"), str):
                return value["name"].strip()
    if isinstance(composer, list):
        for item in composer:
            name = _json_composer_name(item)
            if name:
                return name
    return ""


def _extract_ld_objects(html: str) -> list[dict[str, Any]]:
    objects: list[dict[str, Any]] = []

    for match in _LD_JSON_RE.finditer(html):
        try:
            objects.extend(_iter_ld_objects(json.loads(match.group(1))))
        except (json.JSONDecodeError, TypeError):
            continue

    return objects


def _count_pages(html: str, score_objects: list[dict[str, Any]]) -> int:
    for obj in score_objects:
        for key in ("numberOfPages", "pageCount", "pages"):
            value = obj.get(key)
            if isinstance(value, int) and value > 0:
                return value
            if isinstance(value, str) and value.isdigit() and int(value) > 0:
                return int(value)

    for pattern in (_PAGES_TEXT_RE, _PAGES_TABLE_RE):
        match = pattern.search(html)
        if match:
            return int(match.group(1))

    for pattern in _PAGES_JSON_RES:
        match = pattern.search(html)
        if match:
            return int(match.group(1))

    # MuseScore embeds score metadata as HTML-escaped JSON, for example
    # ``&quot;pages&quot;:4``. Looking at the unescaped markup gives the real
    # page count even without JavaScript rendering.
    unescaped = html_module.unescape(html)
    for pattern in (_PAGES_TEXT_RE, _PAGES_TABLE_RE):
        match = pattern.search(unescaped)
        if match:
            return int(match.group(1))

    for pattern in (*_PAGES_JSON_RES, _PAGES_META_RE):
        match = pattern.search(unescaped)
        if match:
            return int(match.group(1))

    image_indexes = [int(index) for index in _SCORE_IMAGE_RE.findall(html)]
    if image_indexes:
        return max(image_indexes) + 1

    return 0


def _last_path_segment(url: str) -> str:
    try:
        path = urlparse(url).path
    except ValueError:
        path = url
    return path.rstrip("/").split("/")[-1]


def _extract_first_image(
    html: str, metas: list[dict[str, str]], links: list[dict[str, str]]
) -> str:
    for meta in metas:
        if meta.get("property") in ("og:image", "twitter:image"):
            content = _clean_url(meta.get("content", ""))
            if _FIRST_PAGE_RE.search(content):
                return content

    for link in links:
        href = _clean_url(link.get("href", ""))
        if "score_0.svg" in href:
            return href

    for link in links:
        href = _clean_url(link.get("href", ""))
        if "score_0.png" in href:
            return href

    for meta in metas:
        if meta.get("property") in ("og:image", "twitter:image"):
            content = _clean_url(meta.get("content", ""))
            if _FIRST_PAGE_RE.search(content):
                return content

    return ""


def parse_score(html: str, url: str) -> ScoreInfo:
    """Parse a score page into :class:`ScoreInfo`.

    Mirrors ``setScoreProps``/``setScoreName``/``setScoreComposer``/
    ``setScoreId``/``setScorePagesSum`` from ``src/content/content.js``.
    """
    collector = _PageCollector()
    try:
        collector.feed(html)
    except Exception:
        # Malformed HTML: fall back to regex-only extraction below.
        pass

    ld_objects = _extract_ld_objects(html)
    music_objects = [
        obj for obj in ld_objects if obj.get("@type") in ("MusicComposition", "MusicRecording")
    ]
    if not music_objects:
        music_objects = ld_objects

    info = ScoreInfo(url=_clean_url(url))
    thumbnail_fallback = ""

    for obj in music_objects:
        if obj.get("@type") not in ("MusicComposition", "MusicRecording"):
            continue

        thumbnail = obj.get("thumbnailUrl") or obj.get("image")
        if isinstance(thumbnail, dict):
            thumbnail = thumbnail.get("url")
        if isinstance(thumbnail, str) and not info.first_image:
            thumbnail = _clean_url(thumbnail)
            if _FIRST_PAGE_RE.search(thumbnail):
                if "score_0.png" in thumbnail.lower():
                    # The extension replaces a PNG thumbnail with the linked
                    # SVG/PNG score image when one exists.
                    thumbnail_fallback = thumbnail
                else:
                    info.first_image = thumbnail

        composer = _json_composer_name(obj.get("composer"))
        if composer and not info.composer:
            info.composer = composer

        name = obj.get("name")
        if isinstance(name, str) and name.strip() and not info.name:
            info.name = name.strip()

        obj_url = obj.get("url")
        if isinstance(obj_url, str) and obj_url.strip() and not info.score_id:
            info.score_id = _last_path_segment(obj_url)
            if not info.url:
                info.url = obj_url

    if not info.name:
        info.name = next(iter(_iter_meta_values(collector.metas, "og:title")), "").strip()

    if not info.composer:
        info.composer = next(
            iter(_iter_meta_values(collector.metas, "musescore:composer")), ""
        ).strip()

    if not info.score_id:
        for prop in (
            "al:android:url",
            "al:ios:url",
            "twitter:app:url:iphone",
            "twitter:app:url:ipad",
            "twitter:app:url:googleplay",
            "og:url",
        ):
            value = next(iter(_iter_meta_values(collector.metas, prop)), "")
            if value:
                info.score_id = _last_path_segment(value)
                if info.score_id:
                    break

    if not info.first_image:
        info.first_image = _extract_first_image(html, collector.metas, collector.links)

    if not info.first_image:
        info.first_image = thumbnail_fallback

    info.pages = _count_pages(html, music_objects)

    return info