# Repository architecture

This repository uses a standard Python `src` layout. Application code belongs under
`src/de01`; tests live under `tests` and are separated into unit and integration suites.

The package layout maps directly to the logical flow described in the
[README](../README.md#pipeline-architecture). The directories establish ownership
boundaries only; they intentionally contain no pipeline implementation yet.

| Package | Architectural responsibility |
| --- | --- |
| `de01.config` | Shared application configuration boundary. |
| `de01.ingestion` | Data-source ingestion boundary, corresponding to the API ingestion stage. |
| `de01.storage` | Raw and analytics storage boundary. |
| `de01.staging` | Parsing, type-normalisation, and lineage staging boundary. |
| `de01.validation` | Data-quality and quarantine decision boundary. |
| `de01.transformation` | Canonical candle-model and business-transformation boundary. |
| `de01.analytics` | Analytics-facing dataset boundary for downstream consumers. |
| `de01.state` | Execution metadata, watermarks, run logs, and quality-audit boundary. |

The initial feed is XAU/USD daily candle data: `timestamp`, `symbol`, and the OHLC
fields are required; `volume` is optional and may be null. Provider integrations,
storage engines, data processing, and state persistence will be added in later work.
