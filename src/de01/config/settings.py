"""Configuration helpers for DE-01."""

import os


def get_twelve_data_api_key() -> str:
    """Return the configured Twelve Data API key."""
    api_key = os.environ.get("TWELVE_DATA_API_KEY", "").strip()
    if not api_key:
        raise ValueError("TWELVE_DATA_API_KEY must be set to a non-empty value")
    return api_key
