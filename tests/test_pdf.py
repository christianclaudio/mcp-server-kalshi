"""Tests for fetch_pdf_text: download + extract + truncation, without network or real PDFs.

The httpx download and pypdf reader are both stubbed so extracted text is deterministic.
"""

import httpx
import pytest

from mcp_server_kalshi.kalshi_client import pdf

_PDF = "https://assets.kalshi.com/contract_terms/x.pdf"


class _FakeResponse:
    def __init__(self, status_code: int, content: bytes, headers: dict | None = None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                "error",
                request=httpx.Request("GET", _PDF),
                response=None,  # type: ignore[arg-type]
            )


class _FakeAsyncClient:
    """Async-context-manager stand-in for httpx.AsyncClient with a canned GET response."""

    response = _FakeResponse(200, b"%PDF-bytes")
    scripted: list[_FakeResponse] | None = None
    urls: list[str] = []
    init_kwargs: dict = {}

    def __init__(self, *args, **kwargs):
        _FakeAsyncClient.init_kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url):
        _FakeAsyncClient.urls.append(url)
        if _FakeAsyncClient.scripted is not None:
            return _FakeAsyncClient.scripted[len(_FakeAsyncClient.urls) - 1]
        return self.response


class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def extract_text(self) -> str:
        return self._text


class _FakeReader:
    pages_text = ["A" * 50, "B" * 50]

    def __init__(self, stream):
        self.pages = [_FakePage(t) for t in self.pages_text]


@pytest.fixture
def stub_pdf(monkeypatch):
    """Patch the download client and the PDF reader; return a knob to set the HTTP status."""
    monkeypatch.setattr(pdf.httpx, "AsyncClient", _FakeAsyncClient)
    monkeypatch.setattr(pdf, "PdfReader", _FakeReader)

    def set_status(code: int):
        _FakeAsyncClient.response = _FakeResponse(code, b"%PDF-bytes")

    set_status(200)
    _FakeAsyncClient.scripted = None
    _FakeAsyncClient.urls = []
    yield set_status
    _FakeAsyncClient.response = _FakeResponse(200, b"%PDF-bytes")
    _FakeAsyncClient.scripted = None
    _FakeAsyncClient.urls = []


async def test_extracts_text_and_metadata(stub_pdf):
    result = await pdf.fetch_pdf_text(_PDF, max_chars=1000)

    assert result["url"] == _PDF
    assert _FakeAsyncClient.init_kwargs["follow_redirects"] is False
    assert _FakeAsyncClient.init_kwargs["trust_env"] is False
    assert result["page_count"] == 2
    assert result["char_count"] == 102  # 50 + "\n\n" + 50
    assert result["truncated"] is False
    assert result["text"] == "A" * 50 + "\n\n" + "B" * 50


async def test_truncates_to_max_chars_and_flags_it(stub_pdf):
    result = await pdf.fetch_pdf_text(_PDF, max_chars=10)

    assert result["truncated"] is True
    assert result["text"] == "A" * 10  # sliced to max_chars
    assert result["char_count"] == 102  # full length still reported


async def test_raises_on_http_error(stub_pdf):
    stub_pdf(404)
    with pytest.raises(httpx.HTTPStatusError):
        await pdf.fetch_pdf_text("https://assets.kalshi.com/contract_terms/missing.pdf")


async def test_follows_relative_redirect_on_allowlisted_host(stub_pdf):
    _FakeAsyncClient.scripted = [
        _FakeResponse(302, b"", headers={"location": "/contract_terms/b.pdf"}),
        _FakeResponse(200, b"%PDF-bytes"),
    ]
    result = await pdf.fetch_pdf_text(_PDF)
    assert result["url"] == "https://assets.kalshi.com/contract_terms/b.pdf"
    assert result["text"].startswith("A")
    assert _FakeAsyncClient.urls == [
        _PDF,
        "https://assets.kalshi.com/contract_terms/b.pdf",
    ]


async def test_rejects_redirect_off_allowlist(stub_pdf):
    _FakeAsyncClient.scripted = [
        _FakeResponse(
            302,
            b"",
            headers={"location": "http://169.254.169.254/latest/meta-data/"},
        )
    ]
    with pytest.raises(pdf.UnsafeURLError, match="blocked"):
        await pdf.fetch_pdf_text(_PDF)
    assert _FakeAsyncClient.urls == [_PDF]


async def test_rejects_redirect_with_missing_or_blank_location(stub_pdf):
    _FakeAsyncClient.scripted = [_FakeResponse(302, b"", headers={})]
    with pytest.raises(pdf.UnsafeURLError, match="Location"):
        await pdf.fetch_pdf_text(_PDF)

    _FakeAsyncClient.urls = []
    _FakeAsyncClient.scripted = [
        _FakeResponse(302, b"", headers={"location": "   "}),
    ]
    with pytest.raises(pdf.UnsafeURLError, match="Location"):
        await pdf.fetch_pdf_text(_PDF)


async def test_rejects_too_many_redirects(stub_pdf):
    _FakeAsyncClient.scripted = [
        _FakeResponse(302, b"", headers={"location": "/contract_terms/b.pdf"})
        for _ in range(pdf.MAX_PDF_REDIRECTS + 1)
    ]
    with pytest.raises(pdf.UnsafeURLError, match="Too many redirects"):
        await pdf.fetch_pdf_text(_PDF)
    assert len(_FakeAsyncClient.urls) == pdf.MAX_PDF_REDIRECTS + 1


async def test_rejects_non_http_and_private_urls_before_get(monkeypatch):
    created = False

    class _Boom:
        def __init__(self, *args, **kwargs):
            nonlocal created
            created = True

    monkeypatch.setattr(pdf.httpx, "AsyncClient", _Boom)
    with pytest.raises(pdf.UnsafeURLError, match="scheme"):
        await pdf.fetch_pdf_text("file:///etc/passwd")
    with pytest.raises(pdf.UnsafeURLError, match="blocked"):
        await pdf.fetch_pdf_text("http://127.0.0.1/latest/meta-data/")
    assert created is False
