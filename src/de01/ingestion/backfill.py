"""Historical backfill reconstruction for the supported Twelve Data feed."""

from collections.abc import Mapping
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from de01.ingestion.batch import (
    IngestionBatch,
    ProviderRow,
    RecordRef,
    RequestWindow,
    WindowBatch,
    WindowDiagnosticKind,
)
from de01.ingestion.exceptions import TwelveDataError, TwelveDataParseError
from de01.ingestion.twelve_data import TwelveDataClient
from de01.market_data import MarketData

_HOURLY_WINDOW_DAYS = 90
_DAILY_WINDOW_DAYS = 5_000
_XAU_USD_DAILY_TIMEZONE = ZoneInfo("Australia/Sydney")


def backfill_time_series(
    client: TwelveDataClient,
    *,
    symbol: str,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
) -> list[MarketData]:
    """Reconstruct a sorted, deduplicated historical XAU/USD candle set.

    Hourly ranges are inclusive at both boundaries. Daily ranges follow Twelve
    Data's inclusive-start, exclusive-end contract.
    """
    requested_start = _normalize_boundary(start, timeframe=timeframe)
    requested_end = _normalize_boundary(end, timeframe=timeframe)
    provider_earliest = client.fetch_earliest_timestamp(symbol, timeframe)
    effective_start = max(requested_start, provider_earliest)

    if not _has_requestable_history(effective_start, requested_end, timeframe=timeframe):
        return []

    candles: dict[tuple[str, str, datetime], MarketData] = {}
    for window_start, window_end in _build_windows(effective_start, requested_end, timeframe=timeframe):
        response = client.fetch_time_series(
            symbol,
            timeframe,
            start_date=_format_boundary(window_start, timeframe=timeframe),
            end_date=_format_boundary(window_end, timeframe=timeframe),
            order="asc" if timeframe == "1h" else None,
        )
        for candle in response:
            _validate_window_candle(candle, window_start, window_end, timeframe=timeframe)
            candles.setdefault((candle.symbol, candle.timeframe, candle.timestamp), candle)

    return sorted(candles.values(), key=lambda candle: candle.timestamp)


def fetch_backfill_batch(
    client: TwelveDataClient,
    *,
    symbol: str,
    timeframe: str,
    start: date | datetime,
    end: date | datetime,
) -> IngestionBatch:
    """Fetch a backfill with raw provider-row provenance for quality validation."""
    requested_start = _normalize_boundary(start, timeframe=timeframe)
    requested_end = _normalize_boundary(end, timeframe=timeframe)
    provider_earliest = client.fetch_earliest_timestamp(symbol, timeframe)
    effective_start = max(requested_start, provider_earliest)
    if not _has_requestable_history(effective_start, requested_end, timeframe=timeframe):
        return IngestionBatch(
            symbol, timeframe, requested_start, requested_end, effective_start, provider_earliest, ()
        )

    planned = _build_windows(effective_start, requested_end, timeframe=timeframe)
    batches: list[WindowBatch] = []
    for index, (window_start, window_end) in enumerate(planned):
        overlap = window_start if timeframe == "1h" and index else None
        window = RequestWindow(index, symbol, timeframe, window_start, window_end, overlap)
        try:
            payload = client.fetch_time_series_payload(
                symbol,
                timeframe,
                start_date=_format_boundary(window_start, timeframe=timeframe),
                end_date=_format_boundary(window_end, timeframe=timeframe),
                order="asc" if timeframe == "1h" else None,
            )
        except TwelveDataError as exc:
            batches.append(
                WindowBatch(
                    window,
                    None,
                    (),
                    str(exc),
                    WindowDiagnosticKind.WINDOW_FAILURE,
                )
            )
            continue
        batches.append(_make_window_batch(window, payload))
    return IngestionBatch(
        symbol, timeframe, requested_start, requested_end, effective_start, provider_earliest, tuple(batches)
    )


def _make_window_batch(window: RequestWindow, payload: Mapping[str, object]) -> WindowBatch:
    metadata = payload.get("meta")
    values = payload.get("values")
    if (
        payload.get("status") != "ok"
        or not isinstance(metadata, Mapping)
        or not isinstance(values, list)
    ):
        return WindowBatch(
            window,
            metadata,
            (),
            "successful response has invalid status, meta, or values",
            WindowDiagnosticKind.MALFORMED_RESPONSE,
        )
    rows = tuple(ProviderRow(RecordRef(window.window_id, index), row) for index, row in enumerate(values))
    return WindowBatch(window, metadata, rows)


def _normalize_boundary(value: date | datetime, *, timeframe: str) -> datetime:
    timezone = UTC if timeframe == "1h" else _XAU_USD_DAILY_TIMEZONE
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("datetime boundaries must be timezone-aware")
        if timeframe == "1day":
            value = datetime.combine(value.astimezone(timezone).date(), time.min, timezone)
        return value.astimezone(UTC)
    if isinstance(value, date):
        return datetime.combine(value, time.min, timezone).astimezone(UTC)
    raise TypeError("backfill boundaries must be date or datetime values")


def _has_requestable_history(start: datetime, end: datetime, *, timeframe: str) -> bool:
    return start <= end if timeframe == "1h" else start < end


def _build_windows(
    start: datetime, end: datetime, *, timeframe: str
) -> list[tuple[datetime, datetime]]:
    if timeframe == "1h":
        return _build_inclusive_windows(start, end, timedelta(days=_HOURLY_WINDOW_DAYS))
    if timeframe == "1day":
        return _build_daily_windows(start, end)
    raise ValueError(f"Unsupported timeframe {timeframe!r}")


def _build_inclusive_windows(
    start: datetime, end: datetime, maximum_duration: timedelta
) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    current = start
    while current <= end:
        window_end = min(current + maximum_duration, end)
        windows.append((current, window_end))
        if window_end == end:
            break
        # Hourly end boundaries are inclusive, so overlap the next window.
        current = window_end
    return windows


def _build_daily_windows(start: datetime, end: datetime) -> list[tuple[datetime, datetime]]:
    """Build exclusive-end daily windows using Sydney calendar dates."""
    windows: list[tuple[datetime, datetime]] = []
    current = start.astimezone(_XAU_USD_DAILY_TIMEZONE).date()
    final_date = end.astimezone(_XAU_USD_DAILY_TIMEZONE).date()
    while current < final_date:
        window_end = min(current + timedelta(days=_DAILY_WINDOW_DAYS), final_date)
        # Twelve Data daily dates are Sydney market dates, not fixed UTC durations.
        windows.append((_daily_midnight_utc(current), _daily_midnight_utc(window_end)))
        current = window_end
    return windows


def _daily_midnight_utc(value: date) -> datetime:
    return datetime.combine(value, time.min, _XAU_USD_DAILY_TIMEZONE).astimezone(UTC)


def _format_boundary(boundary: datetime, *, timeframe: str) -> str:
    if timeframe == "1h":
        return boundary.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S")
    return boundary.astimezone(_XAU_USD_DAILY_TIMEZONE).date().isoformat()


def _validate_window_candle(
    candle: MarketData, start: datetime, end: datetime, *, timeframe: str
) -> None:
    if timeframe == "1h":
        is_in_range = start <= candle.timestamp <= end
    else:
        is_in_range = start <= candle.timestamp < end
    if not is_in_range:
        raise TwelveDataParseError(
            f"received {timeframe} candle outside requested window: {candle.timestamp.isoformat()}"
        )
