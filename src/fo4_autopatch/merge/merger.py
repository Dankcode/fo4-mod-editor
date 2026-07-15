"""Plan and create conservative source-retaining override patches.

The historical xEdit "Merged Patch" is not a universal semantic merge. This
module therefore defaults to an override patch that preserves FormIDs and keeps
all sources enabled. Full, plugin-eliminating merges are represented in plans so
their hazards can be reported, but are not executed by this implementation.
"""

from __future__ import annotations

import uuid
from pathlib import Path

from .. import backup
from ..config import Config, ConfigError, validate_plugin_name
from ..models import JSON_SCHEMA_VERSION, MergeMode, MergePlan, XEditResult
from ..xedit.pas_templates import render_override_merge
from ..xedit.runner import XEditRunError, XEditSession


def _local_result(cfg: Config, kind: str, *, ok: bool, error: str | None, payload: dict | None = None) -> XEditResult:
    cfg.ensure_work_dirs()
    log_path = cfg.work_dir / "logs" / f"{kind}_{uuid.uuid4().hex}.log"
    return XEditResult(
        ok=ok,
        exit_code=0 if ok else 2,
        json_payload=payload,
        log_path=log_path,
        duration_s=0.0,
        error=error,
    )


def plan_merge(
    cfg: Config,
    sources: list[str],
    output_name: str,
    *,
    mode: MergeMode | str = MergeMode.OVERRIDE_PATCH,
) -> MergePlan:
    """Build a file-hashed dry-run plan without invoking xEdit.

    ``override-patch`` is the safe default. It copies only records that already
    win in the loaded order and retains all source plugins as dependencies.
    """

    try:
        selected_mode = mode if isinstance(mode, MergeMode) else MergeMode(str(mode))
    except ValueError as exc:
        raise ConfigError(f"Unsupported merge mode: {mode!r}") from exc
    if not sources:
        raise ConfigError("At least one source plugin is required")
    output = validate_plugin_name(output_name)
    if Path(output).suffix.casefold() != ".esp":
        raise ConfigError("Generated merge/patch output must use the .esp extension")

    normalized_sources: list[str] = []
    seen: set[str] = set()
    for raw in sources:
        source = validate_plugin_name(raw)
        key = source.casefold()
        if key == output.casefold():
            raise ConfigError("Output plugin cannot also be a source")
        if key not in seen:
            normalized_sources.append(source)
            seen.add(key)

    blockers: list[str] = []
    warnings: list[str] = []
    source_hashes: dict[str, str] = {}
    for source in normalized_sources:
        path = cfg.plugin_path(source)
        if not path.is_file():
            blockers.append(f"Source plugin does not exist: {path}")
            continue
        source_hashes[source] = backup.sha256_file(path)
    output_path = cfg.plugin_path(output)
    if output_path.exists():
        blockers.append(f"Output already exists and will not be overwritten: {output_path}")

    if selected_mode is MergeMode.OVERRIDE_PATCH:
        warnings.extend(
            [
                "This is a source-retaining override patch, not a semantic or plugin-eliminating merge.",
                "Keep every source plugin and its assets enabled after creating the patch.",
                "The generated patch forwards current winning records; it does not combine disjoint field intent.",
                "Review the patch in xEdit and test it on a disposable Vortex profile before normal play.",
            ]
        )
    else:
        if not cfg.allow_experimental_full_merge:
            blockers.append("Experimental full merges are disabled by configuration.")
        blockers.extend(
            [
                "Full merge blocked: external dependent-plugin and hardcoded filename references have not been proven absent.",
                "Full merge blocked: BA2/loose assets, scripts/VMAD, FaceGen, voice, precombine/previs, and navmesh data have not been audited.",
                "Full merge execution is intentionally unavailable; use an override patch or perform a reviewed manual merge on a cloned profile.",
            ]
        )
        warnings.append("Never compact released-mod FormIDs or disable source assets based on a record-only merge.")

    return MergePlan(
        sources=normalized_sources,
        output_name=output,
        mode=selected_mode,
        source_hashes=source_hashes,
        masters=list(normalized_sources) if selected_mode is MergeMode.OVERRIDE_PATCH else [],
        esl_eligible=False,
        blockers=blockers,
        warnings=warnings,
        dry_run=True,
    )


