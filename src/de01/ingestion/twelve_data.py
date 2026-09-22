"""Twelve Data time-series client."""

from collections.abc import Mapping

import httpx

from de01.ingestion.exceptions import TwelveDataRequestError, TwelveDataResponseError
from de01.ingestion.twelve_data_parser import parse_time_series_response
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

    def fetch_time_series(self, symbol: str, interval: str) -> list[MarketData]:
        """Fetch XAU/USD candles for a supported interval."""
        self._validate_request(symbol, interval)
        response_data = self._request_time_series(symbol, interval)
        return parse_time_series_response(response_data, symbol=symbol, interval=interval)

    def _validate_request(self, symbol: str, interval: str) -> None:
        if symbol != _SUPPORTED_SYMBOL:
            raise ValueError(f"Only {_SUPPORTED_SYMBOL!r} is supported")
        if interval not in _SUPPORTED_INTERVALS:
            raise ValueError(f"Unsupported interval {interval!r}; expected '1h' or '1day'")

    def _request_time_series(self, symbol: str, interval: str) -> Mapping[str, object]:
        params = {"symbol": symbol, "interval": interval}
        if interval == "1h":
            params["timezone"] = "UTC"

        try:
            if self._client is not None:
                response = self._client.get(
                    "/time_series", params=params, headers=self._headers(), timeout=10.0
                )
            else:
                with httpx.Client(base_url=_BASE_URL, timeout=10.0) as client:
                    response = client.get("/time_series", params=params, headers=self._headers())
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
