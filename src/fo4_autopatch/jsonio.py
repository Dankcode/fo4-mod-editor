"""Atomic JSON I/O and redaction helpers."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlsplit, urlunsplit

SECRET_KEYS = {"apikey", "api_key", "key", "token", "authorization", "expires", "user_id"}


class Encoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if hasattr(obj, "to_dict"):
            return obj.to_dict()
        if is_dataclass(obj):
            return asdict(obj)
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        return super().default(obj)


def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme:
        return value
    return urlunsplit((parts.scheme, parts.netloc, parts.path, "<redacted>" if parts.query else "", ""))


def redact(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(k): ("<redacted>" if str(k).lower() in SECRET_KEYS else redact(v)) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(v) for v in value]
    if isinstance(value, str):
        if value.lower().startswith(("nxm://", "https://", "http://")) and ("key=" in value.lower() or "token=" in value.lower()):
            return redact_url(value)
        return re.sub(r"(?i)(apikey|authorization|token)\s*[:=]\s*\S+", r"\1=<redacted>", value)
    return value


def dumps(value: Any, *, pretty: bool = True) -> str:
    return json.dumps(redact(value), cls=Encoder, indent=2 if pretty else None, sort_keys=pretty, ensure_ascii=False)


def atomic_write(path: Path, value: Any) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dumps(value) + "\n"
    handle, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    except BaseException:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return path


def load(path: Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError(f"Expected a JSON object in {path}")
    return value

