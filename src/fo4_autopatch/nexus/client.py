"""Small, rate-limit-aware client for Nexus Mods API v1.

This is the personal-key fallback used when work is not running inside Vortex.
Vortex authentication is intentionally never inspected or copied.
"""

from __future__ import annotations

import email.utils
import os
import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping
from urllib.parse import urlsplit

import requests

from ..models import ModFileInfo
from .errors import NexusAPIError, NexusError, RateLimitError
from .nxm import NXMDownloadLink

DEFAULT_BASE_URL = "https://api.nexusmods.com/v1"
_RETRYABLE = frozenset({429, 500, 502, 503, 504})


def _header_int(headers: Mapping[str, Any], name: str) -> int | None:
    value = headers.get(name)
    if value in (None, ""):
        return None
    try:
        return int(str(value))
    except ValueError:
        return None


def _parse_reset(value: Any) -> float | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        return float(text)
    except ValueError:
        pass
    try:
        return email.utils.parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.timestamp()
    except ValueError:
        return None


def _retry_after(headers: Mapping[str, Any], now: float) -> float | None:
    raw = headers.get("Retry-After")
    if raw in (None, ""):
        return None
    try:
        return max(0.0, float(str(raw)))
    except ValueError:
        parsed = _parse_reset(raw)
        return None if parsed is None else max(0.0, parsed - now)


@dataclass(slots=True)
class RateLimitSnapshot:
    """The most recent hourly and daily limits published by Nexus."""

    hourly_limit: int | None = None
    hourly_remaining: int | None = None
    hourly_reset_at: float | None = None
    daily_limit: int | None = None
    daily_remaining: int | None = None
    daily_reset_at: float | None = None
    observed_at: float | None = None

    def update(self, headers: Mapping[str, Any], *, observed_at: float) -> None:
        lowered = {str(key).lower(): value for key, value in headers.items()}
        pairs = (
            ("hourly_limit", "x-rl-hourly-limit"),
            ("hourly_remaining", "x-rl-hourly-remaining"),
            ("daily_limit", "x-rl-daily-limit"),
            ("daily_remaining", "x-rl-daily-remaining"),
        )
        for attribute, header in pairs:
            parsed = _header_int(lowered, header)
            if parsed is not None:
                setattr(self, attribute, parsed)
        hourly_reset = _parse_reset(lowered.get("x-rl-hourly-reset"))
        daily_reset = _parse_reset(lowered.get("x-rl-daily-reset"))
        if hourly_reset is not None:
            self.hourly_reset_at = hourly_reset
        if daily_reset is not None:
            self.daily_reset_at = daily_reset
        self.observed_at = observed_at

    def exhausted_until(self, now: float) -> float | None:
        resets: list[float] = []
        if self.hourly_remaining is not None and self.hourly_remaining <= 0:
            resets.append(self.hourly_reset_at if self.hourly_reset_at is not None else float("inf"))
        if self.daily_remaining is not None and self.daily_remaining <= 0:
            resets.append(self.daily_reset_at if self.daily_reset_at is not None else float("inf"))
        future = [value for value in resets if value > now]
        return max(future) if future else None

    def to_dict(self) -> dict[str, int | float | None]:
        return {
            "hourly_limit": self.hourly_limit,
            "hourly_remaining": self.hourly_remaining,
            "hourly_reset_at": self.hourly_reset_at,
            "daily_limit": self.daily_limit,
            "daily_remaining": self.daily_remaining,
            "daily_reset_at": self.daily_reset_at,
            "observed_at": self.observed_at,
        }


