"""Tests for MCPSchemaBaseModel.to_mcp_input_schema cleanup of optional/enum fields."""

import pytest
from pydantic import ValidationError

from mcp_server_kalshi.kalshi_client.schemas import (
    CreateOrderRequest,
    EmptyRequest,
    GetFillsRequest,
    GetMarketRequest,
    GetMarketTradesRequest,
    GetMilestonesRequest,
    GetPositionsRequest,
    GetSettlementsRequest,
    ListEventsRequest,
    ListMarketsRequest,
    ListMultivariateCollectionsRequest,
    ListOrderGroupsRequest,
    ListOrdersRequest,
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
    assert set(status["enum"]) == {"unopened", "open", "paused", "closed", "settled"}


def test_create_order_schema_required_and_enum():
    schema = CreateOrderRequest.to_mcp_input_schema()
    for field in ("ticker", "action", "side", "count", "limit_price"):
        assert field in schema["required"]
    assert "confirm" not in schema["required"]  # has a default
    assert set(schema["properties"]["action"]["enum"]) == {"buy", "sell"}
    assert set(schema["properties"]["side"]["enum"]) == {"yes", "no"}


def test_schema_with_enum_class_ref_and_no_description():
    from enum import StrEnum

    from pydantic import Field

    from mcp_server_kalshi.kalshi_client.schemas import MCPSchemaBaseModel

    class ColorEnum(StrEnum):
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


def test_batch_requests_have_no_fixed_size_cap() -> None:
    """Kalshi scales batch size with the API tier, so the schema sets no fixed cap.

    https://docs.kalshi.com/api-reference/orders/batch-create-orders-v2
    """
    from mcp_server_kalshi.kalshi_client.schemas import (
        BatchCancelOrdersRequest,
        BatchCreateOrdersRequest,
    )

    item = {
        "ticker": "T",
        "action": "buy",
        "side": "yes",
        "count": 1,
        "limit_price": 50,
    }
    assert len(BatchCreateOrdersRequest(orders=[item] * 25).orders) == 25
    assert len(BatchCancelOrdersRequest(order_ids=["o"] * 25).order_ids) == 25
    for model in (BatchCreateOrdersRequest, BatchCancelOrdersRequest):
        props = model.model_json_schema()["properties"]
        assert all("maxItems" not in p for p in props.values())


# ---- Page-size bounds and status enums from Kalshi OpenAPI v3.34.0 -------------------
@pytest.mark.parametrize(
    ("model", "minimum", "maximum"),
    [
        (ListMarketsRequest, 0, 1000),  # GET /markets
        (GetMarketTradesRequest, 0, 1000),  # GET /markets/trades
        (ListEventsRequest, 1, 200),  # GET /events
        (GetPositionsRequest, 1, 1000),  # GET /portfolio/positions
        (GetFillsRequest, 1, 1000),  # GET /portfolio/fills
        (GetSettlementsRequest, 1, 1000),  # GET /portfolio/settlements
        (ListOrdersRequest, 1, 1000),  # GET /portfolio/orders
        (GetMilestonesRequest, 1, 500),  # GET /milestones
        (
            ListMultivariateCollectionsRequest,
            1,
            200,
        ),  # GET /multivariate_event_collections
    ],
)
def test_page_limit_matches_spec(model, minimum, maximum):
    required = {"ticker": "T"} if model is GetMarketTradesRequest else {}
    prop = model.to_mcp_input_schema()["properties"]["limit"]
    assert (prop["minimum"], prop["maximum"]) == (minimum, maximum)
    assert f"{minimum}-{maximum}" in prop["description"]
    assert model(**required, limit=maximum).limit == maximum
    assert model(**required, limit=minimum).limit == minimum
    with pytest.raises(ValidationError):
        model(**required, limit=maximum + 1)
    with pytest.raises(ValidationError):
        model(**required, limit=minimum - 1)


def test_list_events_no_longer_claims_1000():
    prop = ListEventsRequest.to_mcp_input_schema()["properties"]["limit"]
    assert "1000" not in prop["description"]


def test_list_markets_status_accepts_paused():
    assert ListMarketsRequest(status="paused").status == "paused"
    enum = ListMarketsRequest.to_mcp_input_schema()["properties"]["status"]["enum"]
    assert enum == ["unopened", "open", "paused", "closed", "settled"]
    with pytest.raises(ValidationError):
        ListMarketsRequest(status="halted")


def test_order_groups_pagination_kept_and_marked_not_in_spec():
    props = ListOrderGroupsRequest.to_mcp_input_schema()["properties"]
    assert ListOrderGroupsRequest().limit == 20
    assert (props["limit"]["minimum"], props["limit"]["maximum"]) == (1, 100)
    assert "Not in Kalshi's OpenAPI spec" in props["limit"]["description"]
    assert "Not in Kalshi's OpenAPI spec" in props["cursor"]["description"]
    assert "subaccount" in props
    assert "documents only" in (ListOrderGroupsRequest.__doc__ or "")
