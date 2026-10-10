# 🛡️ Security Policy & Best Practices

> **Disclaimer:** `mcp-server-kalshi` is an independent open-source community project and is **not** affiliated with, endorsed by, or supported by Kalshi Inc. *"Kalshi"* is a registered trademark of Kalshi Inc.

---

## 🔒 Supported Versions

Only the latest release receives security fixes. Upgrade to the newest version on [Releases](https://github.com/christianclaudio/mcp-server-kalshi/releases).

---

## 🚨 Reporting a Vulnerability

**Please do NOT open public issues for security vulnerabilities.**

Report security vulnerabilities privately via [GitHub Security Advisories](https://github.com/christianclaudio/mcp-server-kalshi/security/advisories/new) or directly to the repository maintainers.

You will receive an acknowledgement within 5 business days and a status update within 15 business days. If a patch is warranted, we will publish a fix and credit you in the release notes!

---

## 🔐 Operator & Trader Security Guidelines

This MCP server executes trades and manages financial balances on the Kalshi exchange. Please review the following safety measures:

### 1. Environment Isolation (Demo vs. Prod)
- **Safety by Default:** The server connects to Kalshi's **DEMO sandbox** (`https://demo-api.kalshi.co/trade-api/v2`) unless `KALSHI_ENV=prod` is explicitly configured.
- Always test agent prompt strategies, automated scripts, and algorithmic models in `demo` mode before routing real capital.

### 2. Signing Credentials (RSA or Ed25519)
- Authenticated requests are signed with your private key: RSA-PSS (SHA-256) or Ed25519. The server picks the scheme from the parsed key type.
- **Never commit `.pem` private key files or API key IDs to version control.**
- Place private keys in standard restricted paths (e.g. `chmod 600 ~/.kalshi/rsa.pem`).
- Exception text returned by the server is scrubbed by `redact_message()` (`errors.py`): credential values are masked whole, and JSON inside a message is redacted value by value.

### 3. Read-Only Mode (`KALSHI_READONLY=1`)
When connecting this server to autonomous agents, research workflows, or shared chat assistants, run with:
```bash
KALSHI_READONLY=1 uvx --from git+https://github.com/christianclaudio/mcp-server-kalshi mcp-server-kalshi
```
This strictly limits the server to **29 read-only discovery, research, and portfolio inspection tools**, removing all order creation, cancellation, and amendment endpoints from the agent context.

### 4. Safety Gating (`confirm: bool = False`)
Mutating order tools (`create_order`, `amend_order`, `decrease_order`, `cancel_order`, `batch_create_orders`, `batch_cancel_orders`, `cancel_order_group`) return a simulation preview unless called with `confirm=True`. This prevents unintended order placement or cancellation from hallucinated model outputs.

---

## 🛡️ Summary of Deployment Postures

| Use Case | Recommended Configuration |
| :--- | :--- |
| **Agent Exploration & Research** | `KALSHI_ENV=demo` + `KALSHI_READONLY=1` |
| **Model Backtesting & Paper Trading** | `KALSHI_ENV=demo` (with Demo API Key) |
| **Production Live Execution** | `KALSHI_ENV=prod` (with `confirm=True` gating) |
