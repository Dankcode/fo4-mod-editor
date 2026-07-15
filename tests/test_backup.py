from __future__ import annotations

import json
from pathlib import Path

import pytest

from fo4_autopatch import backup
from fo4_autopatch.config import Config, ConfigError


def make_config(tmp_path: Path) -> Config:
    data = tmp_path / "Data"
    data.mkdir()
    plugins_txt = tmp_path / "plugins.txt"
    plugins_txt.write_text("*A.esp\n", encoding="utf-8")
    return Config(
        fo4edit_exe=tmp_path / "FO4Edit64.exe",
        game_data_dir=data,
        plugins_txt=plugins_txt,
        vortex_downloads_dir=tmp_path / "downloads",
        vortex_staging_dir=tmp_path / "staging",
        work_dir=tmp_path / "work",
        bridge_dir=tmp_path / "bridge",
    )


def test_checkpoint_manifest_and_verified_restore(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    plugin = cfg.game_data_dir / "A.esp"
    plugin.write_bytes(b"original-plugin")

    backup_dir = backup.checkpoint(cfg, ["A.esp"], "before-fix")
    manifest = json.loads((backup_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == backup.MANIFEST_VERSION
    assert len(manifest["entries"]) == 2

    plugin.write_bytes(b"changed-plugin")
    cfg.plugins_txt.write_text("# changed\n", encoding="utf-8")
    changes = backup.inspect_restore(backup_dir)
    assert all(item["changed"] for item in changes)

    restored = backup.restore(backup_dir)
    assert set(restored) == {plugin.resolve(), cfg.plugins_txt.resolve()}
    assert plugin.read_bytes() == b"original-plugin"
    assert cfg.plugins_txt.read_text(encoding="utf-8") == "*A.esp\n"
    assert backup.restore(backup_dir) == []


def test_checkpoint_requires_at_least_one_plugin(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    with pytest.raises(ConfigError, match="at least one"):
        backup.checkpoint(cfg, [], "empty")


def test_prune_keeps_newest_checkpoints(tmp_path: Path) -> None:
    cfg = make_config(tmp_path)
    (cfg.game_data_dir / "A.esp").write_bytes(b"plugin")
    created = [backup.checkpoint(cfg, ["A.esp"], f"checkpoint-{index}") for index in range(3)]

    removed = backup.prune(cfg, keep_last=1)

    assert len(removed) == 2
    remaining = [path for path in created if path.exists()]
    assert len(remaining) == 1
    assert remaining[0] == max(created, key=lambda path: path.name)

