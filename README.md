# DE-01 — Financial Market Data Pipeline

DE-01 is a Python financial-market data project that establishes a reliable path from
provider data to a canonical market-data model. It currently supports a deliberately
narrow Twelve Data ingestion boundary for XAU/USD candles.

## Implemented

### Canonical market-data model

`MarketData` is an immutable domain model for a validated candle with `timestamp`,
`symbol`, `timeframe`, OHLC values, and `volume`.

- Timestamps must be timezone-aware and are normalized to UTC.
- OHLC values are finite, strictly positive `Decimal` instances with intrinsic candle
  relationship checks.
- `volume` is `Decimal | None`: `None` means unavailable or missing volume, while
  `Decimal("0")` represents an explicitly reported zero volume.
- Symbol and timeframe values are normalized by trimming surrounding whitespace.

### Twelve Data ingestion

The Twelve Data adapter uses `httpx` to request `/time_series` data and converts
provider-specific JSON at the ingestion boundary into canonical `MarketData` objects.

- Initial symbol: `XAU/USD`
- Supported intervals: `1h` and `1day`
- Credentials are supplied through `TWELVE_DATA_API_KEY`; no credentials are hard-coded.
- Numeric provider strings are converted directly to `Decimal` before reaching
  `MarketData`.
- Provider, network, response, and parsing failures are represented by ingestion
  exceptions.
- Unit tests use mocked HTTP responses; no live API key or live API call is required.

## Planned architecture

The following end-to-end architecture remains the target. Only the canonical model and
the Twelve Data ingestion boundary are implemented today.

```text
Twelve Data
  → ingestion                         [implemented]
  → raw storage                        [planned]
  → staging / Parquet                  [planned]
  → validation / data quality          [planned]
  → transformation                     [planned]
  → ClickHouse analytics               [planned]
```

State management, watermarks, scheduling, orchestration, observability, and downstream
consumer workflows are also future work. No raw storage, Parquet staging, data-quality
quarantine, database integration, or analytics store is implemented yet.

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
- Twelve Data ingestion boundary for XAU/USD `1h` and `1day` candles

**Next**

- Controlled live XAU/USD API verification
- Historical backfill
- Raw storage
- Parquet staging
- Data-quality and quarantine layer
- Transformations
- ClickHouse analytics
- Incremental ingestion and watermarks
- Scheduling and automation
- Observability and final documentation

## Consumers

The planned analytics dataset will support analysts, data scientists, ML engineers, and
quants for exploration, modelling, backtesting, and research.
