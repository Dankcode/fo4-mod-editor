"""Dependency-free domain models and versioned JSON serialization."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Mapping

JSON_SCHEMA_VERSION = 1


class ConflictClass(str, Enum):
    AUTO_ITM = "itm"
    AUTO_UDR = "udr"
    AUTO_IDENTICAL_WINNER = "identical_winner"
    AUTO_FORWARDABLE = "forwardable"
    MANUAL_SCRIPTED = "scripted_vmad"
    MANUAL_PRECOMBINE = "precombine_previs"
    MANUAL_LEVELED_LIST = "leveled_list"
    MANUAL_NAVMESH = "navmesh"
    MANUAL_OTHER = "other"

    @property
    def is_auto(self) -> bool:
        return self.name.startswith("AUTO_")


class MergeMode(str, Enum):
    OVERRIDE_PATCH = "override-patch"
    FULL = "full"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _path_or_none(value: str | Path | None) -> Path | None:
    return None if value in (None, "") else Path(value)


@dataclass(slots=True)
class PluginInfo:
    name: str
    load_order_index: int = -1
    masters: list[str] = field(default_factory=list)
    is_esl: bool = False
    record_count: int = 0
    nexus_mod_id: int | None = None
    nexus_file_id: int | None = None
    installed_version: str | None = None
    archive_path: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "load_order_index": self.load_order_index,
            "masters": list(self.masters),
            "is_esl": self.is_esl,
            "record_count": self.record_count,
            "nexus_mod_id": self.nexus_mod_id,
            "nexus_file_id": self.nexus_file_id,
            "installed_version": self.installed_version,
            "archive_path": str(self.archive_path) if self.archive_path else None,
        }


@dataclass(slots=True)
class ConflictRecord:
    form_id: str
    signature: str
    edid: str | None
    winning_plugin: str
    losing_plugins: list[str]
    fields: list[str]
    conflict_class: ConflictClass
    fix_instruction: str = ""
    field_sources: dict[str, str] = field(default_factory=dict)
    override_chain: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def stable_id(self) -> str:
        return f"{self.signature}:{self.form_id}:{self.winning_plugin}"

    @property
    def severity(self) -> int:
        return {
            ConflictClass.MANUAL_NAVMESH: 100,
            ConflictClass.MANUAL_PRECOMBINE: 90,
            ConflictClass.MANUAL_SCRIPTED: 80,
            ConflictClass.MANUAL_LEVELED_LIST: 70,
            ConflictClass.MANUAL_OTHER: 60,
        }.get(self.conflict_class, 10)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.stable_id,
            "form_id": self.form_id,
            "signature": self.signature,
            "edid": self.edid,
            "winning_plugin": self.winning_plugin,
            "losing_plugins": list(self.losing_plugins),
            "fields": list(self.fields),
            "field_sources": dict(self.field_sources),
            "override_chain": list(self.override_chain),
            "conflict_class": self.conflict_class.value,
            "fix_instruction": self.fix_instruction,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ConflictRecord":
        return cls(
            form_id=str(raw.get("form_id", "")).upper().removeprefix("0X").zfill(8),
            signature=str(raw.get("signature", "????"))[:4].upper(),
            edid=str(raw["edid"]) if raw.get("edid") not in (None, "") else None,
            winning_plugin=str(raw.get("winning_plugin", "")),
            losing_plugins=[str(v) for v in raw.get("losing_plugins", [])],
            fields=[str(v) for v in raw.get("fields", [])],
            field_sources={str(k): str(v) for k, v in raw.get("field_sources", {}).items()},
            override_chain=[str(v) for v in raw.get("override_chain", [])],
            conflict_class=ConflictClass(str(raw.get("conflict_class", "other"))),
            fix_instruction=str(raw.get("fix_instruction", "")),
            metadata=dict(raw.get("metadata", {})),
        )


@dataclass(slots=True)
class ConflictReport:
    scanned_plugins: list[str]
    records: list[ConflictRecord]
    xedit_log: Path | None = None
    generated_at: str = field(default_factory=utc_now)
    applied_fixes: int = 0
    backup_dir: Path | None = None
    report_path: Path | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def auto_fixable(self) -> list[ConflictRecord]:
        return [r for r in self.records if r.conflict_class.is_auto]

    @property
    def needs_user(self) -> list[ConflictRecord]:
        return [r for r in self.records if not r.conflict_class.is_auto]

    @property
    def exit_code(self) -> int:
        if self.needs_user:
            return 2
        return 1 if self.applied_fixes else 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": JSON_SCHEMA_VERSION,
            "kind": "conflict_report",
            "generated_at": self.generated_at,
            "scanned_plugins": list(self.scanned_plugins),
            "summary": {
                "records": len(self.records),
                "auto_fixable": len(self.auto_fixable),
                "manual": len(self.needs_user),
                "applied_fixes": self.applied_fixes,
            },
            "records": [record.to_dict() for record in self.records],
            "xedit_log": str(self.xedit_log) if self.xedit_log else None,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
            "report_path": str(self.report_path) if self.report_path else None,
            "warnings": list(self.warnings),
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "ConflictReport":
        summary = raw.get("summary", {})
        return cls(
            scanned_plugins=[str(v) for v in raw.get("scanned_plugins", [])],
            records=[ConflictRecord.from_dict(v) for v in raw.get("records", [])],
            xedit_log=_path_or_none(raw.get("xedit_log")),
            generated_at=str(raw.get("generated_at") or utc_now()),
            applied_fixes=int(summary.get("applied_fixes", raw.get("applied_fixes", 0))),
            backup_dir=_path_or_none(raw.get("backup_dir")),
            report_path=_path_or_none(raw.get("report_path")),
            warnings=[str(v) for v in raw.get("warnings", [])],
        )


@dataclass(slots=True)
class MergePlan:
    sources: list[str]
    output_name: str
    mode: MergeMode = MergeMode.OVERRIDE_PATCH
    source_hashes: dict[str, str] = field(default_factory=dict)
    masters: list[str] = field(default_factory=list)
    external_dependents: dict[str, list[str]] = field(default_factory=dict)
    formid_remaps: dict[str, str] = field(default_factory=dict)
    esl_eligible: bool = False
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    record_counts: dict[str, int] = field(default_factory=dict)
    dry_run: bool = True
    generated_at: str = field(default_factory=utc_now)

    @property
    def ready(self) -> bool:
        return not self.blockers

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": JSON_SCHEMA_VERSION,
            "kind": "merge_plan",
            "generated_at": self.generated_at,
            "sources": list(self.sources),
            "output_name": self.output_name,
            "mode": self.mode.value,
            "source_hashes": dict(self.source_hashes),
            "masters": list(self.masters),
            "external_dependents": dict(self.external_dependents),
            "formid_remaps": dict(self.formid_remaps),
            "esl_eligible": self.esl_eligible,
            "record_counts": dict(self.record_counts),
            "blockers": list(self.blockers),
            "warnings": list(self.warnings),
            "dry_run": self.dry_run,
            "ready": self.ready,
        }

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "MergePlan":
        return cls(
            sources=[str(v) for v in raw.get("sources", [])],
            output_name=str(raw.get("output_name", "")),
            mode=MergeMode(str(raw.get("mode", MergeMode.OVERRIDE_PATCH.value))),
            source_hashes={str(k): str(v) for k, v in raw.get("source_hashes", {}).items()},
            masters=[str(v) for v in raw.get("masters", [])],
            external_dependents={str(k): [str(x) for x in v] for k, v in raw.get("external_dependents", {}).items()},
            formid_remaps={str(k): str(v) for k, v in raw.get("formid_remaps", {}).items()},
            esl_eligible=bool(raw.get("esl_eligible", False)),
            blockers=[str(v) for v in raw.get("blockers", [])],
            warnings=[str(v) for v in raw.get("warnings", [])],
            record_counts={str(k): int(v) for k, v in raw.get("record_counts", {}).items()},
            dry_run=bool(raw.get("dry_run", True)),
            generated_at=str(raw.get("generated_at") or utc_now()),
        )


@dataclass(slots=True)
class ModFileInfo:
    mod_id: int
    file_id: int
    name: str
    file_name: str
    version: str
    category: str
    size_bytes: int
    uploaded_at: str | None = None
    md5: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "mod_id": self.mod_id,
            "file_id": self.file_id,
            "name": self.name,
            "file_name": self.file_name,
            "version": self.version,
            "category": self.category,
            "size_bytes": self.size_bytes,
            "uploaded_at": self.uploaded_at,
            "md5": self.md5,
        }


@dataclass(slots=True)
class InstalledMod:
    instance_id: str
    name: str
    mod_id: int | None
    file_id: int | None
    version: str | None
    archive_path: Path | None = None
    plugins: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "name": self.name,
            "mod_id": self.mod_id,
            "file_id": self.file_id,
            "version": self.version,
            "archive_path": str(self.archive_path) if self.archive_path else None,
            "plugins": list(self.plugins),
        }


@dataclass(slots=True)
class XEditResult:
    ok: bool
    exit_code: int
    json_payload: dict[str, Any] | None
    log_path: Path
    duration_s: float
    error: str | None = None
    backup_dir: Path | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": JSON_SCHEMA_VERSION,
            "kind": "xedit_result",
            "ok": self.ok,
            "exit_code": self.exit_code,
            "json_payload": self.json_payload,
            "log_path": str(self.log_path),
            "duration_s": round(self.duration_s, 3),
            "error": self.error,
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
        }

