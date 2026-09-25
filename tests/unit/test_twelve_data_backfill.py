"""Tests for Twelve Data historical backfill reconstruction."""

from datetime import UTC, date, datetime
from decimal import Decimal
from itertools import pairwise

import httpx
import pytest

from de01.ingestion import TwelveDataClient, TwelveDataParseError, backfill_time_series


def _client(handler: httpx.MockTransport) -> TwelveDataClient:
    return TwelveDataClient(
        "test-api-key",
        client=httpx.Client(base_url="https://api.twelvedata.com", transport=handler),
    )


def _earliest(timestamp: str) -> dict[str, object]:
    return {"datetime": timestamp, "status": "ok"}


def _response(interval: str, values: list[dict[str, object]]) -> dict[str, object]:
    return {
        "meta": {"symbol": "XAU/USD", "interval": interval, "type": "Precious Metal"},
        "values": values,
        "status": "ok",
    }


def _candle(timestamp: str, *, volume: str | None = None) -> dict[str, object]:
    candle: dict[str, object] = {
        "datetime": timestamp,
        "open": "100.10",
        "high": "101.20",
        "low": "99.90",
        "close": "100.50",
    }
    if volume is not None:
        candle["volume"] = volume
    return candle


@pytest.mark.parametrize(
    ("interval", "provider_timestamp", "expected"),
    [
        ("1h", "2020-01-24 13:00:00", datetime(2020, 1, 24, 13, tzinfo=UTC)),
        ("1day", "1979-12-26", datetime(1979, 12, 25, 13, tzinfo=UTC)),
    ],
)
def test_earliest_timestamp_is_parsed_to_a_canonical_utc_instant(
    interval: str, provider_timestamp: str, expected: datetime
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/earliest_timestamp"
        assert dict(request.url.params) == {"symbol": "XAU/USD", "interval": interval}
        return httpx.Response(200, json=_earliest(provider_timestamp))

    assert _client(httpx.MockTransport(handler)).fetch_earliest_timestamp("XAU/USD", interval) == expected


@pytest.mark.parametrize(
    ("requested_start", "expected_start"),
    [
        (datetime(2021, 1, 1, tzinfo=UTC), "2021-01-01 00:00:00"),
        (datetime(2019, 1, 1, tzinfo=UTC), "2020-01-24 13:00:00"),
    ],
)
def test_hourly_backfill_uses_the_later_of_requested_and_provider_earliest(
    requested_start: datetime, expected_start: str
) -> None:
    requested_starts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2020-01-24 13:00:00"))
        requested_starts.append(request.url.params["start_date"])
        return httpx.Response(200, json=_response("1h", []))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=requested_start,
        end=datetime(2021, 1, 2, tzinfo=UTC),
    )

    assert result == []
    assert requested_starts[0] == expected_start


def test_hourly_windows_are_deterministic_and_no_larger_than_ninety_days() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2026-01-01 00:00:00"))
        windows.append((request.url.params["start_date"], request.url.params["end_date"]))
        assert request.url.params["timezone"] == "UTC"
        assert request.url.params["order"] == "asc"
        return httpx.Response(200, json=_response("1h", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 8, 1, tzinfo=UTC),
    )

    assert windows == [
        ("2026-01-01 00:00:00", "2026-04-01 00:00:00"),
        ("2026-04-01 00:00:00", "2026-06-30 00:00:00"),
        ("2026-06-30 00:00:00", "2026-08-01 00:00:00"),
    ]


def test_short_hourly_range_uses_one_inclusive_request() -> None:
    requests: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2026-01-01 00:00:00"))
        requests.append(dict(request.url.params))
        return httpx.Response(200, json=_response("1h", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    assert len(requests) == 1


def test_hourly_equal_boundaries_request_and_return_the_candle() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2026-01-01 00:00:00"))
        requests += 1
        return httpx.Response(200, json=_response("1h", [_candle("2026-01-01 00:00:00")]))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, tzinfo=UTC),
    )

    assert requests == 1
    assert [candle.timestamp for candle in result] == [datetime(2026, 1, 1, tzinfo=UTC)]


def test_hourly_overlapping_boundaries_are_deduplicated_and_sorted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2026-01-01 00:00:00"))
        if request.url.params["start_date"] == "2026-01-01 00:00:00":
            values = [_candle("2026-04-01 00:00:00"), _candle("2026-01-01 00:00:00")]
        else:
            values = [_candle("2026-04-02 00:00:00"), _candle("2026-04-01 00:00:00")]
        return httpx.Response(200, json=_response("1h", values))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 4, 2, tzinfo=UTC),
    )

    assert [candle.timestamp for candle in result] == [
        datetime(2026, 1, 1, tzinfo=UTC),
        datetime(2026, 4, 1, tzinfo=UTC),
        datetime(2026, 4, 2, tzinfo=UTC),
    ]


