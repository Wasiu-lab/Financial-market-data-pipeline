"""Canonical domain models for validated market-data observations."""

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal


@dataclass(frozen=True)
class MarketData:
    """One intrinsically valid market-data candle or observation.

    Numeric values must already be :class:`~decimal.Decimal` instances at the
    boundary to keep conversion choices outside the canonical domain model.
    """

    timestamp: datetime
    symbol: str
    timeframe: str
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal | None

    def __post_init__(self) -> None:
        self._validate_timestamp()
        self._normalize_text("symbol")
        self._normalize_text("timeframe")
        self._validate_ohlc_types_and_values()
        self._validate_volume()
        self._validate_ohlc_relationships()

    def _validate_timestamp(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(UTC))

    def _normalize_text(self, field_name: str) -> None:
        value = getattr(self, field_name)
        if not isinstance(value, str):
            raise TypeError(f"{field_name} must be a string")
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{field_name} must not be empty")
        object.__setattr__(self, field_name, normalized)

    def _validate_ohlc_types_and_values(self) -> None:
        for field_name in ("open", "high", "low", "close"):
            value = getattr(self, field_name)
            self._validate_finite_decimal(field_name, value)
            if value <= Decimal(0):
                raise ValueError(f"{field_name} must be greater than zero")

    def _validate_volume(self) -> None:
        if self.volume is None:
            return
        self._validate_finite_decimal("volume", self.volume)
        if self.volume < Decimal(0):
            raise ValueError("volume must be greater than or equal to zero")

    @staticmethod
    def _validate_finite_decimal(field_name: str, value: object) -> None:
        if not isinstance(value, Decimal):
            raise TypeError(f"{field_name} must be a Decimal")
        if not value.is_finite():
            raise ValueError(f"{field_name} must be finite")

    def _validate_ohlc_relationships(self) -> None:
        if self.high < self.open:
            raise ValueError("high must be greater than or equal to open")
        if self.high < self.close:
            raise ValueError("high must be greater than or equal to close")
        if self.low > self.open:
            raise ValueError("low must be less than or equal to open")
        if self.low > self.close:
            raise ValueError("low must be less than or equal to close")
        if self.high < self.low:
            raise ValueError("high must be greater than or equal to low")
