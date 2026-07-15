"""Run the read-only xEdit bridge and construct a validated report."""

from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from typing import Any

from ..config import Config, ConfigError, validate_plugin_name
from ..models import ConflictRecord, ConflictReport, JSON_SCHEMA_VERSION
from ..xedit.pas_templates import render_conflict_export
from ..xedit.runner import XEditRunError, XEditSession
from .fixer import classify

_FORM_ID_RE = re.compile(r"^[0-9A-F]{8}$")
_SIGNATURE_RE = re.compile(r"^[A-Z0-9_]{4}$")
_MAX_RECORDS = 1_000_000


def _safe_text(value: Any, label: str, *, maximum: int = 2048, allow_empty: bool = True) -> str:
    text = str(value)
    if (not allow_empty and not text) or len(text) > maximum:
        raise XEditRunError(f"Invalid {label} in xEdit bridge output")
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise XEditRunError(f"Control character in {label} from xEdit bridge output")
    return text


def _plugin_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise XEditRunError(f"Expected a list for {label} in xEdit bridge output")
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        try:
            plugin = validate_plugin_name(_safe_text(item, label, maximum=240, allow_empty=False))
        except ConfigError as exc:
            raise XEditRunError(str(exc)) from exc
        key = plugin.casefold()
        if key not in seen:
            result.append(plugin)
            seen.add(key)
    return result


def _validated_record(raw: Mapping[str, Any]) -> ConflictRecord:
    form_id = str(raw.get("form_id", "")).upper().removeprefix("0X")
    if not _FORM_ID_RE.fullmatch(form_id):
        raise XEditRunError(f"Invalid FormID in xEdit bridge output: {form_id!r}")
    signature = str(raw.get("signature", "")).upper()
    if not _SIGNATURE_RE.fullmatch(signature):
        raise XEditRunError(f"Invalid record signature in xEdit bridge output: {signature!r}")
    try:
        winner = validate_plugin_name(_safe_text(raw.get("winning_plugin", ""), "winning_plugin", maximum=240, allow_empty=False))
    except ConfigError as exc:
        raise XEditRunError(str(exc)) from exc
    losers = _plugin_list(raw.get("losing_plugins", []), "losing_plugins")
    chain = _plugin_list(raw.get("override_chain", []), "override_chain")
    fields_raw = raw.get("fields", [])
    if not isinstance(fields_raw, list) or len(fields_raw) > 100_000:
        raise XEditRunError("Invalid fields list in xEdit bridge output")
    fields = [_safe_text(value, "field path", allow_empty=False) for value in fields_raw]
    field_sources_raw = raw.get("field_sources", {})
    if not isinstance(field_sources_raw, Mapping):
        raise XEditRunError("Invalid field_sources object in xEdit bridge output")
    field_sources: dict[str, str] = {}
    for key, value in field_sources_raw.items():
        path = _safe_text(key, "field source path", allow_empty=False)
        try:
            field_sources[path] = validate_plugin_name(_safe_text(value, "field source plugin", maximum=240, allow_empty=False))
        except ConfigError as exc:
            raise XEditRunError(str(exc)) from exc
    metadata = raw.get("metadata", {})
    if not isinstance(metadata, Mapping):
        raise XEditRunError("Invalid metadata object in xEdit bridge output")
    edid_value = raw.get("edid")
    edid = None if edid_value in (None, "") else _safe_text(edid_value, "EDID", maximum=1024)
    normalized = {
        "form_id": form_id,
        "signature": signature,
        "edid": edid,
        "winning_plugin": winner,
        "losing_plugins": losers,
        "fields": fields,
        "field_sources": field_sources,
        "override_chain": chain,
        "metadata": dict(metadata),
    }
    conflict_class, instruction = classify(normalized)
    normalized["conflict_class"] = conflict_class.value
    normalized["fix_instruction"] = instruction
    return ConflictRecord.from_dict(normalized)


def scan(cfg: Config, plugins: list[str] | None = None) -> ConflictReport:
    """Scan active plugins without writing any Bethesda plugin.

    Explicit plugin names are also passed on xEdit's command line so inactive
    validation outputs can be loaded alongside the active profile. The Pascal
    bridge filters emitted records to override chains intersecting that scope.
    """

    selected: list[str] | None = None
    if plugins is not None:
        if not plugins:
            raise ConfigError("An explicit scan scope cannot be empty")
        selected = []
        seen: set[str] = set()
        for raw in plugins:
            plugin = validate_plugin_name(raw)
            key = plugin.casefold()
            if key not in seen:
                selected.append(plugin)
                seen.add(key)
    cfg.ensure_work_dirs()
    session_id = "scan_" + uuid.uuid4().hex
    out_json = cfg.work_dir / "xedit_out" / f"{session_id}.json"
    source = render_conflict_export(out_json, selected)
    result = XEditSession(cfg).run_script(source, session_id, extra_args=selected)
    if not result.ok or result.json_payload is None:
        raise XEditRunError(result.error or "xEdit conflict scan failed")
    payload = result.json_payload
    if payload.get("schema_version") != JSON_SCHEMA_VERSION or payload.get("kind") != "xedit_conflict_scan":
        raise XEditRunError("xEdit returned an unexpected conflict-scan payload")
    scanned_plugins = _plugin_list(payload.get("scanned_plugins", []), "scanned_plugins")
    records_raw = payload.get("records", [])
    if not isinstance(records_raw, list) or len(records_raw) > _MAX_RECORDS:
        raise XEditRunError("Invalid or excessive record list in xEdit bridge output")
    records: list[ConflictRecord] = []
    warnings: list[str] = []
    for raw in records_raw:
        if not isinstance(raw, Mapping):
            raise XEditRunError("Every xEdit conflict record must be a JSON object")
        record = _validated_record(raw)
        records.append(record)
        check_error = record.metadata.get("check_error")
        if check_error:
            message = _safe_text(check_error, "xEdit Check error", maximum=8192)
            warnings.append(f"xEdit Check reported {record.stable_id}: {message}")
    if selected:
        loaded = {name.casefold() for name in scanned_plugins}
        missing = [name for name in selected if name.casefold() not in loaded]
        if missing:
            raise XEditRunError("xEdit did not load requested plugins: " + ", ".join(missing))
    records.sort(key=lambda record: (-record.severity, record.signature, record.form_id, record.winning_plugin.casefold()))
    return ConflictReport(
        scanned_plugins=scanned_plugins,
        records=records,
        xedit_log=result.log_path,
        warnings=warnings,
    )