def test_daily_windows_are_exclusive_at_the_end_and_never_empty() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        params = request.url.params
        assert "timezone" not in params
        windows.append((params["start_date"], params["end_date"]))
        return httpx.Response(200, json=_response("1day", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2000, 1, 1),
        end=date(2014, 1, 1),
    )

    assert len(windows) == 2
    assert all(start < end for start, end in windows)
    assert windows[0][1] == windows[1][0]


def test_daily_windows_use_sydney_dates_across_dst_and_accept_later_window_start() -> None:
    windows: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        start_date = request.url.params["start_date"]
        windows.append((start_date, request.url.params["end_date"]))
        return httpx.Response(200, json=_response("1day", [_candle(start_date)]))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2000, 1, 3),
        end=date(2026, 1, 1),
    )

    assert windows == [("2000-01-03", "2013-09-11"), ("2013-09-11", "2026-01-01")]
    assert [candle.timestamp for candle in result] == [
        datetime(2000, 1, 2, 13, tzinfo=UTC),
        datetime(2013, 9, 10, 14, tzinfo=UTC),
    ]


def test_daily_provider_date_windows_never_exceed_five_thousand_calendar_days() -> None:
    windows: list[tuple[date, date]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        windows.append(
            (
                date.fromisoformat(request.url.params["start_date"]),
                date.fromisoformat(request.url.params["end_date"]),
            )
        )
        return httpx.Response(200, json=_response("1day", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(1980, 1, 1),
        end=date(2026, 1, 1),
    )

    assert all((end - start).days <= 5_000 for start, end in windows)
    assert all(end == next_start for (_, end), (next_start, _) in pairwise(windows))


def test_daily_equal_boundaries_do_not_make_time_series_request() -> None:
    time_series_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal time_series_requests
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        time_series_requests += 1
        return httpx.Response(200, json=_response("1day", []))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2026, 1, 1),
        end=date(2026, 1, 1),
    )

    assert result == []
    assert time_series_requests == 0


def test_daily_start_is_clamped_to_provider_earliest_sydney_date() -> None:
    requested_start: str | None = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requested_start
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2020-01-10"))
        requested_start = request.url.params["start_date"]
        return httpx.Response(200, json=_response("1day", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2020, 1, 1),
        end=date(2020, 1, 11),
    )

    assert requested_start == "2020-01-10"


def test_six_year_daily_backfill_uses_one_safe_request() -> None:
    requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        requests += 1
        return httpx.Response(200, json=_response("1day", []))

    backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2020, 1, 1),
        end=date(2026, 1, 1),
    )

    assert requests == 1


def test_daily_backfill_preserves_sydney_conversion_decimal_values_and_missing_volume() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        return httpx.Response(200, json=_response("1day", [_candle("2026-09-24")]))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1day",
        start=date(2026, 9, 24),
        end=date(2026, 9, 25),
    )

    assert result[0].timestamp == datetime(2026, 9, 23, 14, tzinfo=UTC)
    assert result[0].open == Decimal("100.10")
    assert result[0].volume is None


def test_empty_or_unavailable_ranges_do_not_make_time_series_requests() -> None:
    time_series_requests = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal time_series_requests
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2020-01-24 13:00:00"))
        time_series_requests += 1
        return httpx.Response(200, json=_response("1h", []))

    result = backfill_time_series(
        _client(httpx.MockTransport(handler)),
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2019, 1, 1, tzinfo=UTC),
        end=datetime(2019, 1, 2, tzinfo=UTC),
    )

    assert result == []
    assert time_series_requests == 0


def test_malformed_earliest_timestamp_response_raises_parse_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"})

    with pytest.raises(TwelveDataParseError, match="response field 'datetime'"):
        _client(httpx.MockTransport(handler)).fetch_earliest_timestamp("XAU/USD", "1h")


def test_candle_outside_the_daily_window_is_rejected() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("1979-12-26"))
        return httpx.Response(200, json=_response("1day", [_candle("2026-09-25")]))

    with pytest.raises(TwelveDataParseError, match="outside requested window"):
        backfill_time_series(
            _client(httpx.MockTransport(handler)),
            symbol="XAU/USD",
            timeframe="1day",
            start=date(2026, 9, 24),
            end=date(2026, 9, 25),
        )


def test_equivalent_backfills_are_idempotent() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json=_earliest("2026-01-01 00:00:00"))
        return httpx.Response(
            200,
            json=_response("1h", [_candle("2026-01-01 01:00:00"), _candle("2026-01-01 00:00:00")]),
        )

    client = _client(httpx.MockTransport(handler))
    first = backfill_time_series(
        client,
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )
    second = backfill_time_series(
        client,
        symbol="XAU/USD",
        timeframe="1h",
        start=datetime(2026, 1, 1, tzinfo=UTC),
        end=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )

    assert first == second
