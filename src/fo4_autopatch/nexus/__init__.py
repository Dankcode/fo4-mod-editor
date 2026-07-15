"""Nexus API, NXM authorization, update, queue, and transfer helpers."""

from .client import DEFAULT_BASE_URL, NexusClient, RateLimitSnapshot
from .downloads import DownloadResult, DownloadSpec, SequentialDownloader
from .errors import DownloadError, NexusAPIError, NexusError, NXMURLValidationError, RateLimitError
from .nxm import NXMDownloadLink, parse_nxm_url, redact_nxm_url
from .queue import AuthorizedDownload, DownloadQueue, QueueItem
from .updates import (
    UNKNOWN,
    UPDATE_AVAILABLE,
    UP_TO_DATE,
    UpdateDecision,
    compare_installed_mod,
    compare_updates,
    compare_versions,
)

__all__ = [
    "AuthorizedDownload",
    "DEFAULT_BASE_URL",
    "DownloadError",
    "DownloadQueue",
    "DownloadResult",
    "DownloadSpec",
    "NXMDownloadLink",
    "NXMURLValidationError",
    "NexusAPIError",
    "NexusClient",
    "NexusError",
    "QueueItem",
    "RateLimitError",
    "RateLimitSnapshot",
    "SequentialDownloader",
    "UNKNOWN",
    "UPDATE_AVAILABLE",
    "UP_TO_DATE",
    "UpdateDecision",
    "compare_installed_mod",
    "compare_updates",
    "compare_versions",
    "parse_nxm_url",
    "redact_nxm_url",
]
