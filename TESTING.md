# 🧪 Testing & Verification Guide

This document details the test harness, mock fixtures, assertion conventions, and verification workflows for `mcp-server-kalshi`.

---

## 🎯 Coverage & Safety Invariants

- **100.0% Target Coverage**: Every statement and branch in `src/mcp_server_kalshi/` is verified under `pytest --cov --cov-fail-under=100`.
- **Zero Real-Money Risk**: All unit test suites run offline against deterministic mock HTTP transports without making live external network calls.
- **Strict Typing**: All tests and implementation code are validated under `mypy --strict`.

---

## 🚀 Running Tests Locally

### Quick Execution
```bash
uv run pytest -v
```

### Full Coverage with Missing Lines
```bash
uv run pytest --cov=src/mcp_server_kalshi --cov-report=term-missing --cov-fail-under=100
```

---

## 📁 Test Suite Structure

| File | Purpose & Scope |
| :--- | :--- |
| [`tests/test_auth.py`](tests/test_auth.py) | RSA key loading, PSS SHA-256 header generation, signature verification. |
| [`tests/test_base_client.py`](tests/test_base_client.py) | HTTP lifecycle, header injection, async context managers, error trapping. |
| [`tests/test_client_endpoints.py`](tests/test_client_endpoints.py) | Wire tests for all 32 Kalshi API client methods against mock transports. |
| [`tests/test_config.py`](tests/test_config.py) | Environment detection (`demo` vs `prod`), base URL derivation, caching. |
| [`tests/test_errors.py`](tests/test_errors.py) | Credential scrubbing (`redact_secrets`) for RSA keys and tokens, and `KalshiAPIError`. |
| [`tests/test_redaction_house.py`](tests/test_redaction_house.py) | Template v1.6.0 house redaction rules (whole-value masking), ported with the template's tests. |
| [`tests/test_redaction_probes.py`](tests/test_redaction_probes.py) | Kalshi redaction extras, timing probes, and real `tools/call` error paths with a number, list and dict under credential keys. |
| [`tests/test_http_auth.py`](tests/test_http_auth.py) | `KALSHI_MCP_AUTH_TOKEN` bearer auth on every HTTP entry point and the non-localhost bind policy. |
| [`tests/test_handlers.py`](tests/test_handlers.py) | Tool registry completeness and handler dispatch. |
| [`tests/test_orders.py`](tests/test_orders.py) | Kalshi V2 YES-leg order pricing translation (cents $\to$ book ask/bid). |
| [`tests/test_pdf.py`](tests/test_pdf.py) | Rules PDF downloading, fallback parsing, text extraction. |
| [`tests/test_schemas.py`](tests/test_schemas.py) | Pydantic $\to$ MCP schema transformation and enum `$ref` inlining. |
| [`tests/test_server_comprehensive.py`](tests/test_server_comprehensive.py) | End-to-end tool execution, `confirm: bool` simulation previews, and readonly mode. |

---

## 🛡️ Verification Scripts

```bash
# Verify all 36 MCP tool contracts and MCP 2.0 annotations
uv run python scripts/check_tool_contract.py

# Verify parity against live Kalshi OpenAPI documentation
uv run python scripts/check_openapi_drift.py

# Packaged stdio smoke test (local wheel or git; this fork is not on public PyPI)
uv run python -m build
uv run python scripts/smoke_test_uvx.py -- uvx --from dist/mcp_server_kalshi-*.whl mcp-server-kalshi
```
