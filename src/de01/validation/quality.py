"""Pure data-quality and continuity validation for provenance-preserving batches."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from de01.ingestion.batch import IngestionBatch, ProviderRow, RequestWindow, WindowBatch
from de01.ingestion.exceptions import TwelveDataError
from de01.ingestion.twelve_data_parser import parse_time_series_row
from de01.market_data import MarketData
from de01.validation.models import (
    CompletenessSummary,
    IntegritySummary,
    IssueCode,
    QualityDecision,
    QualityIssue,
    QualityPolicy,
    QualityReport,
)

_SYDNEY = ZoneInfo("Australia/Sydney")


@dataclass
class _Observation:
    row: ProviderRow
    timestamp: datetime | None
    candle: MarketData | None
    defective: bool = False
    excluded: bool = False
    usable: bool = True


def validate_batch(batch: IngestionBatch, *, policy: QualityPolicy | None = None) -> QualityReport:
    """Assess retained provider observations without performing I/O or writes."""
    policy = policy or QualityPolicy()
    issues: list[QualityIssue] = []
    observations: list[_Observation] = []
    windows = {window.window.window_id: window for window in batch.windows}
    expected_slots = _expected_slots(batch)

    for window_batch in batch.windows:
        if window_batch.fatal_diagnostic is not None:
            issues.append(
                QualityIssue(IssueCode.MALFORMED_RESPONSE, window_batch.fatal_diagnostic, fatal=True)
            )
        if (
            not isinstance(window_batch.metadata, Mapping)
            or window_batch.metadata.get("symbol") != batch.symbol
            or window_batch.metadata.get("interval") != batch.timeframe
        ):
            issues.append(
                QualityIssue(IssueCode.MALFORMED_RESPONSE, "response metadata is invalid", fatal=True)
            )
        for row in window_batch.rows:
            observation = _decode_row(row, batch, issues)
            observations.append(observation)
            _validate_range_and_alignment(observation, window_batch.window, expected_slots, batch, issues)

    excluded, unexpected_duplicates = _apply_duplicate_policy(observations, windows, batch, issues)
    present = {item.timestamp for item in observations if item.timestamp in expected_slots}
    completeness = _completeness(expected_slots, present, batch, policy, issues)

    effective = [item for item in observations if not item.excluded]
    defective = [item for item in effective if item.defective]
    valid_by_identity: dict[tuple[str, str, datetime], MarketData] = {}
    for item in effective:
        if item.candle is not None and item.usable and item.timestamp in expected_slots:
            valid_by_identity.setdefault((batch.symbol, batch.timeframe, item.candle.timestamp), item.candle)
    integrity = IntegritySummary(
        raw_received_count=len(observations),
        intentional_overlap_copies_excluded=excluded,
        effective_received_count=len(effective),
        unique_recoverable_identity_count=len({item.timestamp for item in observations if item.timestamp}),
        valid_unique_market_data_count=len(valid_by_identity),
        unexpected_duplicate_excess_count=unexpected_duplicates,
        defective_effective_record_count=len(defective),
        integrity_defect_rate=(Decimal(len(defective)) / Decimal(len(effective)) if effective else None),
        missing_volume_count=sum(_has_missing_volume(item.row.raw) for item in observations),
    )
    decision = _decision(integrity, completeness, issues, policy, batch.timeframe)
    return QualityReport(
        decision=decision,
        policy=policy,
        integrity=integrity,
        completeness=completeness,
        issues=tuple(issues),
        valid_market_data=tuple(sorted(valid_by_identity.values(), key=lambda candle: candle.timestamp)),
        requested_start=batch.requested_start,
        requested_end=batch.requested_end,
        effective_start=batch.effective_start,
    )


def _decode_row(row: ProviderRow, batch: IngestionBatch, issues: list[QualityIssue]) -> _Observation:
    timestamp = _recover_timestamp(row.raw, batch.timeframe)
    if timestamp is None:
        issues.append(QualityIssue(IssueCode.INVALID_IDENTITY, "row has no recoverable timestamp", row.ref, True))
    try:
        candle = parse_time_series_row(
            row.raw, index=row.ref.row_index, symbol=batch.symbol, interval=batch.timeframe
        )
    except TwelveDataError as exc:
        issues.append(QualityIssue(IssueCode.INVALID_CANDLE, str(exc), row.ref))
        return _Observation(row, timestamp, None, defective=True)
    if _has_missing_volume(row.raw):
        issues.append(QualityIssue(IssueCode.MISSING_VOLUME, "volume is unavailable", row.ref))
    return _Observation(row, timestamp, candle)


def _recover_timestamp(raw: object, timeframe: str) -> datetime | None:
    if not isinstance(raw, Mapping) or not isinstance(raw.get("datetime"), str):
        return None
    try:
        parsed = datetime.fromisoformat(raw["datetime"])
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        return None
    if timeframe == "1h":
        if "T" not in raw["datetime"] and " " not in raw["datetime"]:
            return None
        return parsed.replace(tzinfo=UTC)
    if timeframe == "1day" and parsed.time() != time.min:
        return None
    if timeframe == "1day":
        return parsed.replace(tzinfo=_SYDNEY).astimezone(UTC)
    return None


def _validate_range_and_alignment(
    item: _Observation,
    window: RequestWindow,
    expected_slots: set[datetime],
    batch: IngestionBatch,
    issues: list[QualityIssue],
) -> None:
    if item.timestamp is None:
        item.defective = True
        return
    if batch.timeframe == "1h":
        in_window = window.start <= item.timestamp <= window.end
    else:
        in_window = window.start <= item.timestamp < window.end
    if not in_window:
        item.defective = True
        item.usable = False
        issues.append(
            QualityIssue(IssueCode.OUT_OF_RANGE, "timestamp is outside expected range", item.row.ref, True)
        )
    elif item.timestamp not in expected_slots:
        item.defective = True
        item.usable = False
        issues.append(
            QualityIssue(IssueCode.INVALID_ALIGNMENT, "timestamp is not an expected interval", item.row.ref, True)
        )


def _apply_duplicate_policy(
    observations: list[_Observation],
    windows: dict[int, WindowBatch],
    batch: IngestionBatch,
    issues: list[QualityIssue],
) -> tuple[int, int]:
    groups: dict[datetime, list[_Observation]] = {}
    for item in observations:
        if item.timestamp is not None:
            groups.setdefault(item.timestamp, []).append(item)
    excluded = 0
    unexpected = 0
    for timestamp, group in groups.items():
        by_window: dict[int, list[_Observation]] = {}
        for item in group:
            by_window.setdefault(item.row.ref.window_id, []).append(item)
        window_ids = sorted(by_window)
        intentional = (
            batch.timeframe == "1h"
            and len(window_ids) == 2
            and window_ids[1] == window_ids[0] + 1
            and windows[window_ids[1]].window.intentional_overlap == timestamp
        )
        if intentional:
            first = by_window[window_ids[0]][0]
            second = by_window[window_ids[1]][0]
            if first.candle is not None and second.candle is not None and first.candle != second.candle:
                issues.append(
                    QualityIssue(
                        IssueCode.CONFLICTING_OBSERVATIONS,
                        "intentional overlap copies conflict",
                        second.row.ref,
                        True,
                    )
                )
            elif first.candle is not None and second.candle is not None:
                second.excluded = True
                excluded += 1
        remaining = [item for item in group if not item.excluded]
        for item in remaining[1:]:
            item.defective = True
            unexpected += 1
            issues.append(
                QualityIssue(IssueCode.UNEXPECTED_DUPLICATE, "unexpected duplicate record", item.row.ref)
            )
    return excluded, unexpected


def _expected_slots(batch: IngestionBatch) -> set[datetime]:
    if batch.timeframe == "1h":
        start = batch.effective_start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        if start < batch.effective_start:
            start += timedelta(hours=1)
        end = batch.requested_end.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        return {start + timedelta(hours=index) for index in range(int((end - start).total_seconds() // 3600) + 1)} if start <= end else set()
    start_date = batch.effective_start.astimezone(_SYDNEY).date()
    end_date = batch.requested_end.astimezone(_SYDNEY).date()
    return {
        datetime.combine(start_date + timedelta(days=index), time.min, _SYDNEY).astimezone(UTC)
        for index in range((end_date - start_date).days)
    }


def _completeness(
    expected: set[datetime], present: set[datetime], batch: IngestionBatch, policy: QualityPolicy, issues: list[QualityIssue]
) -> CompletenessSummary:
    slots = sorted(expected)
    missing = [slot not in present for slot in slots]
    if slots and not present:
        issues.append(QualityIssue(IssueCode.WHOLLY_MISSING, "all expected intervals are missing", fatal=True))
        return CompletenessSummary(len(slots), 0, Decimal(0), 0, 0, 0, True)
    first = next((index for index, absent in enumerate(missing) if not absent), len(slots))
    last = len(slots) - next((index for index, absent in enumerate(reversed(missing)) if not absent), len(slots))
    leading, trailing = first, len(slots) - last
    internal = missing[first:last]
    runs = _missing_runs(internal)
    internal_count = sum(internal)
    largest = max(runs, default=0)
    if leading:
        issues.append(QualityIssue(IssueCode.LEADING_GAP, f"{leading} leading intervals missing", fatal=True))
    if trailing:
        issues.append(QualityIssue(IssueCode.TRAILING_GAP, f"{trailing} trailing intervals missing", fatal=True))
    if internal_count:
        issues.append(QualityIssue(IssueCode.INTERNAL_GAP, f"{internal_count} internal intervals missing"))
    return CompletenessSummary(
        len(slots), internal_count, Decimal(internal_count) / Decimal(len(slots)) if slots else None,
        largest, leading, trailing, False
    )


def _missing_runs(values: list[bool]) -> list[int]:
    runs: list[int] = []
    current = 0
    for value in values:
        if value:
            current += 1
        elif current:
            runs.append(current)
            current = 0
    if current:
        runs.append(current)
    return runs


def _decision(
    integrity: IntegritySummary,
    completeness: CompletenessSummary,
    issues: list[QualityIssue],
    policy: QualityPolicy,
    timeframe: str,
) -> QualityDecision:
    if any(issue.fatal for issue in issues):
        return QualityDecision.QUARANTINE
    if integrity.effective_received_count and Decimal(integrity.defective_effective_record_count) > policy.integrity_defect_limit * integrity.effective_received_count:
        return QualityDecision.QUARANTINE
    if completeness.internal_missing_rate is not None and completeness.internal_missing_rate > policy.internal_missing_rate_limit:
        return QualityDecision.QUARANTINE
    maximum = (
        policy.max_hourly_internal_gap if timeframe == "1h" else policy.max_daily_internal_gap
    )
    if completeness.largest_contiguous_internal_gap > maximum:
        return QualityDecision.QUARANTINE
    if integrity.defective_effective_record_count or completeness.internal_missing_count:
        return QualityDecision.PASS_WITH_WARNINGS
    return QualityDecision.PASS


def _has_missing_volume(raw: object) -> bool:
    return isinstance(raw, Mapping) and ("volume" not in raw or raw["volume"] is None)
