"""Versioned local JSON queue consumed by the optional Vortex extension."""

from __future__ import annotations

import argparse
import re
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..jsonio import atomic_write, dumps, load
from ..models import utc_now

BRIDGE_SCHEMA_VERSION = 1
_REQUEST_ID = re.compile(r"^[a-f0-9]{32}$")


@dataclass(frozen=True, slots=True)
class BridgeRequest:
    request_id: str
    archive_path: Path
    install: bool = True
    deploy: bool = False
    game_id: str = "fallout4"
    profile_id: str | None = None
    created_at: str = ""

    def __post_init__(self) -> None:
        if not _REQUEST_ID.fullmatch(self.request_id):
            raise ValueError("Invalid bridge request ID")
        if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", self.game_id):
            raise ValueError("Invalid Vortex game ID")
        if self.profile_id is not None and (
            not self.profile_id or len(self.profile_id) > 256 or any(ord(ch) < 32 for ch in self.profile_id)
        ):
            raise ValueError("Invalid Vortex profile ID")
        if self.deploy and not self.install:
            raise ValueError("A bridge request cannot deploy an archive that it did not install")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "kind": "vortex_bridge_request",
            "id": self.request_id,
            "action": "import_archive",
            "game_id": self.game_id,
            "archive_path": str(self.archive_path),
            "install": self.install,
            "deploy": self.deploy,
            "profile_id": self.profile_id,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any], *, require_archive: bool = True) -> "BridgeRequest":
        if raw.get("schema_version") != BRIDGE_SCHEMA_VERSION:
            raise ValueError("Unsupported Vortex bridge schema version")
        if raw.get("kind") != "vortex_bridge_request" or raw.get("action") != "import_archive":
            raise ValueError("Unsupported Vortex bridge request kind/action")
        archive = Path(str(raw.get("archive_path", ""))).expanduser().resolve(strict=False)
        if not archive.is_absolute() or archive.suffix.casefold() != ".zip":
            raise ValueError("Bridge archive must be an absolute ZIP path")
        if require_archive and (not archive.is_file() or archive.is_symlink()):
            raise ValueError(f"Bridge archive is missing or unsafe: {archive}")
        return cls(
            request_id=str(raw.get("id", "")),
            archive_path=archive,
            install=bool(raw.get("install", True)),
            deploy=bool(raw.get("deploy", False)),
            game_id=str(raw.get("game_id", "fallout4")).casefold(),
            profile_id=str(raw["profile_id"]) if raw.get("profile_id") not in (None, "") else None,
            created_at=str(raw.get("created_at", "")),
        )


class BridgeQueue:
    """Submit archive imports without reading or changing Vortex's private state."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.requests_dir = self.root / "requests"
        self.results_dir = self.root / "results"

    def ensure_dirs(self) -> None:
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        self.results_dir.mkdir(parents=True, exist_ok=True)

    def submit_archive(
        self,
        archive_path: Path,
        *,
        install: bool = True,
        deploy: bool = False,
        profile_id: str | None = None,
        game_id: str = "fallout4",
    ) -> BridgeRequest:
        archive = Path(archive_path).expanduser().resolve(strict=True)
        if not archive.is_file() or archive.is_symlink() or archive.suffix.casefold() != ".zip":
            raise ValueError("Vortex bridge accepts only an existing, non-symlink ZIP archive")
        request = BridgeRequest(
            request_id=uuid.uuid4().hex,
            archive_path=archive,
            install=bool(install),
            deploy=bool(deploy),
            game_id=str(game_id).casefold(),
            profile_id=profile_id,
            created_at=utc_now(),
        )
        self.ensure_dirs()
        atomic_write(self.requests_dir / f"{request.request_id}.json", request.to_dict())
        return request

    def pending(self) -> list[BridgeRequest]:
        if not self.requests_dir.exists():
            return []
        result: list[BridgeRequest] = []
        for path in sorted(self.requests_dir.glob("*.json"), key=lambda item: item.name):
            if not _REQUEST_ID.fullmatch(path.stem):
                continue
            result.append(BridgeRequest.from_dict(load(path)))
        return result

    def result(self, request_id: str) -> dict[str, Any] | None:
        request_id = _validated_id(request_id)
        path = self.results_dir / f"{request_id}.json"
        if not path.is_file():
            return None
        raw = load(path)
        if raw.get("schema_version") != BRIDGE_SCHEMA_VERSION or raw.get("kind") != "vortex_bridge_result":
            raise ValueError(f"Malformed Vortex bridge result: {path}")
        if raw.get("id") != request_id:
            raise ValueError(f"Mismatched Vortex bridge result ID: {path}")
        return raw


def validate_request_file(path: Path) -> BridgeRequest:
    return BridgeRequest.from_dict(load(Path(path)))


def _validated_id(value: str) -> str:
    if not _REQUEST_ID.fullmatch(str(value)):
        raise ValueError("Invalid bridge request ID")
    return str(value)


def main(argv: list[str] | None = None) -> int:
    """Validation hook used by the extension before it emits Vortex events."""

    parser = argparse.ArgumentParser(prog="python -m fo4_autopatch.vortex.bridge")
    parser.add_argument("command", choices=["validate"])
    parser.add_argument("request", type=Path)
    args = parser.parse_args(argv)
    try:
        request = validate_request_file(args.request)
    except (OSError, ValueError) as exc:
        print(dumps({"ok": False, "error": str(exc)}, pretty=False), file=sys.stderr)
        return 2
    print(dumps({"ok": True, "id": request.request_id}, pretty=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
