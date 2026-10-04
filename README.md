# DE-01 — Financial Market Data Pipeline

DE-01 is a Python financial-market data project that establishes a narrow,
validated path from Twelve Data XAU/USD responses to canonical market-data records.
The implemented scope is deliberately limited to `XAU/USD` at `1h` and `1day`
intervals.

## Implemented capabilities

### Canonical market-data model

`MarketData` is an immutable, validated candle model containing a timestamp, symbol,
timeframe, OHLC values, and volume.

- Timestamps must be timezone-aware and are normalized to UTC.
- OHLC values are finite, strictly positive `Decimal` instances with intrinsic candle
  relationship validation.
- `volume` is `Decimal | None`: `None` means unavailable or missing volume;
  `Decimal("0")` is a distinct, valid explicitly reported zero.
- Symbol and timeframe values are trimmed of surrounding whitespace.

### Twelve Data ingestion

The Twelve Data adapter uses `httpx` to request `/time_series` data and converts
provider-specific JSON at the ingestion boundary into canonical `MarketData` objects.

- Supported symbol: `XAU/USD`
- Supported intervals: `1h` and `1day`
- Credentials are supplied through `TWELVE_DATA_API_KEY`; no credentials are
  hard-coded.
- Provider numeric strings are converted directly to `Decimal`.
- Provider, network, response, and parsing failures use structured ingestion
  exceptions.
- Unit tests use mocked HTTP responses; normal tests need neither a live key nor live
  API calls.

### Historical backfill

`backfill_time_series(...)` is the strict public API for reconstructing an ascending,
deduplicated list of canonical candles. It accepts explicit start and end ranges,
discovers provider availability through the earliest-timestamp endpoint, and clamps
the effective start to that availability.

- Canonical deduplication identity: `(symbol, timeframe, timestamp)`.
- `1h` requests use UTC semantics and deterministic windows of at most 90 calendar
  days.
- `1day` requests use Australia/Sydney provider calendar dates and date-based window
  arithmetic; daily progression is not UTC plus 24 hours.

### Provenance-aware ingestion and quality validation

`fetch_backfill_batch(...)` provides an in-memory, provenance-preserving retrieval
path for validation. It retains pre-deduplication provider rows, metadata, and
request-window context, while protecting provenance structures from caller mutation.
It is separate from quality validation and is not raw object-storage persistence.

`validate_batch(...)` performs pure integrity, completeness, and continuity validation
and returns `PASS`, `PASS_WITH_WARNINGS`, or `QUARANTINE`.

Integrity uses defective effective received records divided by effective received
records. Only proven safe intentional hourly overlap copies are excluded from the
denominator. The integrity threshold passes at or below 1% and quarantines above 1%.
Each defective record contributes at most once, while retaining individual issues.

- OHLC relationships are validated; missing/null volume and explicit zero volume are
  valid.
- Unexpected duplicates are defects; conflicting valid overlap observations are fatal.
- Adjacent `1h` windows intentionally share an inclusive boundary. Only equivalent,
  valid boundary copies receive the overlap exemption; defective or conflicting copies
  are not silently exempted.
- Completeness is independent from integrity. The default internal missing limit is
  0.1%; the maximum contiguous internal gap is 3 intervals for `1h` and 1 interval
  for `1day`.
- Leading, internal, and trailing gaps are reported separately. Unexplained leading
  or trailing gaps in completed historical ranges quarantine the batch.

Quality-aware ingestion distinguishes request/provider/window retrieval failures from
malformed successful responses. Neither can silently become a successful quality report.

## XAU/USD provider time semantics

These semantics are established for this project's Twelve Data XAU/USD contract; they
are not universal claims for every Twelve Data instrument.

| Timeframe | Provider and continuity semantics |
| --- | --- |
| `1h` | Canonical UTC timestamps; inclusive start/end bounds; exact one-hour expected cadence; no currently verified weekend or holiday exclusions. |
| `1day` | Australia/Sydney provider calendar dates; `[start_date, end_date)` bounds; one Sydney calendar day per expected interval; no currently verified weekend or holiday exclusions. |

Daily timestamps are created by converting each Sydney midnight independently to UTC.
This preserves continuity across daylight-saving transitions, where adjacent canonical
UTC timestamps can be 23 or 25 hours apart. Provider calendar dates must be recovered by converting the canonical timestamp to
Australia/Sydney before taking its date, not by using `timestamp.date()` directly.

## Architecture status

```text
Twelve Data
  -> ingestion/client                    [implemented]
  -> historical backfill                 [implemented]
  -> provenance-aware batch              [implemented]
  -> quality/continuity validation       [implemented]
  -> raw object storage                  [planned]
  -> Parquet staging                     [planned]
  -> transformation                      [planned]
  -> ClickHouse analytics                [planned]
```

The broader target remains:

```text
Raw -> Staging -> Transform -> Analytics
```

Persistent raw or quarantine storage, Parquet staging, transformations, ClickHouse,
incremental ingestion, watermarks, scheduling, observability, and downstream analytics
are not implemented.

## Setup and verification

Python 3.11 or later is required.

```powershell
# Create and activate a virtual environment.
py -m venv .venv
.\.venv\Scripts\Activate.ps1

# Install the project and development tools in editable mode.
py -m pip install -e ".[dev]"

# Supply your own key locally. Never commit a real key.
$env:TWELVE_DATA_API_KEY = "replace-with-your-own-key"

# Run quality checks.
py -m ruff check .
py -m pytest
```

## Project status and roadmap

**Completed**

- Repository architecture and CI foundation
- Canonical immutable `MarketData` model
- Twelve Data XAU/USD ingestion
- Controlled provider-contract verification
- Historical backfill
- Provenance-aware ingestion batches
- Data-quality, integrity, completeness, and continuity validation
- Sydney daily/DST handling
- Duplicate and intentional-overlap handling

**Next milestone**

- Raw Data Persistence contract and implementation

**Future**

- Raw object storage and quarantine persistence
- Parquet staging
- Transformations
- ClickHouse analytics
- Incremental ingestion and watermarks
- Scheduling and automation
- Observability and final documentation

## Consumers

The planned analytics dataset will support analysts, data scientists, ML engineers,
and quants for exploration, modelling, backtesting, and research.
