# DE-01 — Financial Market Data Pipeline

## Objective

Build a reliable financial market data pipeline that ingests historical and daily XAU/USD market data from an external API, stores raw and transformed datasets, validates data quality, and produces an analytics-ready dataset for analysts, quants and ML engineers.

## Initial Scope

* Initial Instrument: XAU/USD (Gold vs. US Dollar).
* Initial Timeframe: Daily (`1day`) candles (OHLC with optional/null volume).
* Historical Period: 3 years of historical daily backfill data.
* Continuous Ingestion: Daily incremental updates scheduled following the market daily    close.
* Future Expansion: Extending coverage to additional FX pairs (e.g., EUR/USD), multi-asset classes, and lower timeframes (e.g., hourly/5-minute intervals).

## Data Source

* Provider: Twelve Data API.
* Reconnaissance & Verification:
* Validated via API reconnaissance with HTTP 200 responses for both `XAU/USD` (3-year daily backfill producing 781 candles) and `EUR/USD`.
* Verified structural stability of row-oriented OHLC JSON records formatted in ISO-8601 UTC timestamps (`YYYY-MM-DD`).
* Confirmed API responsiveness, error handling, rate-limit structures, and multi-instrument endpoint compatibility.
* Feed fields: `timestamp`, `symbol`, `open`, `high`, `low`, and `close` are mandatory; `volume` is optional and may be null.
## Pipeline Architecture

API (Twelve Data)
 ↓
Ingestion (Config, httpx, rate limits, retries, auth)
 ↓
Raw Storage (Immutable JSON payloads + ingestion metadata)
 ↓
Staging (Parse, type casting, UTC normalization, lineage metadata)
 ↓
Validation (Schema check, OHLC logic integrity, duplicate detection) ──[Invalid]──> Quarantine
 ↓
Transformation (Canonical candle data model, business logic)
 ↓
Analytics Storage (ClickHouse `fact_candles`)
 ↓
Consumers (Analyst, Quant, ML workflows)

## State Management
Execution metadata, watermarks (`pipeline_watermark`), run logs (`pipeline_run`), and data quality audit logs are maintained independently in PostgreSQL.

## Consumers

| Consumer | Purpose |
| --- | --- |
| Data Analyst | Exploratory market data analysis, reporting, and dashboarding. |
| Data Scientist | Statistical distributions, volatility analysis, and exploratory data modeling. |
| ML Engineer | Feature engineering, training sets, and model pipeline inputs. |
| Quant | Backtesting trading strategies, risk modeling, and market research. |

## Technology Stack

* Language: Python 3.11+
* HTTP Client: `httpx` (async API ingestion with retries and backoff handling)
* Object Storage / Raw & Staging Layer: MinIO / AWS S3 (Raw JSON payloads, Parquet staging format)
* Analytical Database: ClickHouse (`fact_candles` engine optimized for time-series aggregation)
* State & Metadata Store: PostgreSQL (Watermarks, run state, execution tracking)
* Orchestration & Automation: GitHub Actions
* Containerization: Docker Compose (Local development environment parity)

## Project Status

The initial repository architecture and developer configuration are in place. The package boundaries mirror the pipeline architecture, but no ingestion, storage, staging, validation, transformation, ClickHouse, or PostgreSQL implementation exists yet. See [the repository architecture](docs/architecture.md) for the package-to-architecture mapping.

The next milestone is an end-to-end vertical slice: Twelve Data API → raw object storage → Parquet staging → quality validation → ClickHouse.
