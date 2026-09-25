"""Twelve Data time-series client."""

from collections.abc import Mapping
from datetime import datetime

import httpx

from de01.ingestion.exceptions import TwelveDataRequestError, TwelveDataResponseError
from de01.ingestion.twelve_data_parser import (
    parse_earliest_timestamp_response,
    parse_time_series_response,
)
from de01.market_data import MarketData

_BASE_URL = "https://api.twelvedata.com"
_SUPPORTED_SYMBOL = "XAU/USD"
_SUPPORTED_INTERVALS = frozenset({"1h", "1day"})


class TwelveDataClient:
    """Fetch supported Twelve Data candles and return canonical market data."""

    def __init__(self, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be a non-empty string")
        self._api_key = api_key.strip()
        self._client = client

    def fetch_time_series(
        self,
        symbol: str,
        interval: str,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        order: str | None = None,
    ) -> list[MarketData]:
        """Fetch XAU/USD candles for a supported interval."""
        self._validate_request(symbol, interval)
        params = {"symbol": symbol, "interval": interval}
        if start_date is not None:
            params["start_date"] = start_date
        if end_date is not None:
            params["end_date"] = end_date
        if order is not None:
            params["order"] = order
        if interval == "1h":
            params["timezone"] = "UTC"
        response_data = self._request("/time_series", params)
        return parse_time_series_response(response_data, symbol=symbol, interval=interval)

    def fetch_earliest_timestamp(self, symbol: str, interval: str) -> datetime:
        """Return the provider's earliest available candle as a UTC instant."""
        self._validate_request(symbol, interval)
        response_data = self._request(
            "/earliest_timestamp", {"symbol": symbol, "interval": interval}
        )
        return parse_earliest_timestamp_response(response_data, interval=interval)

    def _validate_request(self, symbol: str, interval: str) -> None:
        if symbol != _SUPPORTED_SYMBOL:
            raise ValueError(f"Only {_SUPPORTED_SYMBOL!r} is supported")
        if interval not in _SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval {interval!r}; expected '1h' or '1day'")

    def _request(self, path: str, params: dict[str, str]) -> Mapping[str, object]:
        try:
            if self._client is not None:
                response = self._client.get(
                    path, params=params, headers=self._headers(), timeout=10.0
                )
            else:
                with httpx.Client(base_url=_BASE_URL, timeout=10.0) as client:
                    response = client.get(path, params=params, headers=self._headers())
            response.raise_for_status()
        except httpx.RequestError as exc:
            raise TwelveDataRequestError("Twelve Data request failed") from exc
        except httpx.HTTPStatusError as exc:
            raise TwelveDataResponseError(
                f"Twelve Data returned HTTP {exc.response.status_code}"
            ) from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise TwelveDataResponseError("Twelve Data response was not valid JSON") from exc
        if not isinstance(payload, Mapping):
            raise TwelveDataResponseError("Twelve Data response must be a JSON object")
        if payload.get("status") == "error":
            code = payload.get("code", "unknown")
            message = payload.get("message", "No provider error message supplied")
            raise TwelveDataResponseError(f"Twelve Data error {code}: {message}")
        return payload

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"apikey {self._api_key}"}
