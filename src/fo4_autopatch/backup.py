"""Content-addressed checkpoints and verified restoration."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Iterable

from .config import Config, ConfigError
from .jsonio import atomic_write, load

MANIFEST_VERSION = 1


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_label(label: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", label).strip("-.")
    return (result or "checkpoint")[:64]


def _source_paths(cfg: Config, plugins: Iterable[str]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()
    for plugin in plugins:
        path = cfg.plugin_path(plugin, require_exists=True)
        key = os.path.normcase(str(path))
        if key not in seen:
            result.append(path)
            seen.add(key)
    if cfg.plugins_txt.is_file():
        key = os.path.normcase(str(cfg.plugins_txt.resolve(strict=False)))
        if key not in seen:
            result.append(cfg.plugins_txt.resolve(strict=False))
    return result


def checkpoint(cfg: Config, plugins: list[str], label: str) -> Path:
    if not plugins:
        raise ConfigError("A checkpoint requires at least one plugin")
    cfg.ensure_work_dirs()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    backup_dir = cfg.work_dir / "backups" / f"{timestamp}_{_safe_label(label)}"
    objects_dir = backup_dir / "objects"
    objects_dir.mkdir(parents=True, exist_ok=False)

    entries: list[dict[str, Any]] = []
    try:
        for source in _source_paths(cfg, plugins):
            digest = sha256_file(source)
            object_name = f"{digest}_{source.name}"
            target = objects_dir / object_name
            if not target.exists():
                shutil.copy2(source, target)
            entries.append(
                {
                    "source": str(source),
                    "object": str(Path("objects") / object_name),
                    "sha256": digest,
                    "size": source.stat().st_size,
                    "is_symlink": source.is_symlink(),
                }
            )
        manifest = {
            "manifest_version": MANIFEST_VERSION,
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "label": label,
            "game_data_dir": str(cfg.game_data_dir),
            "plugins_txt": str(cfg.plugins_txt),
            "entries": entries,
        }
        atomic_write(backup_dir / "manifest.json", manifest)
    except BaseException:
        shutil.rmtree(backup_dir, ignore_errors=True)
        raise
    return backup_dir


def inspect_restore(backup_dir: Path) -> list[dict[str, Any]]:
    root = Path(backup_dir).resolve(strict=True)
    manifest = load(root / "manifest.json")
    if manifest.get("manifest_version") != MANIFEST_VERSION:
        raise ConfigError(f"Unsupported backup manifest version in {root}")
    data_root = Path(str(manifest.get("game_data_dir", ""))).resolve(strict=False)
    plugins_txt = Path(str(manifest.get("plugins_txt", ""))).resolve(strict=False)
    changes: list[dict[str, Any]] = []
    for raw in manifest.get("entries", []):
        source = Path(str(raw["source"])).resolve(strict=False)
        if source != plugins_txt:
            try:
                source.relative_to(data_root)
            except ValueError as exc:
                raise ConfigError(f"Backup restore target is outside the recorded profile: {source}") from exc
        obj = (root / str(raw["object"])).resolve(strict=True)
        try:
            obj.relative_to(root)
        except ValueError as exc:
            raise ConfigError("Backup manifest object escapes its backup directory") from exc
        expected = str(raw["sha256"])
        if sha256_file(obj) != expected:
            raise ConfigError(f"Backup object failed SHA-256 verification: {obj}")
        current = sha256_file(source) if source.is_file() else None
        changes.append({"source": source, "object": obj, "backup_sha256": expected, "current_sha256": current, "changed": current != expected})
    return changes


def restore(backup_dir: Path) -> list[Path]:
    changes = inspect_restore(backup_dir)
    pending = [item for item in changes if item["changed"]]
    if not pending:
        return []
    restored: list[Path] = []
    for item in pending:
        source: Path = item["source"]
        obj: Path = item["object"]
        source.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(prefix=f".{source.name}.", suffix=".restore", dir=source.parent)
        os.close(handle)
        temp_path = Path(temp_name)
        try:
            shutil.copy2(obj, temp_path)
            if sha256_file(temp_path) != item["backup_sha256"]:
                raise ConfigError(f"Temporary restore verification failed: {source}")
            os.replace(temp_path, source)
            restored.append(source)
        finally:
            temp_path.unlink(missing_ok=True)
    return restored


def prune(cfg: Config, keep_last: int = 10) -> list[Path]:
    if keep_last < 1:
        raise ConfigError("keep_last must be at least 1")
    root = cfg.work_dir / "backups"
    if not root.exists():
        return []
    candidates = sorted((p for p in root.iterdir() if p.is_dir() and (p / "manifest.json").is_file()), key=lambda p: p.name, reverse=True)
    removed: list[Path] = []
    for path in candidates[keep_last:]:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve(strict=True))
        shutil.rmtree(resolved)
        removed.append(resolved)
    return removed
