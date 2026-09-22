"""Provider-specific parsing for Twelve Data time-series responses."""

from collections.abc import Mapping
from datetime import UTC, datetime, tzinfo
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from de01.ingestion.exceptions import TwelveDataParseError, TwelveDataResponseError
from de01.market_data import MarketData


def parse_time_series_response(
    payload: object, *, symbol: str, interval: str
) -> list[MarketData]:
    """Parse a successful Twelve Data time-series response into canonical candles."""
    response = _require_mapping(payload, "response")
    _validate_status(response)
    metadata = _require_mapping(response.get("meta"), "meta")
    _validate_metadata(metadata, symbol=symbol, interval=interval)
    values = response.get("values")
    if not isinstance(values, list):
        raise TwelveDataParseError("response field 'values' must be a list")

    timezone = _timestamp_timezone(interval, metadata)
    return [
        _parse_candle(row, index=index, symbol=symbol, interval=interval, timezone=timezone)
        for index, row in enumerate(values)
    ]


def _validate_status(response: Mapping[str, object]) -> None:
    status = response.get("status")
    if status == "error":
        code = response.get("code", "unknown")
        message = response.get("message", "No provider error message supplied")
        raise TwelveDataResponseError(f"Twelve Data error {code}: {message}")
    if status != "ok":
        raise TwelveDataParseError("response field 'status' must be 'ok'")


def _validate_metadata(metadata: Mapping[str, object], *, symbol: str, interval: str) -> None:
    if metadata.get("symbol") != symbol:
        raise TwelveDataParseError(f"meta field 'symbol' must be {symbol!r}")
    if metadata.get("interval") != interval:
        raise TwelveDataParseError(f"meta field 'interval' must be {interval!r}")


def _timestamp_timezone(interval: str, metadata: Mapping[str, object]) -> tzinfo:
    if interval == "1h":
        return UTC

    exchange_timezone = metadata.get("exchange_timezone")
    if not isinstance(exchange_timezone, str) or not exchange_timezone.strip():
        raise TwelveDataParseError("meta field 'exchange_timezone' must be a non-empty string")
    try:
        return ZoneInfo(exchange_timezone)
    except ZoneInfoNotFoundError as exc:
        raise TwelveDataParseError(
            f"meta field 'exchange_timezone' is not a known IANA timezone: {exchange_timezone!r}"
        ) from exc


def _parse_candle(
    row: object, *, index: int, symbol: str, interval: str, timezone: tzinfo
) -> MarketData:
    candle = _require_mapping(row, f"row {index}")
    try:
        return MarketData(
            timestamp=_parse_timestamp(candle.get("datetime"), index=index, interval=interval, timezone=timezone),
            symbol=symbol,
            timeframe=interval,
            open=_parse_decimal(candle.get("open"), field_name="open", index=index),
            high=_parse_decimal(candle.get("high"), field_name="high", index=index),
            low=_parse_decimal(candle.get("low"), field_name="low", index=index),
            close=_parse_decimal(candle.get("close"), field_name="close", index=index),
            volume=_parse_volume(candle, index=index),
        )
    except TwelveDataParseError:
        raise
    except (TypeError, ValueError) as exc:
        raise TwelveDataParseError(f"row {index}: invalid candle: {exc}") from exc


def _parse_timestamp(value: object, *, index: int, interval: str, timezone: tzinfo) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise TwelveDataParseError(f"row {index} field 'datetime' must be a non-empty string")
    if interval == "1h" and "T" not in value and " " not in value:
        raise TwelveDataParseError(f"row {index} field 'datetime' must include a time component")
    try:
        timestamp = datetime.fromisoformat(value)
    except ValueError as exc:
        raise TwelveDataParseError(f"row {index} field 'datetime' is not ISO-8601: {value!r}") from exc
    if timestamp.tzinfo is not None:
        raise TwelveDataParseError(f"row {index} field 'datetime' must not include a timezone offset")
    return timestamp.replace(tzinfo=timezone)


def _parse_decimal(value: object, *, field_name: str, index: int) -> Decimal:
    if not isinstance(value, str) or not value.strip():
        raise TwelveDataParseError(f"row {index} field {field_name!r} must be a non-empty string")
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise TwelveDataParseError(f"row {index} field {field_name!r} is not a Decimal: {value!r}") from exc


def _parse_volume(candle: Mapping[str, object], *, index: int) -> Decimal | None:
    if "volume" not in candle or candle["volume"] is None:
        return None
    return _parse_decimal(candle["volume"], field_name="volume", index=index)


def _require_mapping(value: object, location: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TwelveDataParseError(f"{location} must be an object")
    return value
