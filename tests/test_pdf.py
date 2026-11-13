from __future__ import annotations

import io
import tempfile
import unittest
from pathlib import Path

from musescore_downloader.pdf import PdfError, build_pdf, is_svg


def _png_bytes() -> bytes:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover
        raise unittest.SkipTest("Pillow is not installed") from exc

    buffer = io.BytesIO()
    Image.new("RGB", (600, 800), "white").save(buffer, "PNG")
    return buffer.getvalue()


class PdfTests(unittest.TestCase):
    def test_is_svg(self) -> None:
        self.assertTrue(is_svg(b'<?xml version="1.0"?><svg></svg>'))
        self.assertTrue(is_svg(b"  <svg></svg>"))
        self.assertFalse(is_svg(b"\x89PNG\r\n"))

    def test_builds_raster_pdf(self) -> None:
        try:
            import reportlab  # noqa: F401
        except ImportError as exc:  # pragma: no cover
            raise unittest.SkipTest("ReportLab is not installed") from exc

        with tempfile.TemporaryDirectory() as directory:
            destination = Path(directory) / "sheet.pdf"
            result = build_pdf(
                [_png_bytes(), _png_bytes()],
                destination,
                title="Test",
                author="Composer",
            )

            self.assertEqual(result, destination)
            self.assertTrue(destination.read_bytes().startswith(b"%PDF-"))

    def test_rejects_empty_pages(self) -> None:
        with self.assertRaises(PdfError):
            build_pdf([], "unused.pdf")


if __name__ == "__main__":
    unittest.main()