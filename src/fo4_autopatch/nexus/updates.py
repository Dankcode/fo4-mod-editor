"""Conservative comparison of installed Vortex metadata with Nexus files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

from ..models import InstalledMod, ModFileInfo

UPDATE_AVAILABLE = "update_available"
UP_TO_DATE = "up_to_date"
UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class UpdateDecision:
    installed: InstalledMod
    latest: ModFileInfo | None
    status: str
    reason: str

    @property
    def update_available(self) -> bool:
        return self.status == UPDATE_AVAILABLE

    def to_dict(self) -> dict[str, object]:
        return {
            "installed": self.installed.to_dict(),
            "latest": self.latest.to_dict() if self.latest else None,
            "status": self.status,
            "update_available": self.update_available,
            "reason": self.reason,
        }


def compare_versions(left: str | None, right: str | None) -> int:
    """Compare loose mod versions without claiming full SemVer behavior."""

    left_tokens = _version_tokens(left)
    right_tokens = _version_tokens(right)
    for index in range(max(len(left_tokens), len(right_tokens))):
        if index >= len(left_tokens):
            return _missing_vs_tail(right_tokens[index:])
        if index >= len(right_tokens):
            return -_missing_vs_tail(left_tokens[index:])
        l_kind, l_value = left_tokens[index]
        r_kind, r_value = right_tokens[index]
        if l_kind == r_kind:
            if l_value != r_value:
                return 1 if l_value > r_value else -1
        elif l_kind == 1:  # numeric tokens sort after prerelease words
            return 1
        else:
            return -1
    return 0


def compare_updates(
    installed: Iterable[InstalledMod],
    available_by_mod: Mapping[int, Sequence[ModFileInfo]],
) -> list[UpdateDecision]:
    return [compare_installed_mod(mod, available_by_mod.get(mod.mod_id or -1, ())) for mod in installed]


def compare_installed_mod(installed: InstalledMod, available: Sequence[ModFileInfo]) -> UpdateDecision:
    if installed.mod_id is None:
        return UpdateDecision(installed, None, UNKNOWN, "Installed mod has no Nexus mod ID")
    files = [item for item in available if item.mod_id == installed.mod_id and not _retired(item.category)]
    if not files:
        return UpdateDecision(installed, None, UNKNOWN, "Nexus returned no active files for this mod")

    current = next((item for item in files if item.file_id == installed.file_id), None)
    if current is not None:
        category = _category(current.category)
        comparable = [item for item in files if _category(item.category) == category]
    else:
        main = [item for item in files if _main_category(item.category)]
        if not main:
            return UpdateDecision(
                installed,
                None,
                UNKNOWN,
                "Installed file is absent and no unambiguous main-file category is available",
            )
        comparable = main

    latest = max(comparable, key=_file_sort_key)
    if installed.file_id is not None:
        if latest.file_id > installed.file_id:
            return UpdateDecision(installed, latest, UPDATE_AVAILABLE, "A newer file ID exists in the same file category")
        if latest.file_id < installed.file_id:
            return UpdateDecision(installed, latest, UNKNOWN, "Installed file ID is newer than the API result")
        return UpdateDecision(installed, latest, UP_TO_DATE, "Installed Nexus file ID is current")

    if installed.version and latest.version:
        comparison = compare_versions(latest.version, installed.version)
        if comparison > 0:
            return UpdateDecision(installed, latest, UPDATE_AVAILABLE, "The latest main-file version is newer")
        if comparison == 0:
            return UpdateDecision(installed, latest, UP_TO_DATE, "Installed version matches the latest main file")
        return UpdateDecision(installed, latest, UNKNOWN, "Installed version is newer than the API result")
    return UpdateDecision(installed, latest, UNKNOWN, "Installed metadata has neither a file ID nor comparable version")


def _version_tokens(value: str | None) -> list[tuple[int, int | str]]:
    text = (value or "").strip().casefold().removeprefix("v")
    result: list[tuple[int, int | str]] = []
    for token in re.findall(r"[0-9]+|[a-z]+", text):
        result.append((1, int(token)) if token.isdigit() else (0, token))
    return result


def _missing_vs_tail(tail: Sequence[tuple[int, int | str]]) -> int:
    """Compare a missing stable-version tail with the other side's tail."""

    for kind, value in tail:
        if kind == 0:
            return 1  # a release sorts after an alpha/prerelease suffix
        if int(value) != 0:
            return -1
    return 0


def _category(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value).casefold()).strip("_")


def _main_category(value: str) -> bool:
    normalized = _category(value)
    return normalized in {"1", "main", "main_file", "main_files"} or normalized.startswith("main_")


def _retired(value: str) -> bool:
    normalized = _category(value)
    return any(word in normalized for word in ("archived", "deleted", "old_version"))


def _file_sort_key(item: ModFileInfo) -> tuple[str, int, list[tuple[int, int | str]]]:
    return (item.uploaded_at or "", item.file_id, _version_tokens(item.version))