def execute(cfg: Config, plan: MergePlan) -> XEditResult:
    """Execute a reviewed source-retaining plan and validate in a new process."""

    if not isinstance(plan, MergePlan):
        raise TypeError("plan must be a MergePlan")
    try:
        sources = [validate_plugin_name(source) for source in plan.sources]
        output_name = validate_plugin_name(plan.output_name)
    except ConfigError as exc:
        return _local_result(cfg, "merge_rejected", ok=False, error=str(exc))
    if not sources:
        return _local_result(cfg, "merge_rejected", ok=False, error="Merge plan has no source plugins")
    if plan.blockers:
        return _local_result(cfg, "merge_blocked", ok=False, error="; ".join(plan.blockers))
    if plan.dry_run:
        return _local_result(
            cfg,
            "merge_dry_run",
            ok=True,
            error=None,
            payload={
                "schema_version": JSON_SCHEMA_VERSION,
                "kind": "merge_dry_run",
                "ok": True,
                "plan": plan.to_dict(),
                "files_written": 0,
            },
        )
    if plan.mode is MergeMode.FULL:
        gate = "enabled" if cfg.allow_experimental_full_merge else "disabled"
        return _local_result(
            cfg,
            "full_merge_blocked",
            ok=False,
            error=f"Full merge execution is unavailable (experimental configuration gate is {gate}); no files were written.",
        )
    if plan.mode is not MergeMode.OVERRIDE_PATCH:
        return _local_result(cfg, "merge_rejected", ok=False, error=f"Unsupported merge mode: {plan.mode!r}")
    output_path = cfg.plugin_path(output_name)
    if output_path.exists():
        return _local_result(cfg, "merge_rejected", ok=False, error=f"Refusing to overwrite {output_path}")

    for source in sources:
        source_path = cfg.plugin_path(source)
        if not source_path.is_file():
            return _local_result(cfg, "merge_rejected", ok=False, error=f"Source plugin is missing: {source_path}")
        expected = plan.source_hashes.get(source)
        if not expected:
            return _local_result(cfg, "merge_rejected", ok=False, error=f"Plan has no source hash for {source}")
        current = backup.sha256_file(source_path)
        if current.casefold() != expected.casefold():
            return _local_result(
                cfg,
                "merge_rejected",
                ok=False,
                error=f"Source changed after planning: {source}; create a fresh plan.",
            )

    backup_dir = backup.checkpoint(cfg, sources, "before-override-patch-merge")
    session_id = "merge_" + uuid.uuid4().hex
    out_json = cfg.work_dir / "xedit_out" / f"{session_id}.json"
    source = render_override_merge(out_json, output_name, sources, dry_run=False)
    result = XEditSession(cfg).run_script(source, session_id, extra_args=sources)
    result.backup_dir = backup_dir
    if not result.ok:
        return result
    if not output_path.is_file():
        result.ok = False
        result.error = f"xEdit reported success but did not create {output_path}"
        return result
    if not result.json_payload or result.json_payload.get("sources_retained") is not True:
        result.ok = False
        result.error = "xEdit result did not confirm source-retaining override-patch semantics"
        return result

    # Validate from a second xEdit process. Conflict chains are intentional;
    # only Check() errors make the generated patch fail validation.
    try:
        from ..conflicts.scanner import scan

        validation = scan(cfg, [output_name])
    except XEditRunError as exc:
        result.ok = False
        result.error = f"Fresh-process validation could not complete: {exc}"
        return result
    check_errors = [warning for warning in validation.warnings if warning.startswith("xEdit Check reported")]
    if check_errors:
        result.ok = False
        result.error = "Fresh-process xEdit validation failed: " + "; ".join(check_errors[:10])
        return result
    result.json_payload = dict(result.json_payload)
    result.json_payload.update(
        {
            "validation_log": str(validation.xedit_log) if validation.xedit_log else None,
            "validation_records": len(validation.records),
            "sources_retained": True,
            "semantic_merge_claimed": False,
        }
    )
    return result
