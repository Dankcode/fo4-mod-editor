"""Durable Markdown conflict reports and optional Windows notifications."""

from __future__ import annotations

import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from ..config import Config
from ..models import ConflictReport


def _markdown(value: object) -> str:
    text = str(value).replace("\r", " ").replace("\n", " ")
    text = text.replace("\\", "\\\\").replace("|", "\\|")
    return text.replace("<", "&lt;").replace(">", "&gt;")


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def write_report(cfg: Config, report: ConflictReport) -> Path:
    """Write a severity-sorted report with record-specific next actions."""

    cfg.ensure_work_dirs()
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = cfg.work_dir / "reports" / f"conflicts_{timestamp}.md"
    counter = 1
    while path.exists():
        path = cfg.work_dir / "reports" / f"conflicts_{timestamp}_{counter}.md"
        counter += 1
    manual = sorted(
        report.needs_user,
        key=lambda record: (-record.severity, record.winning_plugin.casefold(), record.signature, record.form_id),
    )
    lines = [
        "# Fallout 4 conflict report",
        "",
        f"Generated: {_markdown(report.generated_at)}",
        f"Scanned plugins: {len(report.scanned_plugins)}",
        f"Records observed: {len(report.records)}",
        f"Override records written to a new patch: {report.applied_fixes}",
        f"Manual review required: {len(manual)}",
        "",
        "> A red or conflicting xEdit record is not automatically an error. These instructions identify review points, not universal semantic fixes.",
        "",
    ]
    if report.warnings:
        lines.extend(["## Warnings", ""])
        lines.extend(f"- {_markdown(warning)}" for warning in report.warnings)
        lines.append("")
    if not manual:
        lines.extend(["## Result", "", "No records require manual conflict resolution.", ""])
    else:
        groups: dict[str, list] = {}
        for record in manual:
            groups.setdefault(record.winning_plugin, []).append(record)
        for plugin in sorted(groups, key=str.casefold):
            lines.extend([f"## Winner: {_markdown(plugin)}", ""])
            for record in groups[plugin]:
                editor = f" ({_markdown(record.edid)})" if record.edid else ""
                lines.extend(
                    [
                        f"### {_markdown(record.signature)} {_markdown(record.form_id)}{editor}",
                        "",
                        f"- Classification: `{_markdown(record.conflict_class.value)}`",
                        f"- Losing plugins: {_markdown(', '.join(record.losing_plugins) or 'none recorded')}",
                        f"- Changed paths: {_markdown(', '.join(record.fields) or 'not exported; inspect in xEdit')}",
                        f"- Recommended action: {_markdown(record.fix_instruction)}",
                        "",
                    ]
                )
    _atomic_text(path, "\n".join(lines).rstrip() + "\n")
    report.report_path = path
    return path


def notify(cfg: Config, report: ConflictReport, report_path: Path) -> None:
    """Show an optional toast, falling back to a concise console message."""

    message = (
        f"{len(report.needs_user)} manual conflict(s), "
        f"{report.applied_fixes} override(s) patched. Report: {Path(report_path)}"
    )
    if os.name == "nt":
        try:
            from win11toast import toast

            toast("For4 Mod Editor", message, duration="short")
            return
        except (ImportError, OSError, RuntimeError):
            pass
    print(message)
