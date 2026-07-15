from __future__ import annotations

from pathlib import Path

from fo4_autopatch.config import Config
from fo4_autopatch.merge.merger import plan_merge
from fo4_autopatch.models import MergeMode


def _config(tmp_path: Path) -> Config:
    data = tmp_path / "Data"
    data.mkdir()
    return Config(
        fo4edit_exe=tmp_path / "FO4Edit64.exe",
        game_data_dir=data,
        plugins_txt=tmp_path / "plugins.txt",
        vortex_downloads_dir=tmp_path / "downloads",
        vortex_staging_dir=tmp_path / "staging",
        work_dir=tmp_path / "work",
        bridge_dir=tmp_path / "bridge",
    )


def test_default_merge_is_source_retaining(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    (cfg.game_data_dir / "A.esp").write_bytes(b"fixture")

    plan = plan_merge(cfg, ["A.esp"], "Patch.esp")

    assert plan.ready
    assert plan.mode is MergeMode.OVERRIDE_PATCH
    assert plan.masters == ["A.esp"]
    assert any("Keep every source plugin" in warning for warning in plan.warnings)


def test_full_merge_stays_blocked_even_when_config_gate_enabled(tmp_path: Path) -> None:
    cfg = _config(tmp_path)
    cfg.allow_experimental_full_merge = True
    (cfg.game_data_dir / "A.esp").write_bytes(b"fixture")

    plan = plan_merge(cfg, ["A.esp"], "Merged.esp", mode=MergeMode.FULL)

    assert not plan.ready
    assert any("execution is intentionally unavailable" in blocker for blocker in plan.blockers)

