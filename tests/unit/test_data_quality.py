"""Focused contract tests for the pure DE-01 quality-validation stage."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from unittest.mock import patch
from zoneinfo import ZoneInfo

import httpx
import pytest

import de01.validation.quality as quality_module
from de01.ingestion import (
    IngestionBatch,
    ProviderRow,
    RecordRef,
    RequestWindow,
    TwelveDataClient,
    WindowBatch,
    fetch_backfill_batch,
)
from de01.validation import IssueCode, QualityDecision, QualityPolicy, validate_batch

_SYDNEY = ZoneInfo("Australia/Sydney")


def _raw(timestamp: str, *, low: str = "99", volume: object = "1") -> dict[str, object]:
    row: dict[str, object] = {
        "datetime": timestamp,
        "open": "100",
        "high": "101",
        "low": low,
        "close": "100",
    }
    if volume != "absent":
        row["volume"] = volume
    return row


def _batch(
    rows_by_window: list[list[object]],
    *,
    timeframe: str = "1h",
    start: datetime | None = None,
    end: datetime | None = None,
) -> IngestionBatch:
    start = start or datetime(2026, 1, 1, tzinfo=UTC)
    end = end or datetime(2026, 1, 1, 2, tzinfo=UTC)
    windows = []
    for index, rows in enumerate(rows_by_window):
        window_start = start if index == 0 else start + timedelta(hours=1)
        window_end = end
        overlap = window_start if timeframe == "1h" and index else None
        window = RequestWindow(index, "XAU/USD", timeframe, window_start, window_end, overlap)
        received = tuple(ProviderRow(RecordRef(index, row_index), row) for row_index, row in enumerate(rows))
        windows.append(WindowBatch(window, {"symbol": "XAU/USD", "interval": timeframe}, received))
    return IngestionBatch("XAU/USD", timeframe, start, end, start, start, tuple(windows))


def test_normal_hourly_overlap_is_excluded_without_a_duplicate_defect() -> None:
    batch = _batch(
        [[_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00")], [_raw("2026-01-01 01:00:00"), _raw("2026-01-01 02:00:00")]]
    )

    report = validate_batch(batch)

    assert report.integrity.raw_received_count == 4
    assert report.integrity.intentional_overlap_copies_excluded == 1
    assert report.integrity.effective_received_count == 3
    assert report.integrity.unexpected_duplicate_excess_count == 0
    assert report.decision == QualityDecision.PASS


def test_overlap_exemption_does_not_hide_excess_duplicates() -> None:
    batch = _batch(
        [[_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00"), _raw("2026-01-01 01:00:00")], [_raw("2026-01-01 01:00:00"), _raw("2026-01-01 02:00:00")]]
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 1
    assert report.integrity.unexpected_duplicate_excess_count == 1
    assert report.integrity.defective_effective_record_count == 1
    assert report.decision == QualityDecision.QUARANTINE


def test_conflicting_hourly_overlap_is_fatal() -> None:
    left, right = _raw("2026-01-01 01:00:00"), _raw("2026-01-01 01:00:00")
    right["close"] = "100.1"
    batch = _batch([[_raw("2026-01-01 00:00:00"), left], [right, _raw("2026-01-01 02:00:00")]])

    report = validate_batch(batch)

    assert report.decision == QualityDecision.QUARANTINE
    assert IssueCode.CONFLICTING_OBSERVATIONS in {issue.code for issue in report.issues}


def test_defective_later_overlap_copy_is_not_excluded() -> None:
    batch = _batch(
        [
            [_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00")],
            [_raw("2026-01-01 01:00:00", low="102"), _raw("2026-01-01 02:00:00")],
        ]
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.effective_received_count == 4
    assert report.integrity.defective_effective_record_count == 1


def test_malformed_overlap_volume_is_not_excluded() -> None:
    batch = _batch(
        [
            [_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00")],
            [_raw("2026-01-01 01:00:00", volume="not-a-decimal"), _raw("2026-01-01 02:00:00")],
        ]
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.defective_effective_record_count == 1


def test_defective_first_overlap_copy_does_not_receive_an_exemption() -> None:
    batch = _batch(
        [
            [_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00", low="102")],
            [_raw("2026-01-01 01:00:00"), _raw("2026-01-01 02:00:00")],
        ]
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.effective_received_count == 4
    assert report.integrity.defective_effective_record_count == 2


def test_two_defective_overlap_primaries_receive_no_exemption() -> None:
    batch = _batch(
        [
            [_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00", low="102")],
            [_raw("2026-01-01 01:00:00", volume="not-a-decimal"), _raw("2026-01-01 02:00:00")],
        ]
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.effective_received_count == 4
    assert report.integrity.defective_effective_record_count == 2


def test_non_adjacent_hourly_duplicate_receives_no_overlap_exemption() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    first = RequestWindow(0, "XAU/USD", "1h", start, start + timedelta(hours=1))
    third = RequestWindow(2, "XAU/USD", "1h", start, start + timedelta(hours=2))
    batch = IngestionBatch(
        "XAU/USD",
        "1h",
        start,
        start + timedelta(hours=2),
        start,
        start,
        (
            WindowBatch(first, {"symbol": "XAU/USD", "interval": "1h"}, (ProviderRow(RecordRef(0, 0), _raw("2026-01-01 01:00:00")),)),
            WindowBatch(third, {"symbol": "XAU/USD", "interval": "1h"}, (ProviderRow(RecordRef(2, 0), _raw("2026-01-01 01:00:00")),)),
        ),
    )

    report = validate_batch(batch)

    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.unexpected_duplicate_excess_count == 1


def test_bad_ohlc_occupies_its_hourly_slot_but_is_an_integrity_defect() -> None:
    batch = _batch([[_raw("2026-01-01 00:00:00"), _raw("2026-01-01 01:00:00", low="102"), _raw("2026-01-01 02:00:00")]])

    report = validate_batch(batch)

    assert report.completeness.internal_missing_count == 0
    assert report.integrity.defective_effective_record_count == 1
    assert report.decision == QualityDecision.QUARANTINE


def test_missing_volume_is_informational_only() -> None:
    batch = _batch([[_raw("2026-01-01 00:00:00", volume="absent"), _raw("2026-01-01 01:00:00", volume=None), _raw("2026-01-01 02:00:00", volume="0")]])

    report = validate_batch(batch)

    assert report.integrity.missing_volume_count == 2
    assert report.integrity.defective_effective_record_count == 0
    assert report.decision == QualityDecision.PASS


def test_integrity_limit_uses_exact_unrounded_comparison() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in range(100)]
    rows[50]["low"] = "102"
    batch = _batch([rows], start=start, end=start + timedelta(hours=99))

    report = validate_batch(batch)

    assert report.integrity.integrity_defect_rate == Decimal("0.01")
    assert report.decision == QualityDecision.PASS_WITH_WARNINGS


def test_internal_hourly_gap_limit_and_leading_gap_are_separate() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    allowed = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in (0, 4, 5, 6, 7)]
    relaxed = QualityPolicy(internal_missing_rate_limit=Decimal(1))
    report = validate_batch(_batch([allowed], start=start, end=start + timedelta(hours=7)), policy=relaxed)
    assert report.completeness.internal_missing_count == 3
    assert report.completeness.largest_contiguous_internal_gap == 3
    assert report.decision == QualityDecision.PASS_WITH_WARNINGS

    too_long = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in (0, 5, 6, 7)]
    report = validate_batch(_batch([too_long], start=start, end=start + timedelta(hours=7)), policy=relaxed)
    assert report.completeness.largest_contiguous_internal_gap == 4
    assert report.decision == QualityDecision.QUARANTINE

    leading = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in (1, 2)]
    report = validate_batch(_batch([leading], start=start, end=start + timedelta(hours=2)))
    assert report.completeness.leading_missing_count == 1
    assert report.decision == QualityDecision.QUARANTINE


def test_daily_slots_use_sydney_dates_across_both_dst_directions() -> None:
    start_date, end_date = date(2026, 4, 4), date(2026, 4, 7)
    start = datetime.combine(start_date, time.min, _SYDNEY).astimezone(UTC)
    end = datetime.combine(end_date, time.min, _SYDNEY).astimezone(UTC)
    rows = [[_raw(day.isoformat()) for day in (date(2026, 4, 4), date(2026, 4, 5), date(2026, 4, 6))]]
    report = validate_batch(_batch(rows, timeframe="1day", start=start, end=end))

    assert report.completeness.expected_interval_count == 3
    assert report.decision == QualityDecision.PASS
    assert report.valid_market_data[0].timestamp.date() == date(2026, 4, 3)

    spring_start, spring_end = date(2026, 10, 3), date(2026, 10, 6)
    start = datetime.combine(spring_start, time.min, _SYDNEY).astimezone(UTC)
    end = datetime.combine(spring_end, time.min, _SYDNEY).astimezone(UTC)
    rows = [[_raw(day.isoformat()) for day in (date(2026, 10, 3), date(2026, 10, 4), date(2026, 10, 5))]]
    report = validate_batch(_batch(rows, timeframe="1day", start=start, end=end))
    assert report.completeness.expected_interval_count == 3
    assert report.decision == QualityDecision.PASS


def test_one_in_ninety_nine_integrity_defects_quarantines() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in range(99)]
    rows[-1]["low"] = "102"

    report = validate_batch(_batch([rows], start=start, end=start + timedelta(hours=98)))

    assert report.integrity.integrity_defect_rate > Decimal("0.01")
    assert report.decision == QualityDecision.QUARANTINE


def test_one_in_one_hundred_one_integrity_defects_passes_with_warning() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in range(101)]
    rows[-1]["low"] = "102"

    report = validate_batch(_batch([rows], start=start, end=start + timedelta(hours=100)))

    assert report.integrity.integrity_defect_rate < Decimal("0.01")
    assert report.decision == QualityDecision.PASS_WITH_WARNINGS


def test_exact_completeness_limit_passes_but_a_wholly_missing_range_quarantines() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [
        _raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S"))
        for index in range(1_000)
        if index != 500
    ]
    report = validate_batch(_batch([rows], start=start, end=start + timedelta(hours=999)))
    assert report.completeness.internal_missing_rate == Decimal("0.001")
    assert report.decision == QualityDecision.PASS_WITH_WARNINGS

    report = validate_batch(_batch([[]], start=start, end=start + timedelta(hours=1)))
    assert report.completeness.wholly_missing is True
    assert report.decision == QualityDecision.QUARANTINE


def test_daily_duplicates_have_no_overlap_exemption_and_daily_gap_limit_is_configurable() -> None:
    start_date, end_date = date(2026, 9, 24), date(2026, 9, 29)
    start = datetime.combine(start_date, time.min, _SYDNEY).astimezone(UTC)
    end = datetime.combine(end_date, time.min, _SYDNEY).astimezone(UTC)
    duplicated = [_raw("2026-09-24"), _raw("2026-09-24"), _raw("2026-09-25"), _raw("2026-09-26"), _raw("2026-09-27"), _raw("2026-09-28")]
    report = validate_batch(_batch([duplicated], timeframe="1day", start=start, end=end))
    assert report.integrity.intentional_overlap_copies_excluded == 0
    assert report.integrity.unexpected_duplicate_excess_count == 1

    relaxed = QualityPolicy(internal_missing_rate_limit=Decimal(1))
    one_gap = [_raw(day.isoformat()) for day in (date(2026, 9, 24), date(2026, 9, 26), date(2026, 9, 27), date(2026, 9, 28))]
    report = validate_batch(_batch([one_gap], timeframe="1day", start=start, end=end), policy=relaxed)
    assert report.completeness.largest_contiguous_internal_gap == 1
    assert report.decision == QualityDecision.PASS_WITH_WARNINGS

    two_gaps = [_raw(day.isoformat()) for day in (date(2026, 9, 24), date(2026, 9, 27), date(2026, 9, 28))]
    report = validate_batch(_batch([two_gaps], timeframe="1day", start=start, end=end), policy=relaxed)
    assert report.completeness.largest_contiguous_internal_gap == 2
    assert report.decision == QualityDecision.QUARANTINE


def test_daily_out_of_range_candle_is_fatal() -> None:
    start = datetime.combine(date(2026, 9, 24), time.min, _SYDNEY).astimezone(UTC)
    end = datetime.combine(date(2026, 9, 25), time.min, _SYDNEY).astimezone(UTC)
    report = validate_batch(_batch([[_raw("2026-09-25")]], timeframe="1day", start=start, end=end))

    assert report.decision == QualityDecision.QUARANTINE
    assert IssueCode.OUT_OF_RANGE in {issue.code for issue in report.issues}


def test_batch_fetch_retains_row_provenance_without_api_key() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json={"status": "ok", "datetime": "2026-01-01 00:00:00"})
        return httpx.Response(200, json={"status": "ok", "meta": {"symbol": "XAU/USD", "interval": "1h"}, "values": [_raw("2026-01-01 00:00:00")]})

    client = TwelveDataClient("secret-key", client=httpx.Client(base_url="https://api.twelvedata.com", transport=httpx.MockTransport(handler)))
    batch = fetch_backfill_batch(client, symbol="XAU/USD", timeframe="1h", start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC))

    assert batch.windows[0].rows[0].ref == RecordRef(0, 0)
    assert batch.windows[0].window.start == datetime(2026, 1, 1, tzinfo=UTC)
    assert "secret-key" not in repr(batch)


def test_batch_fetch_retains_malformed_rows_and_window_failures_as_diagnostics() -> None:
    def malformed_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json={"status": "ok", "datetime": "2026-01-01 00:00:00"})
        return httpx.Response(200, json={"status": "ok", "meta": {"symbol": "XAU/USD", "interval": "1h"}, "values": ["bad-row"]})

    client = TwelveDataClient("test", client=httpx.Client(base_url="https://api.twelvedata.com", transport=httpx.MockTransport(malformed_handler)))
    batch = fetch_backfill_batch(client, symbol="XAU/USD", timeframe="1h", start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC))
    assert batch.windows[0].rows[0].raw == "bad-row"

    def failing_handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/earliest_timestamp":
            return httpx.Response(200, json={"status": "ok", "datetime": "2026-01-01 00:00:00"})
        return httpx.Response(500, json={})

    client = TwelveDataClient("test", client=httpx.Client(base_url="https://api.twelvedata.com", transport=httpx.MockTransport(failing_handler)))
    batch = fetch_backfill_batch(client, symbol="XAU/USD", timeframe="1h", start=datetime(2026, 1, 1, tzinfo=UTC), end=datetime(2026, 1, 1, tzinfo=UTC))
    assert batch.windows[0].fatal_diagnostic is not None


def test_provenance_objects_deep_freeze_raw_rows_and_metadata() -> None:
    raw = {"nested": {"items": ["original"], "tuple": ({"value": "original"},)}}
    metadata = {"nested": {"items": ["original"]}}
    row = ProviderRow(RecordRef(0, 0), raw)
    window = RequestWindow(0, "XAU/USD", "1h", datetime(2026, 1, 1, tzinfo=UTC), datetime(2026, 1, 1, tzinfo=UTC))
    batch = WindowBatch(window, metadata, (row,))
    raw["nested"]["items"].append("changed")
    raw["nested"]["tuple"][0]["value"] = "changed"
    metadata["nested"]["items"].append("changed")

    assert row.raw["nested"]["items"] == ("original",)
    assert row.raw["nested"]["tuple"][0]["value"] == "original"
    assert batch.metadata["nested"]["items"] == ("original",)
    with pytest.raises(TypeError):
        row.raw["nested"] = "changed"
    with pytest.raises(TypeError):
        batch.metadata["nested"] = "changed"


def test_wrong_window_candle_is_not_exposed_as_usable_market_data() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = RequestWindow(0, "XAU/USD", "1h", start, start)
    batch = IngestionBatch(
        "XAU/USD",
        "1h",
        start,
        start + timedelta(hours=1),
        start,
        start,
        (WindowBatch(window, {"symbol": "XAU/USD", "interval": "1h"}, (ProviderRow(RecordRef(0, 0), _raw("2026-01-01 01:00:00")),)),),
    )

    report = validate_batch(batch)

    assert report.decision == QualityDecision.QUARANTINE
    assert IssueCode.OUT_OF_RANGE in {issue.code for issue in report.issues}
    assert report.valid_market_data == ()


def test_expected_slots_are_constructed_once_for_a_moderate_batch() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    rows = [_raw((start + timedelta(hours=index)).strftime("%Y-%m-%d %H:%M:%S")) for index in range(500)]
    batch = _batch([rows], start=start, end=start + timedelta(hours=499))

    with patch.object(quality_module, "_expected_slots", wraps=quality_module._expected_slots) as expected_slots:
        report = validate_batch(batch)

    assert expected_slots.call_count == 1
    assert report.decision == QualityDecision.PASS


def test_structural_metadata_failure_is_reported_by_validation() -> None:
    start = datetime(2026, 1, 1, tzinfo=UTC)
    window = RequestWindow(0, "XAU/USD", "1h", start, start)
    batch = IngestionBatch(
        "XAU/USD",
        "1h",
        start,
        start,
        start,
        start,
        (WindowBatch(window, {"symbol": "wrong", "interval": "1h"}, ()),),
    )

    report = validate_batch(batch)

    assert report.decision == QualityDecision.QUARANTINE
    assert IssueCode.MALFORMED_RESPONSE in {issue.code for issue in report.issues}
