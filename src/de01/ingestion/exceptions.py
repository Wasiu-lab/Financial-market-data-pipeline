"""Exceptions raised by the Twelve Data ingestion boundary."""


class TwelveDataError(Exception):
    """Base exception for Twelve Data ingestion failures."""


class TwelveDataRequestError(TwelveDataError):
    """Raised when a request cannot be completed successfully."""


class TwelveDataResponseError(TwelveDataError):
    """Raised when Twelve Data returns an error response."""


class TwelveDataParseError(TwelveDataError):
    """Raised when a successful response cannot be parsed into market data."""