@dataclass(slots=True, repr=False)
class NexusClient:
    """Nexus API v1 client using a user-supplied personal API key."""

    api_key: str = field(repr=False)
    session: Any = None
    base_url: str = DEFAULT_BASE_URL
    timeout: float = 30.0
    max_retries: int = 2
    application_name: str = "for4-mod-editor"
    application_version: str = "0.1.0"
    sleeper: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.time
    max_retry_after_s: float = 30.0
    rate_limits: RateLimitSnapshot = field(default_factory=RateLimitSnapshot, init=False)

    def __post_init__(self) -> None:
        self.api_key = self.api_key.strip()
        if not self.api_key or any(ord(ch) < 33 for ch in self.api_key):
            raise NexusError("A non-empty NEXUS_API_KEY is required")
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or not parts.netloc or parts.query or parts.fragment:
            raise NexusError("Nexus API base URL must be an HTTPS origin/path without a query")
        if self.timeout <= 0 or self.max_retries < 0 or self.max_retry_after_s < 0:
            raise ValueError("Invalid Nexus client timeout or retry setting")
        if self.session is None:
            self.session = requests.Session()

    def __repr__(self) -> str:
        return (
            f"NexusClient(api_key='<redacted>', base_url={self.base_url!r}, "
            f"timeout={self.timeout!r}, max_retries={self.max_retries!r})"
        )

    @classmethod
    def from_personal_key(cls, api_key: str | None = None, **kwargs: Any) -> "NexusClient":
        """Construct the standalone fallback from an explicit or environment key."""

        key = api_key if api_key is not None else os.environ.get("NEXUS_API_KEY", "")
        return cls(key, **kwargs)

    @property
    def headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "apikey": self.api_key,
            "Application-Name": self.application_name,
            "Application-Version": self.application_version,
        }

    def _preflight_rate_limit(self) -> None:
        now = self.clock()
        reset_at = self.rate_limits.exhausted_until(now)
        if reset_at is not None:
            retry_after = None if reset_at == float("inf") else max(0.0, reset_at - now)
            raise RateLimitError(429, "published API budget is exhausted", retry_after_s=retry_after)

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int] | None = None,
    ) -> Any:
        if not re.fullmatch(r"[A-Za-z0-9_./-]+", path) or ".." in path.split("/"):
            raise ValueError("Unsafe Nexus API path")
        self._preflight_rate_limit()
        url = f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"

        for attempt in range(self.max_retries + 1):
            try:
                response = self.session.request(
                    method.upper(),
                    url,
                    headers=self.headers,
                    params=dict(params or {}),
                    timeout=self.timeout,
                )
            except requests.RequestException as exc:
                if attempt >= self.max_retries:
                    raise NexusError(f"Nexus API request failed: {type(exc).__name__}") from exc
                self.sleeper(min(2**attempt, self.max_retry_after_s))
                continue

            now = self.clock()
            self.rate_limits.update(response.headers, observed_at=now)
            status = int(response.status_code)
            if 200 <= status < 300:
                try:
                    return response.json()
                except (TypeError, ValueError) as exc:
                    raise NexusAPIError(status, "response was not valid JSON") from exc

            wait_s = _retry_after(response.headers, now)
            if status in _RETRYABLE and attempt < self.max_retries and (wait_s is None or wait_s <= self.max_retry_after_s):
                self.sleeper(wait_s if wait_s is not None else min(2**attempt, self.max_retry_after_s))
                continue
            error_type = RateLimitError if status == 429 else NexusAPIError
            raise error_type(status, "request failed", retry_after_s=wait_s)

        raise AssertionError("unreachable")

    def validate_user(self) -> Mapping[str, Any]:
        payload = self.request_json("GET", "users/validate.json")
        if not isinstance(payload, Mapping):
            raise NexusAPIError(200, "user validation response had the wrong shape")
        return payload

    def list_mod_files(self, game: str, mod_id: int) -> list[ModFileInfo]:
        game = _validate_game(game)
        mod_id = _positive_id(mod_id, "mod_id")
        payload = self.request_json("GET", f"games/{game}/mods/{mod_id}/files.json")
        raw_files = payload.get("files", []) if isinstance(payload, Mapping) else []
        if not isinstance(raw_files, list):
            raise NexusAPIError(200, "mod-files response had the wrong shape")
        return [_mod_file(mod_id, raw) for raw in raw_files if isinstance(raw, Mapping)]

    def download_links(
        self,
        game: str,
        mod_id: int,
        file_id: int,
        *,
        nxm_link: NXMDownloadLink | None = None,
    ) -> list[str]:
        game = _validate_game(game)
        mod_id = _positive_id(mod_id, "mod_id")
        file_id = _positive_id(file_id, "file_id")
        params: Mapping[str, str] | None = None
        if nxm_link is not None:
            if not nxm_link.matches(game=game, mod_id=mod_id, file_id=file_id):
                raise NexusError("NXM authorization does not match the requested Nexus file")
            if nxm_link.expires <= int(self.clock()):
                raise NexusError("NXM authorization expired before the download-link request")
            params = nxm_link.authorization_params
        payload = self.request_json(
            "GET",
            f"games/{game}/mods/{mod_id}/files/{file_id}/download_link.json",
            params=params,
        )
        if not isinstance(payload, list):
            raise NexusAPIError(200, "download-link response had the wrong shape")
        urls: list[str] = []
        for raw in payload:
            if not isinstance(raw, Mapping):
                continue
            value = raw.get("URI") or raw.get("uri")
            if not isinstance(value, str) or len(value) > 8192 or any(ord(ch) < 32 for ch in value):
                continue
            parsed = urlsplit(value)
            if parsed.scheme == "https" and parsed.netloc:
                urls.append(value)
        if not urls:
            raise NexusAPIError(200, "Nexus returned no usable HTTPS download mirrors")
        return urls


def _validate_game(game: str) -> str:
    value = str(game).casefold()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", value):
        raise ValueError("Invalid Nexus game domain")
    return value


def _positive_id(value: int, name: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be positive")
    return parsed


def _mod_file(mod_id: int, raw: Mapping[str, Any]) -> ModFileInfo:
    file_id = _positive_id(int(raw.get("file_id", 0)), "file_id")
    size = raw.get("size_in_bytes")
    if size is None:
        size = int(raw.get("size_kb") or 0) * 1024
    uploaded_at = raw.get("uploaded_time") or raw.get("uploaded_at")
    if uploaded_at is None and raw.get("uploaded_timestamp") is not None:
        try:
            uploaded_at = datetime.fromtimestamp(int(raw["uploaded_timestamp"]), UTC).isoformat(timespec="seconds")
        except (OverflowError, TypeError, ValueError):
            uploaded_at = None
    return ModFileInfo(
        mod_id=mod_id,
        file_id=file_id,
        name=str(raw.get("name") or raw.get("file_name") or f"File {file_id}"),
        file_name=str(raw.get("file_name") or raw.get("name") or f"{file_id}.archive"),
        version=str(raw.get("version") or ""),
        category=str(raw.get("category_name") or raw.get("category") or raw.get("category_id") or "unknown"),
        size_bytes=max(0, int(size or 0)),
        uploaded_at=str(uploaded_at) if uploaded_at not in (None, "") else None,
        md5=str(raw["md5"]) if raw.get("md5") not in (None, "") else None,
    )
