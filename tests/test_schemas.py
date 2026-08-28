"""Tests for MCPSchemaBaseModel.to_mcp_input_schema cleanup of optional/enum fields."""

from mcp_server_kalshi.kalshi_client.schemas import (
    CreateOrderRequest,
    EmptyRequest,
    GetMarketRequest,
    ListMarketsRequest,
)


def test_empty_request_schema():
    assert EmptyRequest.to_mcp_input_schema() == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }


def test_required_field_schema():
    schema = GetMarketRequest.to_mcp_input_schema()
    assert schema["required"] == ["ticker"]
    assert schema["properties"]["ticker"]["type"] == "string"
    assert "anyOf" not in schema["properties"]["ticker"]


def test_optional_fields_flattened():
    schema = ListMarketsRequest.to_mcp_input_schema()
    # Optional string field should be a plain typed prop, not anyOf/null.
    series = schema["properties"]["series_ticker"]
    assert "anyOf" not in series
    assert series["type"] == "string"
    assert schema["required"] == []
    # Enum literal should be inlined.
    status = schema["properties"]["status"]
    assert set(status["enum"]) == {"unopened", "open", "closed", "settled"}


def test_create_order_schema_required_and_enum():
    schema = CreateOrderRequest.to_mcp_input_schema()
    for field in ("ticker", "action", "side", "count", "limit_price"):
        assert field in schema["required"]
    assert "confirm" not in schema["required"]  # has a default
    assert set(schema["properties"]["action"]["enum"]) == {"buy", "sell"}
    assert set(schema["properties"]["side"]["enum"]) == {"yes", "no"}


def test_schema_with_enum_class_ref_and_no_description():
    from enum import Enum

    from pydantic import Field

    from mcp_server_kalshi.kalshi_client.schemas import MCPSchemaBaseModel

    class ColorEnum(str, Enum):
        RED = "red"
        BLUE = "blue"

    class SampleModel(MCPSchemaBaseModel):
        color: ColorEnum
        no_desc: str = Field(...)
        optional_no_desc: str | None = None
        optional_with_desc: str | None = Field(
            default=None, description="My description"
        )
        only_null: None = None
        multi_union: int | str | None = None

    schema = SampleModel.to_mcp_input_schema()
    assert schema["properties"]["color"]["type"] == "string"
    assert "red" in schema["properties"]["color"]["enum"]
    assert "description" not in schema["properties"]["no_desc"]
    assert schema["properties"]["optional_no_desc"]["type"] == "string"
    assert schema["properties"]["optional_with_desc"]["description"] == "My description"
    assert "only_null" in schema["properties"]
    assert "anyOf" in schema["properties"]["multi_union"]
