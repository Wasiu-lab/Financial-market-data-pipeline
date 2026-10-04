"""Typed policy, diagnostics, and summaries for data-quality validation."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from de01.ingestion.batch import RecordRef
from de01.market_data import MarketData


class QualityDecision(StrEnum):
    PASS = "PASS"
    PASS_WITH_WARNINGS = "PASS_WITH_WARNINGS"
    QUARANTINE = "QUARANTINE"


class IssueCode(StrEnum):
    MALFORMED_RESPONSE = "MALFORMED_RESPONSE"
    WINDOW_FAILURE = "WINDOW_FAILURE"
    INVALID_CANDLE = "INVALID_CANDLE"
    INVALID_IDENTITY = "INVALID_IDENTITY"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    INVALID_ALIGNMENT = "INVALID_ALIGNMENT"
    UNEXPECTED_DUPLICATE = "UNEXPECTED_DUPLICATE"
    CONFLICTING_OBSERVATIONS = "CONFLICTING_OBSERVATIONS"
    MISSING_VOLUME = "MISSING_VOLUME"
    LEADING_GAP = "LEADING_GAP"
    TRAILING_GAP = "TRAILING_GAP"
    INTERNAL_GAP = "INTERNAL_GAP"
    WHOLLY_MISSING = "WHOLLY_MISSING"


@dataclass(frozen=True)
class QualityPolicy:
    integrity_defect_limit: Decimal = Decimal("0.01")
    internal_missing_rate_limit: Decimal = Decimal("0.001")
    max_hourly_internal_gap: int = 3
    max_daily_internal_gap: int = 1

    def __post_init__(self) -> None:
        for name in ("integrity_defect_limit", "internal_missing_rate_limit"):
            value = getattr(self, name)
            if not isinstance(value, Decimal) or not value.is_finite() or not Decimal(0) <= value <= Decimal(1):
                raise ValueError(f"{name} must be a finite Decimal from zero to one")
        if self.max_hourly_internal_gap < 0 or self.max_daily_internal_gap < 0:
            raise ValueError("maximum internal gaps must not be negative")


@dataclass(frozen=True)
class QualityIssue:
    code: IssueCode
    message: str
    ref: RecordRef | None = None
    fatal: bool = False


@dataclass(frozen=True)
class IntegritySummary:
    raw_received_count: int
    intentional_overlap_copies_excluded: int
    effective_received_count: int
    unique_recoverable_identity_count: int
    valid_unique_market_data_count: int
    unexpected_duplicate_excess_count: int
    defective_effective_record_count: int
    integrity_defect_rate: Decimal | None
    missing_volume_count: int


@dataclass(frozen=True)
class CompletenessSummary:
    expected_interval_count: int
    internal_missing_count: int
    internal_missing_rate: Decimal | None
    largest_contiguous_internal_gap: int
    leading_missing_count: int
    trailing_missing_count: int
    wholly_missing: bool


@dataclass(frozen=True)
class QualityReport:
    decision: QualityDecision
    policy: QualityPolicy
    integrity: IntegritySummary
    completeness: CompletenessSummary
    issues: tuple[QualityIssue, ...]
    valid_market_data: tuple[MarketData, ...]
    requested_start: datetime
    requested_end: datetime
    effective_start: datetime
