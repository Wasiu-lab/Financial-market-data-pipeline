"""Immutable provenance records for quality-aware historical retrieval."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType


def freeze_value(value: object) -> object:
    """Copy provider JSON into immutable containers without retaining request secrets."""
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): freeze_value(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(freeze_value(item) for item in value)
    return value


@dataclass(frozen=True)
class RequestWindow:
    """One planned provider request, represented by canonical UTC boundaries."""

    window_id: int
    symbol: str
    timeframe: str
    start: datetime
    end: datetime
    intentional_overlap: datetime | None = None


@dataclass(frozen=True)
class RecordRef:
    """Stable provenance for an element received from a provider response."""

    window_id: int
    row_index: int


@dataclass(frozen=True)
class ProviderRow:
    """A raw provider row and its response/window provenance."""

    ref: RecordRef
    raw: object

    def __post_init__(self) -> None:
        object.__setattr__(self, "raw", freeze_value(self.raw))


@dataclass(frozen=True)
class WindowBatch:
    """Rows and metadata received for one request window."""

    window: RequestWindow
    metadata: object | None
    rows: tuple[ProviderRow, ...]
    fatal_diagnostic: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", freeze_value(self.metadata))
        object.__setattr__(self, "rows", tuple(self.rows))

    @property
    def raw_row_count(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class IngestionBatch:
    """A provenance-preserving historical retrieval result, with no credentials."""

    symbol: str
    timeframe: str
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
    provider_earliest: datetime
    windows: tuple[WindowBatch, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "windows", tuple(self.windows))
