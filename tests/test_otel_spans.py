"""Recorded OpenTelemetry spans of a failing tool hold no secret.

FastMCP traces every ``tools/call`` with OpenTelemetry. A tool that raises gets the
exception recorded on its span (``exception.message`` and ``exception.stacktrace``), and a
chain-walking exporter would bring back an unredacted original carried on ``__cause__`` /
``__context__``. Kalshi tools return the failure as an ``isError`` result with the
``redact_message`` text instead of raising, so nothing (and no chain) reaches the span.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from conftest import FakeClient
from fastmcp import Client
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.util._once import Once

from mcp_server_kalshi import server

EXPORTER = InMemorySpanExporter()


@pytest.fixture(autouse=True)
def _provider() -> Iterator[None]:
    """Install an SDK provider for this test only, then restore the global one.

    ``trace.set_tracer_provider`` is set-once per process, so the fixture swaps the API's
    module globals and puts the previous provider and set-once guard back afterwards,
    keeping other tests' tracing state untouched.
    """
    saved = (trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE)
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(EXPORTER))
    trace._TRACER_PROVIDER_SET_ONCE = Once()
    trace.set_tracer_provider(provider)
    try:
        yield
    finally:
        provider.shutdown()
        trace._TRACER_PROVIDER, trace._TRACER_PROVIDER_SET_ONCE = saved


def _span_text(span: Any) -> str:
    parts = [span.name, str(span.status.description or "")]
    parts += [f"{k}={v}" for k, v in (span.attributes or {}).items()]
    for event in span.events:
        parts.append(event.name)
        parts += [f"{k}={v}" for k, v in (event.attributes or {}).items()]
    return "\n".join(parts)


async def test_failing_tool_spans_hold_no_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inner, outer, number = (
        "hunter2-inner-otel",
        "sk-outer-otel-secret-12345",
        "987650001",
    )

    def _chained() -> Exception:
        try:
            raise RuntimeError(f"pool failed password={inner}")
        except RuntimeError as exc:
            err = ValueError(
                f'upstream rejected Authorization: Bearer {outer} {{"api_key": {number}}}'
            )
            err.__cause__ = exc
            return err

    monkeypatch.setattr(server, "kalshi_client", FakeClient(get_balance=_chained()))
    EXPORTER.clear()
    async with Client(server.mcp) as client:
        result = await client.call_tool("get_balance", {}, raise_on_error=False)
    assert result.is_error
    reply = result.content[0].text  # type: ignore[union-attr]
    assert "[REDACTED]" in reply

    spans = EXPORTER.get_finished_spans()
    assert spans, "FastMCP recorded no spans"
    tool_spans = [s for s in spans if "get_balance" in _span_text(s)]
    assert tool_spans, [s.name for s in spans]
    text = "\n".join(_span_text(s) for s in spans)
    for secret in (inner, outer, number):
        assert secret not in text
        assert secret not in reply
