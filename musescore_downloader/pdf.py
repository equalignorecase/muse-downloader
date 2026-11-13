from __future__ import annotations

import io
import re
from pathlib import Path

from .utils import Logger

A4_WIDTH = 595.2755905511812
A4_HEIGHT = 841.8897637795277

_SVG_START_RE = re.compile(rb"^\s*(?:<\?xml|<svg)", re.IGNORECASE)
_SVG_WIDTH_RE = re.compile(r'<svg\b[^>]*\bwidth=["\']([\d.]+)[^"\']*["\']', re.IGNORECASE)
_SVG_HEIGHT_RE = re.compile(r'<svg\b[^>]*\bheight=["\']([\d.]+)[^"\']*["\']', re.IGNORECASE)
_SVG_VIEWBOX_RE = re.compile(r'\bviewBox=["\']([^"\']+)["\']', re.IGNORECASE)


class PdfError(RuntimeError):
    pass


def is_svg(data: bytes) -> bool:
    return _SVG_START_RE.match(data or b"") is not None


def _svg_dimensions(data: bytes) -> tuple[float, float] | None:
    text = data.decode("utf-8", errors="ignore")

    viewbox = _SVG_VIEWBOX_RE.search(text)
    if viewbox:
        parts = re.split(r"[,\s]+", viewbox.group(1).strip())
        if len(parts) == 4:
            try:
                width, height = float(parts[2]), float(parts[3])
                if width > 0 and height > 0:
                    return width, height
            except ValueError:
                pass

    width_match = _SVG_WIDTH_RE.search(text)
    height_match = _SVG_HEIGHT_RE.search(text)
    if width_match and height_match:
        try:
            width, height = float(width_match.group(1)), float(height_match.group(1))
            if width > 0 and height > 0:
                return width, height
        except ValueError:
            pass

    return None


def _image_dimensions(data: bytes) -> tuple[float, float] | None:
    if is_svg(data):
        return _svg_dimensions(data)

    try:
        from PIL import Image

        with Image.open(io.BytesIO(data)) as image:
            return float(image.width), float(image.height)
    except Exception:
        return None


def _ensure_viewbox(data: bytes) -> bytes:
    """Add a viewBox when the SVG omitted one (extension parity)."""
    dimensions = _svg_dimensions(data)
    if not dimensions:
        return data

    text = data.decode("utf-8", errors="ignore")
    if _SVG_VIEWBOX_RE.search(text):
        return data

    width, height = dimensions
    text = re.sub(
        r"<svg\b",
        f'<svg viewBox="0 0 {width:g} {height:g}"',
        text,
        count=1,
        flags=re.IGNORECASE,
    )
    return text.encode("utf-8")


def build_pdf(
    pages: list[bytes],
    output_path: str | Path,
    *,
    title: str = "",
    author: str = "",
    logger: Logger | None = None,
) -> Path:
    """Write the downloaded pages into a single A4 PDF.

    Equivalent to ``generatePDF`` in ``src/content/content.js`` but implemented
    with ReportLab instead of pdfmake.
    """
    log = logger or Logger()

    try:
        from reportlab.graphics import renderPDF
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.utils import ImageReader
        from reportlab.pdfgen import canvas
        from svglib.svglib import svg2rlg
    except ImportError as exc:
        raise PdfError(
            "PDF support requires extra packages. Install them with:\n"
            "  pip install 'musescore-downloader[pdf]'"
        ) from exc

    if not pages:
        raise PdfError("No pages were downloaded")

    first_size = _image_dimensions(pages[0])
    landscape = bool(first_size and first_size[0] > first_size[1])

    page_width, page_height = A4
    if landscape:
        page_width, page_height = page_height, page_width

    destination = Path(output_path).expanduser()
    destination.parent.mkdir(parents=True, exist_ok=True)

    pdf = canvas.Canvas(str(destination), pagesize=(page_width, page_height))
    pdf.setTitle(title or destination.stem)
    pdf.setAuthor(author or "")

    try:
        for page_number, page_data in enumerate(pages, start=1):
            if not page_data:
                log.debug(f"Skipping empty page {page_number}")
                continue

            if is_svg(page_data):
                drawing = svg2rlg(io.BytesIO(_ensure_viewbox(page_data)))
                if drawing is None or not drawing.width or not drawing.height:
                    raise PdfError(f"Could not parse SVG on page {page_number}")

                scale_x = page_width / drawing.width
                scale_y = page_height / drawing.height

                pdf.saveState()
                pdf.scale(scale_x, scale_y)
                renderPDF.draw(drawing, pdf, 0, 0)
                pdf.restoreState()
            else:
                image = ImageReader(io.BytesIO(page_data))
                pdf.drawImage(
                    image,
                    0,
                    0,
                    width=page_width,
                    height=page_height,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )

            pdf.showPage()
    except PdfError:
        raise
    except Exception as exc:
        raise PdfError(f"Failed to build PDF: {exc}") from exc

    pdf.save()
    return destination