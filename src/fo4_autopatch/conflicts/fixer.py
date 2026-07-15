"""Evidence-gated classification and source-preserving patch creation."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from .. import backup
from ..config import Config, ConfigError, validate_plugin_name
from ..models import ConflictClass, ConflictRecord, ConflictReport
from ..xedit.pas_templates import render_batch_patch
from ..xedit.runner import XEditRunError, XEditSession

_SCRIPTED_SIGNATURES = {"DIAL", "INFO", "PACK", "QUST", "SCEN"}
_LEVELED_SIGNATURES = {"LVLI", "LVLN", "LVSP"}
_UDR_REFERENCE_SIGNATURES = {"ACHR", "ACRE", "PARW", "PBAR", "PBEA", "PCON", "PFLA", "PGRE", "PMIS", "REFR"}


def _metadata(raw: Mapping[str, object]) -> Mapping[str, object]:
    value = raw.get("metadata", {})
    return value if isinstance(value, Mapping) else {}


def _flag(raw: Mapping[str, object], name: str) -> bool:
    metadata = _metadata(raw)
    return raw.get(name) is True or metadata.get(name) is True


def _contributors(raw: Mapping[str, object]) -> list[str]:
    names: list[str] = []
    for key in ("override_chain", "losing_plugins"):
        value = raw.get(key, [])
        if isinstance(value, list):
            names.extend(str(item) for item in value if item)
    winner = str(raw.get("winning_plugin", ""))
    if winner:
        names.append(winner)
    result: list[str] = []
    seen: set[str] = set()
    for name in names:
        folded = name.casefold()
        if folded not in seen:
            result.append(name)
            seen.add(folded)
    return result


def classify(raw: dict) -> tuple[ConflictClass, str]:
    """Classify a record only when the payload contains sufficient evidence.

    xEdit's presence of a conflict is not evidence that either edit is wrong.
    The standard scan intentionally marks element-level evidence incomplete, so
    most records remain manual until an allowlisted analyzer supplies proof.
    """

    signature = str(raw.get("signature", "????")).upper()[:4]
    form_id = str(raw.get("form_id", "????????")).upper()
    winner = str(raw.get("winning_plugin", "<unknown>"))
    contributors = ", ".join(_contributors(raw)) or "unknown plugins"

    if signature == "NAVM":
        return (
            ConflictClass.MANUAL_NAVMESH,
            f"Review NAVM {form_id} manually in xEdit and test pathfinding; navmesh records are never auto-forwarded.",
        )
    if _flag(raw, "has_precombine") or _flag(raw, "precombine_sensitive") or _flag(raw, "has_previs"):
        return (
            ConflictClass.MANUAL_PRECOMBINE,
            f"Review {signature} {form_id} with its precombine/previs assets; keep the matching plugin and external files together.",
        )
    if signature in _SCRIPTED_SIGNATURES or _flag(raw, "has_vmad"):
        return (
            ConflictClass.MANUAL_SCRIPTED,
            f"Inspect {signature} {form_id} and its VMAD/script properties from {contributors}; script semantics require author or user review.",
        )
    if signature in _LEVELED_SIGNATURES:
        return (
            ConflictClass.MANUAL_LEVELED_LIST,
            f"Build and review a leveled-list patch for {signature} {form_id}; contributors are {contributors}.",
        )

    complete = _flag(raw, "evidence_complete") and _flag(raw, "source_values_complete")
    if complete and _flag(raw, "is_itm") and _flag(raw, "xedit_qac_safe"):
        return (
            ConflictClass.AUTO_ITM,
            f"xEdit verified {signature} {form_id} as a Quick Auto Clean candidate; cleaning remains a separate explicit source-edit action.",
        )
    if complete and signature in _UDR_REFERENCE_SIGNATURES and _flag(raw, "is_deleted") and _flag(raw, "udr_safe"):
        return (
            ConflictClass.AUTO_UDR,
            f"xEdit verified deleted reference {signature} {form_id}; undelete/disable only through an explicit cleaning workflow.",
        )
    if complete and _flag(raw, "identical_to_winner"):
        return (
            ConflictClass.AUTO_IDENTICAL_WINNER,
            f"{signature} {form_id} is byte-for-byte identical to the winning value; no source edit is required.",
        )

    metadata = _metadata(raw)
    forward_plugin = str(metadata.get("forward_plugin") or raw.get("forward_plugin") or "")
    field_sources = raw.get("field_sources", {})
    source_values = set(str(value).casefold() for value in field_sources.values()) if isinstance(field_sources, Mapping) else set()
    record_forward_safe = complete and _flag(raw, "record_forward_safe")
    if (
        record_forward_safe
        and forward_plugin
        and forward_plugin.casefold() == winner.casefold()
        and (not source_values or source_values == {winner.casefold()})
    ):
        return (
            ConflictClass.AUTO_FORWARDABLE,
            f"Copy the complete winning record {signature} {form_id} from {winner} into a new override patch; retain all source plugins.",
        )

    return (
        ConflictClass.MANUAL_OTHER,
        f"Review {signature} {form_id} in xEdit. {winner} currently wins over {contributors}; choose intent before changing load order or creating a patch.",
    )


def _copy_report(report: ConflictReport) -> ConflictReport:
    return ConflictReport.from_dict(report.to_dict())


def _downgrade(record: ConflictRecord, reason: str) -> ConflictRecord:
    raw = record.to_dict()
    raw["conflict_class"] = ConflictClass.MANUAL_OTHER.value
    raw["fix_instruction"] = reason
    return ConflictRecord.from_dict(raw)


def apply_fixes(cfg: Config, report: ConflictReport, dry_run: bool = True) -> ConflictReport:
    """Create one new override patch for proven whole-record forwards.

    This never deletes, edits, or cleans a source plugin. ITM/UDR cleanup is
    intentionally excluded because it is a separate source-mutating operation.
    """

    result_report = _copy_report(report)
    operations: list[dict[str, str]] = []
    applied_ids: set[str] = set()
    retained: list[ConflictRecord] = []
    for record in result_report.records:
        if record.conflict_class is ConflictClass.AUTO_FORWARDABLE:
            source = validate_plugin_name(str(record.metadata.get("forward_plugin") or record.winning_plugin))
            if source.casefold() != record.winning_plugin.casefold():
                retained.append(_downgrade(record, "The requested forward source is not the current winner; review manually."))
                continue
            operations.append({"action": "forward_record", "form_id": record.form_id, "source_plugin": source})
            applied_ids.add(record.stable_id)
        elif record.conflict_class in {ConflictClass.AUTO_ITM, ConflictClass.AUTO_UDR}:
            retained.append(
                _downgrade(
                    record,
                    "This action would change a source plugin. Run the explicit Quick Auto Clean workflow after reviewing its checkpoint.",
                )
            )
        else:
            retained.append(record)

    if dry_run:
        result_report.warnings.append(
            f"Dry run: {len(operations)} whole-record override(s) would be copied to the new patch {cfg.patch_plugin}; no files were written."
        )
        return result_report
    if not operations:
        result_report.records = retained
        result_report.warnings.append("No evidence-backed whole-record forwards were available; no xEdit write was started.")
        return result_report

    patch_name = validate_plugin_name(cfg.patch_plugin)
    patch_path = cfg.plugin_path(patch_name)
    if patch_path.exists():
        raise ConfigError(f"Refusing to modify existing conflict patch: {patch_path}")
    sources = sorted({operation["source_plugin"] for operation in operations}, key=str.casefold)
    backup_dir = backup.checkpoint(cfg, sources, "before-conflict-override-patch")
    session_id = "patch_" + uuid.uuid4().hex
    out_json = cfg.work_dir / "xedit_out" / f"{session_id}.json"
    source = render_batch_patch(out_json, patch_name, operations)
    xedit_result = XEditSession(cfg).run_script(source, session_id, extra_args=report.scanned_plugins)
    if not xedit_result.ok:
        raise XEditRunError(xedit_result.error or "xEdit failed to build the override patch")
    if not patch_path.is_file():
        raise XEditRunError(f"xEdit reported success but did not create {patch_path}")

    # A second process reloads the output and asks xEdit's Check() API to
    # validate every emitted patch record. Conflicts themselves remain expected.
    from .scanner import scan

    validation = scan(cfg, [patch_name])
    check_warnings = [warning for warning in validation.warnings if warning.startswith("xEdit Check reported")]
    if check_warnings:
        raise XEditRunError("Fresh-process validation failed: " + "; ".join(check_warnings[:10]))
    result_report.records = [record for record in retained if record.stable_id not in applied_ids]
    result_report.applied_fixes += len(operations)
    result_report.backup_dir = backup_dir
    result_report.xedit_log = validation.xedit_log
    result_report.warnings.append(
        f"Created {patch_name} with {len(operations)} whole-record override(s). Keep every source plugin enabled."
    )
    return result_report
