"""Exceptions raised by the Nexus integration boundary."""

from __future__ import annotations


class NexusError(RuntimeError):
    """Base class for operational Nexus failures."""


class NexusAPIError(NexusError):
    """A Nexus API request failed without exposing its authorization data."""

    def __init__(self, status_code: int, message: str, *, retry_after_s: float | None = None) -> None:
        self.status_code = int(status_code)
        self.retry_after_s = retry_after_s
        super().__init__(f"Nexus API returned HTTP {self.status_code}: {message}")


class RateLimitError(NexusAPIError):
    """A known hourly/daily API budget is exhausted."""


class NXMURLValidationError(NexusError, ValueError):
    """An NXM link is malformed, expired, or for an unexpected file."""


class DownloadError(NexusError):
    """A resumable transfer could not be completed or verified."""
