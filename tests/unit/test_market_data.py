"""Tests for the canonical market-data domain model."""

from datetime import UTC, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from de01.market_data import MarketData


def make_market_data(**overrides: object) -> MarketData:
    """Build a valid candle, allowing a test to override individual fields."""
    values: dict[str, object] = {
        "timestamp": datetime(2026, 9, 6, 12, tzinfo=UTC),
        "symbol": "XAU/USD",
        "timeframe": "1day",
        "open": Decimal(100),
        "high": Decimal(110),
        "low": Decimal(90),
        "close": Decimal(105),
        "volume": None,
    }
    values.update(overrides)
    return MarketData(**values)  # type: ignore[arg-type]


def test_valid_candle_with_no_volume() -> None:
    data = make_market_data()

    assert data.volume is None


@pytest.mark.parametrize("volume", [Decimal(0), Decimal("100.5")])
def test_decimal_volume_is_preserved(volume: Decimal) -> None:
    data = make_market_data(volume=volume)

    assert data.volume == volume


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": Decimal(100), "close": Decimal(100)},
        {"high": Decimal(105)},
        {"low": Decimal(100)},
        {"low": Decimal(105), "open": Decimal(105)},
    ],
)
def test_ohlc_boundary_equalities_are_valid(overrides: dict[str, Decimal]) -> None:
    data = make_market_data(**overrides)

    assert data.high >= data.open
    assert data.high >= data.close
    assert data.low <= data.open
    assert data.low <= data.close


def test_aware_timestamp_is_preserved_in_utc() -> None:
    timestamp = datetime(2026, 9, 6, 12, tzinfo=UTC)

    assert make_market_data(timestamp=timestamp).timestamp == timestamp


def test_non_utc_timestamp_is_normalized_to_utc() -> None:
    london_timestamp = datetime(2026, 9, 6, 12, tzinfo=ZoneInfo("Europe/London"))

    assert make_market_data(timestamp=london_timestamp).timestamp == datetime(
        2026, 9, 6, 11, tzinfo=UTC
    )


def test_dst_sensitive_timestamp_conversion_preserves_instant() -> None:
    london_timestamp = datetime(2026, 10, 25, 1, 30, tzinfo=ZoneInfo("Europe/London"), fold=0)

    assert make_market_data(timestamp=london_timestamp).timestamp == datetime(
        2026, 10, 25, 0, 30, tzinfo=UTC
    )


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [("symbol", " XAU/USD ", "XAU/USD"), ("timeframe", " 1day ", "1day")],
)
def test_text_fields_are_trimmed(field: str, value: str, expected: str) -> None:
    data = make_market_data(**{field: value})

    assert getattr(data, field) == expected


@pytest.mark.parametrize(
    "timestamp",
    [datetime(2026, 9, 6, 12), "2026-09-06T12:00:00Z"],  # noqa: DTZ001
)
def test_invalid_timestamp_is_rejected(timestamp: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_market_data(timestamp=timestamp)


@pytest.mark.parametrize("field", ["symbol", "timeframe"])
@pytest.mark.parametrize("value", ["", "   ", 1])
def test_invalid_text_fields_are_rejected(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_market_data(**{field: value})


@pytest.mark.parametrize("field", ["open", "high", "low", "close"])
@pytest.mark.parametrize(
    "value",
    [1.25, 100, "1.25", Decimal("NaN"), Decimal("Infinity"), Decimal("-Infinity")],
)
def test_invalid_ohlc_types_and_non_finite_values_are_rejected(field: str, value: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_market_data(**{field: value})


@pytest.mark.parametrize(
    "volume",
    [
        1.0,
        1,
        "1",
        Decimal("NaN"),
        Decimal("Infinity"),
        Decimal("-Infinity"),
        Decimal(-1),
    ],
)
def test_invalid_volume_is_rejected(volume: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        make_market_data(volume=volume)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("open", Decimal(0)),
        ("open", Decimal(-1)),
        ("high", Decimal(0)),
        ("high", Decimal(-1)),
        ("low", Decimal(0)),
        ("low", Decimal(-1)),
        ("close", Decimal(0)),
        ("close", Decimal(-1)),
    ],
)
def test_non_positive_prices_are_rejected(field: str, value: Decimal) -> None:
    with pytest.raises(ValueError):
        make_market_data(**{field: value})


@pytest.mark.parametrize(
    "overrides",
    [
        {"high": Decimal(99)},
        {"high": Decimal(104)},
        {"low": Decimal(101)},
        {"low": Decimal(106)},
        {"high": Decimal(95), "low": Decimal(100)},
    ],
)
def test_impossible_ohlc_relationships_are_rejected(overrides: dict[str, Decimal]) -> None:
    with pytest.raises(ValueError):
        make_market_data(**overrides)


def test_market_data_is_immutable() -> None:
    data = make_market_data()

    with pytest.raises(AttributeError):
        data.symbol = "EUR/USD"  # type: ignore[misc]
