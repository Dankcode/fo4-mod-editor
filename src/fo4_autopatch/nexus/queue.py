"""Persistent free-account queue with ephemeral manual NXM authorization."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from ..config import validate_archive_name
from ..jsonio import atomic_write, load, redact
from ..models import utc_now
from .errors import NexusError
from .nxm import NXMDownloadLink, parse_nxm_url

QUEUE_SCHEMA_VERSION = 1
AWAITING_LINK = "awaiting_manual_link"
COMPLETED = "completed"
FAILED = "failed"


@dataclass(frozen=True, slots=True)
class QueueItem:
    item_id: str
    game: str
    mod_id: int
    file_id: int
    file_name: str
    state: str = AWAITING_LINK
    created_at: str = ""
    updated_at: str = ""
    attempts: int = 0
    last_error: str | None = None
    downloaded_path: Path | None = None

    @property
    def needs_manual_link(self) -> bool:
        return self.state != COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.item_id,
            "game": self.game,
            "mod_id": self.mod_id,
            "file_id": self.file_id,
            "file_name": self.file_name,
            "state": self.state,
            "manual_link_required": self.needs_manual_link,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "attempts": self.attempts,
            "last_error": self.last_error,
            "downloaded_path": str(self.downloaded_path) if self.downloaded_path else None,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "QueueItem":
        return cls(
            item_id=str(raw["id"]),
            game=str(raw["game"]),
            mod_id=int(raw["mod_id"]),
            file_id=int(raw["file_id"]),
            file_name=str(raw["file_name"]),
            state=str(raw.get("state", AWAITING_LINK)),
            created_at=str(raw.get("created_at", "")),
            updated_at=str(raw.get("updated_at", "")),
            attempts=max(0, int(raw.get("attempts", 0))),
            last_error=str(raw["last_error"]) if raw.get("last_error") else None,
            downloaded_path=Path(str(raw["downloaded_path"])) if raw.get("downloaded_path") else None,
        )


@dataclass(frozen=True, slots=True)
class AuthorizedDownload:
    """A queue item paired with authorization that is never written to disk."""

    item: QueueItem
    nxm_link: NXMDownloadLink


class DownloadQueue:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def items(self) -> list[QueueItem]:
        raw = self._read()
        result = [QueueItem.from_dict(value) for value in raw.get("items", []) if isinstance(value, Mapping)]
        return sorted(result, key=lambda item: (item.created_at, item.item_id))

    def get(self, item_id: str) -> QueueItem:
        for item in self.items():
            if item.item_id == item_id:
                return item
        raise NexusError(f"Unknown Nexus download queue item: {item_id}")

    def enqueue(self, game: str, mod_id: int, file_id: int, file_name: str) -> QueueItem:
        game = _game(game)
        mod_id = _positive(mod_id, "mod_id")
        file_id = _positive(file_id, "file_id")
        file_name = validate_archive_name(file_name)
        existing = [
            item
            for item in self.items()
            if item.game == game and item.mod_id == mod_id and item.file_id == file_id and item.state != COMPLETED
        ]
        if existing:
            return existing[0]
        now = utc_now()
        item = QueueItem(
            item_id=uuid.uuid4().hex,
            game=game,
            mod_id=mod_id,
            file_id=file_id,
            file_name=file_name,
            created_at=now,
            updated_at=now,
        )
        self._replace_or_append(item)
        return item

    def authorize(self, item_id: str, nxm_url: str, *, now: float | None = None) -> AuthorizedDownload:
        """Validate the user's manual link without persisting its query values."""

        item = self.get(item_id)
        if item.state == COMPLETED:
            raise NexusError("Download queue item is already complete")
        link = parse_nxm_url(nxm_url, expected_game=item.game, now=now)
        if not link.matches(game=item.game, mod_id=item.mod_id, file_id=item.file_id):
            raise NexusError("Manual NXM link is for a different queued file")
        return AuthorizedDownload(item=item, nxm_link=link)

    def mark_complete(self, item_id: str, downloaded_path: Path) -> QueueItem:
        path = Path(downloaded_path).expanduser().resolve(strict=False)
        if not path.is_file():
            raise NexusError(f"Cannot complete queue item without a downloaded file: {path}")
        item = replace(
            self.get(item_id),
            state=COMPLETED,
            updated_at=utc_now(),
            downloaded_path=path,
            last_error=None,
        )
        self._replace_or_append(item)
        return item

    def mark_failed(self, item_id: str, error: BaseException | str) -> QueueItem:
        current = self.get(item_id)
        safe_error = redact(str(error))
        item = replace(
            current,
            state=FAILED,
            updated_at=utc_now(),
            attempts=current.attempts + 1,
            last_error=str(safe_error)[:500],
        )
        self._replace_or_append(item)
        return item

    def requeue(self, item_id: str) -> QueueItem:
        item = replace(self.get(item_id), state=AWAITING_LINK, updated_at=utc_now(), last_error=None)
        self._replace_or_append(item)
        return item

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": QUEUE_SCHEMA_VERSION, "kind": "nexus_download_queue", "items": []}
        raw = load(self.path)
        if raw.get("schema_version") != QUEUE_SCHEMA_VERSION or raw.get("kind") != "nexus_download_queue":
            raise NexusError(f"Unsupported Nexus download queue format: {self.path}")
        if not isinstance(raw.get("items", []), list):
            raise NexusError(f"Malformed Nexus download queue: {self.path}")
        return raw

    def _replace_or_append(self, item: QueueItem) -> None:
        items = self.items()
        for index, current in enumerate(items):
            if current.item_id == item.item_id:
                items[index] = item
                break
        else:
            items.append(item)
        atomic_write(
            self.path,
            {
                "schema_version": QUEUE_SCHEMA_VERSION,
                "kind": "nexus_download_queue",
                "updated_at": utc_now(),
                "items": [value.to_dict() for value in items],
            },
        )


def _positive(value: int, name: str) -> int:
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _game(value: str) -> str:
    result = str(value).casefold()
    if not result or not all(ch.isalnum() or ch in "_-" for ch in result):
        raise ValueError("Invalid Nexus game domain")
    return result
