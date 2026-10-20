from __future__ import annotations

import http.client
import re
import time
import urllib.parse
import urllib.request
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable

_ARXIV_ABS_RE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/abs/(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)
_ARXIV_PDF_RE = re.compile(
    r"^https?://(?:www\.)?arxiv\.org/pdf/(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)(?:\.pdf)?/?$",
    re.IGNORECASE,
)
_ACL_PAGE_RE = re.compile(
    r"^https?://(?:www\.)?aclanthology\.org/(?P<id>[^/?#]+)/?$",
    re.IGNORECASE,
)
_DEFAULT_TIMEOUT = 45
_DOWNLOAD_ATTEMPTS = 2
_USER_AGENT = "EssayPod/1.0 (+https://github.com/local/essaypod)"
_MAX_PDF_CANDIDATES = 12
_TRACKING_QUERY_KEYS = {
    "fbclid",
    "gclid",
    "mc_cid",
    "mc_eid",
    "utm_campaign",
    "utm_content",
    "utm_medium",
    "utm_source",
    "utm_term",
}
StageCallback = Callable[[str], None]


class PaperPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.candidates: list[tuple[int, str]] = []
        self._anchor_href = ""
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.lower(): (value or "").strip() for key, value in attrs}
        tag = tag.lower()
        if tag == "meta":
            key = (values.get("name") or values.get("property") or "").lower()
            content = values.get("content", "")
            if content and key in {
                "citation_pdf_url",
                "citation_fulltext_html_url",
                "dc.identifier",
                "eprints.document_url",
            } and (".pdf" in content.lower() or key == "citation_pdf_url"):
                self.candidates.append((100, content))
        elif tag == "link":
            href = values.get("href", "")
            rel = values.get("rel", "").lower()
            media_type = values.get("type", "").lower()
            if href and (media_type == "application/pdf" or "pdf" in rel):
                self.candidates.append((90, href))
        elif tag == "a":
            self._anchor_href = values.get("href", "")
            self._anchor_text = []

    def handle_data(self, data: str) -> None:
        if self._anchor_href:
            self._anchor_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or not self._anchor_href:
            return
        href = self._anchor_href
        text = " ".join(self._anchor_text).strip().lower()
        href_lower = href.lower()
        if href_lower.endswith(".pdf") or ".pdf?" in href_lower:
            score = 80
            if any(token in text for token in ("download pdf", "view pdf", "full text pdf", "pdf 下载", "下载 pdf")):
                score = 88
            if any(token in text for token in ("supplement", "supporting", "peer review", "reporting summary")):
                score -= 35
            self.candidates.append((score, href))
        elif any(token in text for token in ("download pdf", "view pdf", "full text pdf", "pdf 下载", "下载 pdf")):
            self.candidates.append((70, href))
        self._anchor_href = ""
        self._anchor_text = []


def normalize_pdf_url(url: str) -> str:
    raw = url.strip()
    if not raw:
        raise ValueError("PDF URL is empty")

    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("Please provide a valid http(s) URL")

    query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    clean_query = urllib.parse.urlencode(
        [(key, value) for key, value in query if key.lower() not in _TRACKING_QUERY_KEYS]
    )
    normalized = urllib.parse.urlunparse(
        parsed._replace(query=clean_query, fragment="")
    )

    abs_match = _ARXIV_ABS_RE.match(normalized)
    if abs_match:
        paper_id = abs_match.group("id")
        return f"https://arxiv.org/pdf/{paper_id}.pdf"

    pdf_match = _ARXIV_PDF_RE.match(normalized)
    if pdf_match:
        paper_id = pdf_match.group("id")
        return f"https://arxiv.org/pdf/{paper_id}.pdf"

    acl_match = _ACL_PAGE_RE.match(normalized)
    if acl_match:
        acl_id = acl_match.group("id")
        if acl_id.lower().endswith(".pdf"):
            return normalized
        return f"https://aclanthology.org/{acl_id}.pdf"

    return normalized


