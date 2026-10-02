"""Download and extract Kalshi contract-terms / certification PDFs."""

import io
from typing import Any
from urllib.parse import urljoin

import httpx
from pypdf import PdfReader

from ..ssrf import UnsafeURLError, validate_pdf_url

# Redirect hops after the initial response. Each hop is allowlisted again.
MAX_PDF_REDIRECTS = 3


async def _download_pdf(url: str) -> tuple[str, bytes]:
    """GET a PDF without env proxies, re-checking every redirect target."""
    current = validate_pdf_url(url)
    async with httpx.AsyncClient(
        timeout=30,
        follow_redirects=False,
        trust_env=False,
    ) as client:
        for hop in range(MAX_PDF_REDIRECTS + 1):
            response = await client.get(current)
            if response.status_code < 300 or response.status_code >= 400:
                response.raise_for_status()
                return current, response.content
            if hop == MAX_PDF_REDIRECTS:
                raise UnsafeURLError("Too many redirects while fetching a rules PDF")
            raw_location = response.headers.get("location")
            location = raw_location.strip() if isinstance(raw_location, str) else ""
            if not location:
                raise UnsafeURLError("Redirect is missing a Location header")
            current = validate_pdf_url(urljoin(current, location))
    raise UnsafeURLError("redirect loop")  # pragma: no cover


async def fetch_pdf_text(url: str, max_chars: int = 40000) -> dict[str, Any]:
    """Download a Kalshi rules PDF and extract its text.

    ``url`` must be http(s) on an allowlisted Kalshi document host (the assets CDN
    or the public-docs buckets). Private, link-local, metadata, and other hosts are
    rejected before the GET. Redirects are followed only when the target passes the
    same check. Text is truncated to ``max_chars`` with a flag so callers know to
    page further if needed.
    """
    final_url, data = await _download_pdf(url)

    reader = PdfReader(io.BytesIO(data))
    pages = [page.extract_text() or "" for page in reader.pages]
    full_text = "\n\n".join(pages).strip()
    truncated = len(full_text) > max_chars

    return {
        "url": final_url,
        "page_count": len(reader.pages),
        "char_count": len(full_text),
        "truncated": truncated,
        "text": full_text[:max_chars],
    }
