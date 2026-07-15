"""Sequential, resumable HTTP transfers for Nexus-hosted archives."""

from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlsplit

import requests

from ..jsonio import redact_url
from .errors import DownloadError

_CONTENT_RANGE = re.compile(r"^bytes\s+(\d+)-(\d+)/(\d+|\*)$", re.IGNORECASE)
_RETRYABLE = frozenset({408, 425, 429, 500, 502, 503, 504})


@dataclass(frozen=True, slots=True, repr=False)
class DownloadSpec:
    url: str = field(repr=False)
    destination: Path
    expected_size: int | None = None
    md5: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        parts = urlsplit(self.url)
        if parts.scheme != "https" or not parts.netloc or any(ord(ch) < 32 for ch in self.url):
            raise ValueError("Download URL must be a valid HTTPS URL")
        if self.expected_size is not None and self.expected_size < 0:
            raise ValueError("expected_size cannot be negative")
        if self.md5 is not None and not re.fullmatch(r"[0-9a-fA-F]{32}", self.md5):
            raise ValueError("md5 must contain 32 hexadecimal characters")
        object.__setattr__(self, "destination", Path(self.destination))

    def __repr__(self) -> str:
        return (
            f"DownloadSpec(url={redact_url(self.url)!r}, destination={self.destination!r}, "
            f"expected_size={self.expected_size!r}, md5={'<set>' if self.md5 else None!r})"
        )


@dataclass(frozen=True, slots=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    resumed: bool
    attempts: int
    from_cache: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "size_bytes": self.size_bytes,
            "sha256": self.sha256,
            "resumed": self.resumed,
            "attempts": self.attempts,
            "from_cache": self.from_cache,
        }


class SequentialDownloader:
    """Download one archive at a time, resuming a ``.part`` file with Range."""

    def __init__(
        self,
        *,
        session: Any = None,
        timeout: tuple[float, float] = (30.0, 120.0),
        max_retries: int = 3,
        chunk_size: int = 1024 * 1024,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if max_retries < 0 or chunk_size <= 0 or min(timeout) <= 0:
            raise ValueError("Invalid downloader retry, chunk, or timeout setting")
        self.session = session if session is not None else requests.Session()
        self.timeout = timeout
        self.max_retries = max_retries
        self.chunk_size = chunk_size
        self.sleeper = sleeper

    def download_all(self, specs: Iterable[DownloadSpec]) -> list[DownloadResult]:
        """Process the supplied order without concurrent connections."""

        return [self.download(spec) for spec in specs]

    def download(self, spec: DownloadSpec) -> DownloadResult:
        destination = spec.destination.expanduser().resolve(strict=False)
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_name(f"{destination.name}.part")
        _reject_symlink(destination)
        _reject_symlink(partial)

        if destination.is_file():
            if spec.expected_size is None and spec.md5 is None:
                raise DownloadError(f"Destination already exists and cannot be verified: {destination}")
            self._verify(destination, spec)
            return _result(destination, resumed=False, attempts=0, from_cache=True)
        if destination.exists():
            raise DownloadError(f"Download destination is not a regular file: {destination}")

        resumed_any = partial.is_file() and partial.stat().st_size > 0
        last_error: BaseException | None = None
        for attempt in range(1, self.max_retries + 2):
            offset = partial.stat().st_size if partial.is_file() else 0
            headers = {"Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            response = None
            try:
                response = self.session.get(
                    spec.url,
                    headers=headers,
                    stream=True,
                    timeout=self.timeout,
                    allow_redirects=True,
                )
                status = int(response.status_code)

                if status == 416 and offset and spec.expected_size == offset:
                    self._verify(partial, spec)
                    os.replace(partial, destination)
                    return _result(destination, resumed=True, attempts=attempt)
                if status in _RETRYABLE:
                    if attempt > self.max_retries:
                        raise DownloadError(f"Download server returned HTTP {status}")
                    self.sleeper(_response_delay(response, attempt))
                    continue
                if status not in {200, 206}:
                    raise DownloadError(f"Download server returned HTTP {status}")

                append = status == 206 and offset > 0
                if status == 206:
                    content_range = str(response.headers.get("Content-Range", ""))
                    match = _CONTENT_RANGE.fullmatch(content_range)
                    if match is None or int(match.group(1)) != offset:
                        raise DownloadError("Download server returned an inconsistent Content-Range")
                    total = match.group(3)
                    if spec.expected_size is not None and total != "*" and int(total) != spec.expected_size:
                        raise DownloadError("Download server reported an unexpected archive size")
                elif offset:
                    # The mirror ignored Range. Restart from its complete 200 response.
                    append = False

                mode = "ab" if append else "wb"
                with partial.open(mode) as stream:
                    for chunk in response.iter_content(chunk_size=self.chunk_size):
                        if not chunk:
                            continue
                        if not isinstance(chunk, (bytes, bytearray)):
                            raise DownloadError("Download stream returned non-byte data")
                        stream.write(chunk)
                    stream.flush()
                    os.fsync(stream.fileno())

                self._verify(partial, spec)
                os.replace(partial, destination)
                return _result(destination, resumed=resumed_any and append, attempts=attempt)
            except (requests.RequestException, OSError) as exc:
                last_error = exc
                if attempt > self.max_retries:
                    break
                self.sleeper(min(2 ** (attempt - 1), 30))
            finally:
                if response is not None:
                    close = getattr(response, "close", None)
                    if callable(close):
                        close()

        name = type(last_error).__name__ if last_error is not None else "unknown error"
        raise DownloadError(f"Download did not complete after {self.max_retries + 1} attempts ({name})") from last_error

    @staticmethod
    def _verify(path: Path, spec: DownloadSpec) -> None:
        if spec.expected_size is not None and path.stat().st_size != spec.expected_size:
            raise DownloadError(
                f"Downloaded size mismatch for {path.name}: expected {spec.expected_size}, got {path.stat().st_size}"
            )
        if spec.md5 is not None and _hash_file(path, "md5") != spec.md5.casefold():
            raise DownloadError(f"Downloaded MD5 mismatch for {path.name}")


def _response_delay(response: Any, attempt: int) -> float:
    raw = response.headers.get("Retry-After")
    if raw not in (None, ""):
        try:
            return min(max(0.0, float(raw)), 30.0)
        except ValueError:
            pass
    return float(min(2 ** (attempt - 1), 30))


def _reject_symlink(path: Path) -> None:
    if path.is_symlink():
        raise DownloadError(f"Refusing to use a symlink as a download file: {path}")


def _hash_file(path: Path, algorithm: str) -> str:
    digest = hashlib.new(algorithm)
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _result(path: Path, *, resumed: bool, attempts: int, from_cache: bool = False) -> DownloadResult:
    return DownloadResult(
        path=path,
        size_bytes=path.stat().st_size,
        sha256=_hash_file(path, "sha256"),
        resumed=resumed,
        attempts=attempts,
        from_cache=from_cache,
    )
