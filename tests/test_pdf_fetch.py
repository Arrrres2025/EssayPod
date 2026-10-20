from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pdf_fetch import (
    download_pdf_from_url,
    extract_pdf_candidates,
    is_probably_pdf,
    normalize_pdf_url,
)


class PdfFetchTests(unittest.TestCase):
    def test_normalizes_tracking_urls_and_known_paper_pages(self) -> None:
        self.assertEqual(
            normalize_pdf_url(
                "https://arxiv.org/abs/2412.15529?utm_source=chatgpt.com"
            ),
            "https://arxiv.org/pdf/2412.15529.pdf",
        )
        self.assertEqual(
            normalize_pdf_url(
                "https://aclanthology.org/2026.acl-long.447/?utm_source=chatgpt.com"
            ),
            "https://aclanthology.org/2026.acl-long.447.pdf",
        )
        self.assertEqual(
            normalize_pdf_url(
                "https://aclanthology.org/2026.acl-long.447.pdf"
            ),
            "https://aclanthology.org/2026.acl-long.447.pdf",
        )

    def test_strict_pdf_signature(self) -> None:
        self.assertTrue(is_probably_pdf(b"%PDF-1.7\nbody"))
        self.assertTrue(is_probably_pdf(b"\ncomment\n%PDF-1.4\nbody"))
        self.assertFalse(is_probably_pdf(b"<html>download page</html>"))
        self.assertFalse(is_probably_pdf(b"arbitrary binary"))

    def test_extracts_metadata_and_download_links_in_priority_order(self) -> None:
        html = b"""
        <html><head>
          <meta name="citation_pdf_url" content="/paper/main.pdf">
          <link rel="alternate" type="application/pdf" href="/paper/alternate.pdf">
        </head><body>
          <a href="/paper/supplement.pdf">Supplementary information</a>
          <a href="downloads/fulltext">Download PDF</a>
        </body></html>
        """
        urls = extract_pdf_candidates("https://journal.example/article/42", html)
        self.assertEqual(urls[0], "https://journal.example/paper/main.pdf")
        self.assertIn("https://journal.example/article/downloads/fulltext", urls)
        self.assertGreater(
            urls.index("https://journal.example/article/downloads/fulltext"),
            urls.index("https://journal.example/paper/alternate.pdf"),
        )

    def test_article_page_resolves_and_downloads_pdf(self) -> None:
        page_url = "https://journal.example/articles/42"
        pdf_url = "https://journal.example/files/42.pdf"
        page = b'<meta name="citation_pdf_url" content="/files/42.pdf">'
        pdf = b"%PDF-1.7\nmock"

        def fake_download(url, timeout, *, accept):
            return page if url == page_url else pdf

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "input.pdf"
            stages = []
            with patch("pdf_fetch._download", side_effect=fake_download):
                selected = download_pdf_from_url(
                    page_url, output, on_stage=stages.append
                )
            self.assertEqual(selected, pdf_url)
            self.assertEqual(output.read_bytes(), pdf)
            self.assertEqual(stages, ["resolving", "downloading"])

    def test_rejects_page_without_pdf_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            with patch(
                "pdf_fetch._download", return_value=b"<html><body>Article</body></html>"
            ):
                with self.assertRaisesRegex(ValueError, "does not expose"):
                    download_pdf_from_url(
                        "https://journal.example/articles/42",
                        Path(directory) / "input.pdf",
                    )


if __name__ == "__main__":
    unittest.main()