def filename_from_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    name = Path(parsed.path).name
    if name.lower().endswith(".pdf"):
        return name
    abs_match = _ARXIV_ABS_RE.match(url) or _ARXIV_PDF_RE.match(url)
    if abs_match:
        return f"{abs_match.group('id')}.pdf"
    stem = Path(parsed.path).stem or "paper"
    return f"{stem}.pdf"


def is_probably_pdf(content: bytes) -> bool:
    return bool(content and b"%PDF-" in content[:1024])


def extract_pdf_candidates(page_url: str, content: bytes) -> list[str]:
    text = content.decode("utf-8", errors="replace")
    parser = PaperPageParser()
    parser.feed(text)

    # JSON-LD and inline metadata occasionally expose the PDF only as a string.
    for match in re.finditer(
        r"""(?:"(?:contentUrl|encoding|url|pdfUrl|downloadUrl)"\s*:\s*)?["']([^"']+\.pdf(?:\?[^"']*)?)["']""",
        text,
        re.IGNORECASE,
    ):
        parser.candidates.append((65, match.group(1).replace("\\/", "/")))

    resolved: list[str] = []
    seen: set[str] = set()
    for _, candidate in sorted(parser.candidates, key=lambda item: item[0], reverse=True):
        absolute = urllib.parse.urljoin(page_url, candidate.strip())
        parsed = urllib.parse.urlparse(absolute)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        clean = urllib.parse.urlunparse(parsed._replace(fragment=""))
        if clean in seen:
            continue
        seen.add(clean)
        resolved.append(clean)
        if len(resolved) >= _MAX_PDF_CANDIDATES:
            break
    return resolved


def _download(url: str, timeout: int, *, accept: str) -> bytes:
    request = urllib.request.Request(
        url,
        method="GET",
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": accept,
            "Referer": url,
        },
    )
    last_error: BaseException | None = None
    for attempt in range(_DOWNLOAD_ATTEMPTS):
        try:
            opener = (
                urllib.request.build_opener()
                if attempt == 0
                else urllib.request.build_opener(urllib.request.ProxyHandler({}))
            )
            with opener.open(request, timeout=timeout) as response:
                return response.read()
        except urllib.error.HTTPError:
            raise
        except (
            urllib.error.URLError,
            http.client.RemoteDisconnected,
            ConnectionResetError,
            TimeoutError,
            OSError,
        ) as exc:
            last_error = exc
            if attempt + 1 < _DOWNLOAD_ATTEMPTS:
                time.sleep(0.75)
    if last_error:
        raise last_error
    raise RuntimeError("PDF download failed without an exception")


def download_pdf_from_url(
    url: str,
    out_path: Path,
    *,
    timeout: int = _DEFAULT_TIMEOUT,
    on_stage: StageCallback | None = None,
) -> str:
    fetch_url = normalize_pdf_url(url)
    if on_stage:
        on_stage(
            "downloading"
            if urllib.parse.urlparse(fetch_url).path.lower().endswith(".pdf")
            else "resolving"
        )
    content = _download(fetch_url, timeout, accept="application/pdf,text/html;q=0.9,*/*;q=0.8")
    selected_url = fetch_url
    if not is_probably_pdf(content):
        candidates = extract_pdf_candidates(fetch_url, content)
        if not candidates:
            raise ValueError("The page does not expose a downloadable PDF link")
        if on_stage:
            on_stage("downloading")
        selected_content = None
        errors: list[str] = []
        for candidate in candidates:
            try:
                candidate_content = _download(candidate, timeout, accept="application/pdf,*/*;q=0.8")
            except Exception as exc:
                errors.append(f"{candidate}: {exc}")
                continue
            if is_probably_pdf(candidate_content):
                selected_url = candidate
                selected_content = candidate_content
                break
            errors.append(f"{candidate}: response is not a PDF")
        if selected_content is None:
            detail = errors[0] if errors else "no valid PDF candidates"
            raise ValueError(f"Found PDF links, but none could be downloaded ({detail})")
        content = selected_content
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(content)
    return selected_url
