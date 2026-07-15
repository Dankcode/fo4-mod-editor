from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from fo4_autopatch import cli
from fo4_autopatch.config import Config, ConfigError
from fo4_autopatch.models import ConflictReport, MergePlan, XEditResult


def make_config(tmp_path: Path) -> Config:
    return Config(
        fo4edit_exe=tmp_path / "FO4Edit64.exe",
        game_data_dir=tmp_path / "Data",
        plugins_txt=tmp_path / "plugins.txt",
        vortex_downloads_dir=tmp_path / "downloads",
        vortex_staging_dir=tmp_path / "staging",
        work_dir=tmp_path / "work",
        bridge_dir=tmp_path / "bridge",
    )


def install_config(monkeypatch: pytest.MonkeyPatch, cfg: Config) -> None:
    monkeypatch.setattr(cli.Config, "load", classmethod(lambda cls, path=None: cfg))


def test_doctor_emits_compact_json(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    install_config(monkeypatch, make_config(tmp_path))
    monkeypatch.setattr(cli.doctor, "run", lambda cfg: {"schema_version": 1, "kind": "doctor", "ok": True})

    code = cli.main(["doctor", "--json"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert json.loads(captured.out)["ok"] is True
    assert "\n " not in captured.out


def test_scan_keeps_notification_fallback_off_stdout(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_config(monkeypatch, make_config(tmp_path))
    report = ConflictReport(scanned_plugins=[], records=[])
    notifier = SimpleNamespace(
        write_report=lambda cfg, value: tmp_path / "conflicts.md",
        notify=lambda cfg, value, path: print("desktop fallback"),
    )
    scanner = SimpleNamespace(scan=lambda cfg, plugins: report)
    monkeypatch.setattr(cli, "_conflict_modules", lambda: (SimpleNamespace(), notifier, scanner))

    code = cli.main(["scan", "--json"])

    captured = capsys.readouterr()
    assert code == cli.EXIT_OK
    assert json.loads(captured.out)["kind"] == "conflict_report"
    assert "desktop fallback" not in captured.out
    assert "desktop fallback" in captured.err


def test_fix_apply_requires_yes_before_fixer_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_config(monkeypatch, make_config(tmp_path))
    calls: list[bool] = []
    report = ConflictReport(scanned_plugins=[], records=[])
    fixer = SimpleNamespace(apply_fixes=lambda cfg, value, dry_run=True: calls.append(dry_run))
    scanner = SimpleNamespace(scan=lambda cfg, plugins: report)
    monkeypatch.setattr(cli, "_conflict_modules", lambda: (fixer, SimpleNamespace(), scanner))

    code = cli.main(["fix", "--apply", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_REVIEW
    assert payload["kind"] == "approval_required"
    assert calls == []


def test_merge_dry_run_never_executes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_config(monkeypatch, make_config(tmp_path))
    plan = MergePlan(sources=["A.esp", "B.esp"], output_name="Patch.esp")
    merger = SimpleNamespace(
        plan_merge=lambda cfg, sources, output: plan,
        execute=lambda cfg, value: pytest.fail("dry run executed the merge"),
    )
    monkeypatch.setattr(cli, "_merge_module", lambda: merger)

    code = cli.main(["merge", "--out", "Patch.esp", "A.esp", "B.esp", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_REVIEW
    assert payload["kind"] == "merge_plan"
    assert payload["dry_run"] is True


def test_merge_apply_returns_changed_after_explicit_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    install_config(monkeypatch, make_config(tmp_path))
    plan = MergePlan(sources=["A.esp"], output_name="Patch.esp")
    result = XEditResult(ok=True, exit_code=0, json_payload={}, log_path=tmp_path / "xedit.log", duration_s=1.0)
    merger = SimpleNamespace(plan_merge=lambda cfg, sources, output: plan, execute=lambda cfg, value: result)
    monkeypatch.setattr(cli, "_merge_module", lambda: merger)

    code = cli.main(["merge", "--out", "Patch.esp", "A.esp", "--apply", "--yes", "--json"])

    payload = json.loads(capsys.readouterr().out)
    assert code == cli.EXIT_CHANGED
    assert payload["kind"] == "merge_execution"
    assert payload["plan"]["dry_run"] is False


def test_config_errors_are_redacted_and_machine_readable(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail(cls, path=None):
        raise ConfigError("authorization=do-not-print")

    monkeypatch.setattr(cli.Config, "load", classmethod(fail))

    code = cli.main(["doctor", "--json"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == cli.EXIT_ERROR
    assert "do-not-print" not in captured.out
    assert "do-not-print" not in captured.err
    assert "<redacted>" in payload["message"]

