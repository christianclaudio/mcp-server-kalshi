"""Every tool, resource and prompt the server exposes, exercised through a real client.

House standard, mandatory in every server. Conformance runs the MCP suite's own
fixtures, so it cannot see this server's resources or prompts; unit tests that call
``mcp.read_resource()`` or ``mcp.render_prompt()`` skip the request handler that
serializes the result. This test talks to the production ``mcp`` instance over an
in-memory ``fastmcp.Client``, so the full ``tools/list``, ``resources/list``,
``resources/templates/list``, ``resources/read``, ``prompts/list`` and ``prompts/get``
paths run on the wire types a host receives. The test runs offline because none of its
calls reach the vendor API; any repo whose tools need a network mock should supply it
in its own ``conftest.py``.

The server has no factory or profiles, so the fixture is a single ``Client(mcp)``, run once with ``KALSHI_READONLY`` off and once on. The
checks are generic; a resource template or prompt added later needs at most an entry in
one of the two tables below:

* ``RESOURCE_TEMPLATE_URIS`` -- one concrete URI per resource template, keyed by the
  client-visible ``uriTemplate``. A template without an entry fails the test, so a new
  template is covered on purpose. Example: ``{"kalshi://markets/{ticker}":
  "kalshi://markets/EXAMPLE"}``.
* ``PROMPT_ARGUMENTS`` -- explicit arguments for a prompt, keyed by the client-visible
  prompt name. Without an entry the test fills only the required arguments, using the
  JSON schema FastMCP appends to each non-``str`` argument's description (a ``str``
  argument gets ``"example"``). Add an entry when a required argument needs a specific
  value, such as an enum member the prompt validates.

The server must list at least one tool. Each tool's ``inputSchema`` (and
``outputSchema`` when present) must be an object schema that is itself valid JSON Schema
(draft 2020-12); every failure is collected and reported in one message. The server has
no Tool Search or Code Mode, so there are no discovery-mode runs.

The server registers no resources, templates or prompts today, so those two checks pass
trivially until one is added.
"""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.client.transports import FastMCPTransport
from fastmcp.resources.template import match_uri_template
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError
from mcp.types import BlobResourceContents, PromptArgument, TextResourceContents, Tool

from mcp_server_kalshi.server import mcp, settings

RESOURCE_TEMPLATE_URIS: dict[str, str] = {}
PROMPT_ARGUMENTS: dict[str, dict[str, str]] = {}

SurfaceClient = Client[FastMCPTransport]

_SCHEMA_NOTE = re.compile(
    r"following JSON schema: (\{.*\})\. Encode non-string values as JSON\."
)
_SCALAR_EXAMPLES: dict[str, Any] = {
    "string": "example",
    "integer": 1,
    "number": 1,
    "boolean": False,
    "array": [],
    "object": {},
    "null": None,
}


@pytest.fixture(params=[False, True], ids=["default", "readonly"])
def surface_client(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> SurfaceClient:
    """An in-memory client on the production server, with KALSHI_READONLY off and on."""
    monkeypatch.setattr(settings, "KALSHI_READONLY", request.param)
    return Client(mcp)


def _example_for(schema: dict[str, Any]) -> Any:
    """Return a minimal value that satisfies a simple JSON schema."""
    if "enum" in schema:
        return schema["enum"][0]
    if "const" in schema:
        return schema["const"]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return _example_for(schema[key][0])
    kind = schema.get("type", "string")
    if isinstance(kind, list):
        kind = kind[0]
    return _SCALAR_EXAMPLES.get(kind, "example")


def _wire_value(argument: PromptArgument) -> str:
    """Return a minimal MCP prompt argument string (prompt arguments are strings on the wire)."""
    match = _SCHEMA_NOTE.search(argument.description or "")
    value = _example_for(json.loads(match.group(1))) if match else "example"
    return value if isinstance(value, str) else json.dumps(value)


def _has_content(item: TextResourceContents | BlobResourceContents) -> bool:
    if isinstance(item, TextResourceContents):
        return bool(item.text)
    return bool(item.blob)


def _schema_failures(tools: list[Tool]) -> list[str]:
    """Collect every missing name, non-object schema and invalid JSON Schema in ``tools``."""
    failures: list[str] = []
    for tool in tools:
        schema = tool.input_schema
        if (
            not tool.name
            or not isinstance(schema, dict)
            or schema.get("type") != "object"
        ):
            failures.append(f"{tool.name!r}: missing name or non-object input schema")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except SchemaError as exc:
            failures.append(f"{tool.name}: invalid input schema: {exc.message}")
        output = tool.output_schema
        if output is None:
            continue
        if not isinstance(output, dict) or output.get("type") != "object":
            failures.append(f"{tool.name}: non-object output schema")
            continue
        try:
            Draft202012Validator.check_schema(output)
        except SchemaError as exc:
            failures.append(f"{tool.name}: invalid output schema: {exc.message}")
    return failures


async def test_every_tool_lists_with_name_and_input_schema(
    surface_client: SurfaceClient,
) -> None:
    """``tools/list`` returns at least one tool, each with a name and valid object schemas."""
    async with surface_client as client:
        tools = await client.list_tools()
    assert tools, "the server lists no tools"
    failures = _schema_failures(tools)
    assert not failures, "tools/list failed:\n" + "\n".join(failures)


async def test_every_resource_reads_through_the_client(
    surface_client: SurfaceClient,
) -> None:
    """Every concrete resource, and one fixture URI per template, reads with content."""
    failures: list[str] = []
    async with surface_client as client:
        uris = [str(resource.uri) for resource in await client.list_resources()]
        for template in await client.list_resource_templates():
            fixture = RESOURCE_TEMPLATE_URIS.get(template.uri_template)
            if fixture is None:
                failures.append(
                    f"{template.uri_template}: no RESOURCE_TEMPLATE_URIS entry"
                )
            elif match_uri_template(fixture, template.uri_template) is None:
                failures.append(
                    f"{template.uri_template}: fixture {fixture} does not match"
                )
            else:
                uris.append(fixture)
        for uri in uris:
            try:
                contents = await client.read_resource(uri)
            except Exception as exc:
                failures.append(f"{uri}: {type(exc).__name__}: {exc}")
                continue
            if not contents or not all(_has_content(item) for item in contents):
                failures.append(f"{uri}: empty contents")
    assert not failures, "resources/read failed:\n" + "\n".join(failures)


async def test_every_prompt_renders_through_the_client(
    surface_client: SurfaceClient,
) -> None:
    """Every prompt renders with its required arguments filled from the declared schema."""
    failures: list[str] = []
    async with surface_client as client:
        for prompt in await client.list_prompts():
            arguments = PROMPT_ARGUMENTS.get(prompt.name)
            if arguments is None:
                arguments = {
                    argument.name: _wire_value(argument)
                    for argument in prompt.arguments or []
                    if argument.required
                }
            try:
                result = await client.get_prompt(prompt.name, arguments)
            except Exception as exc:
                failures.append(f"{prompt.name}: {type(exc).__name__}: {exc}")
                continue
            if not result.messages:
                failures.append(f"{prompt.name}: no messages")
    assert not failures, "prompts/get failed:\n" + "\n".join(failures)
