"""Build ordinary, deterministic archives for Vortex's Install From File flow."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping
from xml.sax.saxutils import escape

from ..config import validate_archive_name
from ..jsonio import redact

_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)


@dataclass(frozen=True, slots=True)
class ArchiveResult:
    path: Path
    sha256: str
    size_bytes: int
    entries: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "entries": list(self.entries),
            "install_method": "Vortex Install From File or for4 bridge",
        }


def build_vortex_archive(
    files: Iterable[Path | tuple[Path, str]],
    output_dir: Path,
    *,
    mod_name: str,
    version: str,
    archive_name: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> ArchiveResult:
    """Package files without changing sources, the game Data dir, or staging.

    A plain path is placed at archive root using its basename. A ``(path,
    archive_path)`` tuple can place related assets below ``textures/``,
    ``scripts/``, and similar Fallout 4 Data-relative locations.
    """

    name = _display_value(mod_name, "mod_name", 120)
    release = _display_value(version, "version", 64)
    prepared = _prepare_entries(files)
    if not prepared:
        raise ValueError("A Vortex archive requires at least one source file")

    output_dir = Path(output_dir).expanduser().resolve(strict=False)
    output_dir.mkdir(parents=True, exist_ok=True)
    if archive_name is None:
        archive_name = f"{_slug(name)}-{_slug(release)}.zip"
    archive_name = validate_archive_name(archive_name)
    if Path(archive_name).suffix.casefold() != ".zip":
        raise ValueError("Vortex archive_name must end in .zip")
    target = (output_dir / archive_name).resolve(strict=False)
    target.relative_to(output_dir)
    if target.is_symlink():
        raise ValueError(f"Refusing to replace a symlink archive: {target}")

    manifest_entries = [
        {"path": arcname, "size": source.stat().st_size, "sha256": _hash_file(source)}
        for source, arcname in prepared
    ]
    manifest = {
        "schema_version": 1,
        "kind": "fo4ap_vortex_archive",
        "name": name,
        "version": release,
        "entries": manifest_entries,
        "metadata": redact(dict(metadata or {})),
    }
    info_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<fomod>\n'
        f'  <Name>{escape(name)}</Name>\n'
        f'  <Version>{escape(release)}</Version>\n'
        '  <Author>for4-mod-editor</Author>\n'
        '  <Description>Generated Fallout 4 patch archive. Install and deploy through Vortex.</Description>\n'
        '</fomod>\n'
    ).encode("utf-8")
    manifest_json = (json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")

    handle, temp_name = tempfile.mkstemp(prefix=f".{archive_name}.", suffix=".tmp", dir=output_dir)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        with zipfile.ZipFile(temp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
            for source, arcname in prepared:
                _write_file(archive, source, arcname)
            _write_bytes(archive, "fomod/info.xml", info_xml)
            _write_bytes(archive, "fomod/fo4ap-manifest.json", manifest_json)
        os.replace(temp_path, target)
    finally:
        temp_path.unlink(missing_ok=True)

    entries = tuple([arcname for _, arcname in prepared] + ["fomod/info.xml", "fomod/fo4ap-manifest.json"])
    return ArchiveResult(path=target, sha256=_hash_file(target), size_bytes=target.stat().st_size, entries=entries)


def _prepare_entries(files: Iterable[Path | tuple[Path, str]]) -> list[tuple[Path, str]]:
    result: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for value in files:
        if isinstance(value, tuple):
            if len(value) != 2:
                raise ValueError("Archive entry tuples must contain (source, archive_path)")
            source, requested = Path(value[0]), str(value[1])
        else:
            source, requested = Path(value), Path(value).name
        source = source.expanduser().resolve(strict=True)
        if not source.is_file() or source.is_symlink():
            raise ValueError(f"Archive source must be a regular, non-symlink file: {source}")
        arcname = _archive_path(requested)
        key = arcname.casefold()
        if key in seen:
            raise ValueError(f"Duplicate case-insensitive archive path: {arcname}")
        if key in {"fomod/info.xml", "fomod/fo4ap-manifest.json"}:
            raise ValueError(f"Archive path is reserved: {arcname}")
        seen.add(key)
        result.append((source, arcname))
    return sorted(result, key=lambda item: item[1].casefold())


def _archive_path(value: str) -> str:
    if not value or "\\" in value or any(ord(ch) < 32 for ch in value):
        raise ValueError(f"Unsafe archive path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or not path.parts or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Unsafe archive path: {value!r}")
    if ":" in path.parts[0]:
        raise ValueError(f"Unsafe archive path: {value!r}")
    return path.as_posix()


def _display_value(value: str, field: str, limit: int) -> str:
    result = str(value).strip()
    if not result or len(result) > limit or any(ord(ch) < 32 for ch in result):
        raise ValueError(f"Invalid {field}")
    return result


def _slug(value: str) -> str:
    result = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-. ")[:80]
    return result or "for4-patch"


def _zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, _ZIP_TIMESTAMP)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.create_system = 3
    info.external_attr = 0o100644 << 16
    return info


def _write_file(archive: zipfile.ZipFile, source: Path, arcname: str) -> None:
    with source.open("rb") as stream, archive.open(_zip_info(arcname), "w") as target:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            target.write(chunk)


def _write_bytes(archive: zipfile.ZipFile, arcname: str, payload: bytes) -> None:
    archive.writestr(_zip_info(arcname), payload)


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
