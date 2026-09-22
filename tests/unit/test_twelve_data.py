"""Tests for the Twelve Data ingestion boundary."""

from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from de01.config.settings import get_twelve_data_api_key
from de01.ingestion import (
    TwelveDataClient,
    TwelveDataParseError,
    TwelveDataRequestError,
    TwelveDataResponseError,
)


def _successful_payload(
    *,
    interval: str = "1h",
    exchange_timezone: str = "UTC",
    candle: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "meta": {
            "symbol": "XAU/USD",
            "interval": interval,
            "exchange_timezone": exchange_timezone,
        },
        "values": [
            candle
            or {
                "datetime": "2026-09-06 12:00:00",
                "open": "100.10",
                "high": "101.20",
                "low": "99.90",
                "close": "100.50",
                "volume": "12.5",
            }
        ],
        "status": "ok",
    }


def _client(handler: httpx.MockTransport) -> TwelveDataClient:
    client = httpx.Client(base_url="https://api.twelvedata.com", transport=handler)
    return TwelveDataClient("test-api-key", client=client)


@pytest.mark.parametrize("value", [None, "", "   "])
def test_missing_or_blank_api_key_configuration_is_rejected(
    monkeypatch: pytest.MonkeyPatch, value: str | None
) -> None:
    if value is None:
        monkeypatch.delenv("TWELVE_DATA_API_KEY", raising=False)
    else:
        monkeypatch.setenv("TWELVE_DATA_API_KEY", value)

    with pytest.raises(ValueError, match="TWELVE_DATA_API_KEY"):
        get_twelve_data_api_key()


def test_api_key_configuration_is_stripped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TWELVE_DATA_API_KEY", " key ")

    assert get_twelve_data_api_key() == "key"


def test_unsupported_symbol_is_rejected() -> None:
    with pytest.raises(ValueError, match="XAU/USD"):
        TwelveDataClient("key").fetch_time_series("EUR/USD", "1h")


def test_unsupported_interval_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported interval"):
        TwelveDataClient("key").fetch_time_series("XAU/USD", "5min")


def test_successful_hourly_response_is_parsed_to_canonical_market_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "apikey test-api-key"
        assert request.url.path == "/time_series"
        assert dict(request.url.params) == {"symbol": "XAU/USD", "interval": "1h", "timezone": "UTC"}
        return httpx.Response(200, json=_successful_payload())

    data = _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")

    assert len(data) == 1
    assert data[0].timestamp == datetime(2026, 9, 6, 12, tzinfo=UTC)
    assert data[0].symbol == "XAU/USD"
    assert data[0].timeframe == "1h"
    assert data[0].open == Decimal("100.10")
    assert data[0].high == Decimal("101.20")
    assert data[0].low == Decimal("99.90")
    assert data[0].close == Decimal("100.50")
    assert data[0].volume == Decimal("12.5")


def test_daily_response_uses_exchange_timezone_before_market_data_normalizes_to_utc() -> None:
    payload = _successful_payload(
        interval="1day",
        exchange_timezone="Europe/London",
        candle={
            "datetime": "2026-09-06",
            "open": "100",
            "high": "110",
            "low": "90",
            "close": "105",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {"symbol": "XAU/USD", "interval": "1day"}
        return httpx.Response(200, json=payload)

    data = _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1day")

    assert data[0].timestamp == datetime(2026, 9, 5, 23, tzinfo=UTC)


@pytest.mark.parametrize(
    ("volume_present", "volume_value", "expected"),
    [
        (False, None, None),
        (True, None, None),
        (True, "0", Decimal(0)),
    ],
)
def test_volume_nullability_and_zero_are_preserved(
    volume_present: bool, volume_value: str | None, expected: Decimal | None
) -> None:
    candle: dict[str, object] = {
        "datetime": "2026-09-06 12:00:00",
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
    }
    if volume_present:
        candle["volume"] = volume_value

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_successful_payload(candle=candle))

    data = _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")

    assert data[0].volume == expected


@pytest.mark.parametrize("field", ["open", "volume"])
def test_malformed_numeric_field_is_rejected_with_row_context(field: str) -> None:
    candle = {
        "datetime": "2026-09-06 12:00:00",
        "open": "100",
        "high": "110",
        "low": "90",
        "close": "105",
        "volume": "12",
    }
    candle[field] = "not-a-decimal"

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_successful_payload(candle=candle))

    with pytest.raises(TwelveDataParseError, match=rf"row 0 field '{field}'"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_malformed_timestamp_is_rejected_with_row_context() -> None:
    payload = _successful_payload(
        candle={"datetime": "invalid", "open": "100", "high": "110", "low": "90", "close": "105"}
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(TwelveDataParseError, match="row 0 field 'datetime'"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_malformed_response_structure_is_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok", "values": []})

    with pytest.raises(TwelveDataParseError, match="meta"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_documented_provider_error_response_is_rejected() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"code": 400, "message": "Invalid interval", "status": "error"},
        )

    with pytest.raises(TwelveDataResponseError, match="Twelve Data error 400: Invalid interval"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_transport_failure_is_wrapped() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection failed", request=request)

    with pytest.raises(TwelveDataRequestError, match="request failed"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_non_success_http_status_is_wrapped() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401,
            json={"code": 401, "message": "Invalid API key", "status": "error"},
        )

    with pytest.raises(TwelveDataResponseError, match="HTTP 401"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")


def test_market_data_validation_failure_includes_row_context() -> None:
    payload = _successful_payload(
        candle={
            "datetime": "2026-09-06 12:00:00",
            "open": "100",
            "high": "99",
            "low": "90",
            "close": "95",
        }
    )

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    with pytest.raises(TwelveDataParseError, match="row 0: invalid candle: high"):
        _client(httpx.MockTransport(handler)).fetch_time_series("XAU/USD", "1h")
