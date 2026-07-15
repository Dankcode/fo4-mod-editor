from __future__ import annotations

from pathlib import Path

import pytest

from fo4_autopatch.config import Config, ConfigError, validate_archive_name, validate_plugin_name
from fo4_autopatch.jsonio import dumps


def test_name_validation_accepts_expected_files_and_rejects_traversal() -> None:
    assert validate_plugin_name("Patch.esp") == "Patch.esp"
    assert validate_archive_name("mod-1.0.zip") == "mod-1.0.zip"
    for value in ("../Patch.esp", "folder/Patch.esp", "NUL.esp", "bad?.esp"):
        with pytest.raises(ConfigError):
            validate_plugin_name(value)
    with pytest.raises(ConfigError):
        validate_archive_name("../mod.zip")


def test_load_and_public_dict_never_expose_nexus_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_path = tmp_path / "fo4ap.toml"
    values = {
        "fo4edit_exe": tmp_path / "FO4Edit64.exe",
        "game_data_dir": tmp_path / "Data",
        "plugins_txt": tmp_path / "plugins.txt",
        "vortex_downloads_dir": tmp_path / "downloads",
        "vortex_staging_dir": tmp_path / "staging",
        "work_dir": tmp_path / "work",
    }
    config_path.write_text(
        "[paths]\n"
        + "\n".join(f'{key} = "{path.as_posix()}"' for key, path in values.items())
        + "\n\n[xedit]\ntimeout_s = 120\n\n[safety]\npatch_plugin = \"SafePatch.esp\"\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NEXUS_API_KEY", "never-print-this-key")

    cfg = Config.load(config_path)

    assert cfg.patch_plugin == "SafePatch.esp"
    assert cfg.xedit_timeout_s == 120
    assert cfg.missing_values == []
    public = dumps(cfg.public_dict())
    assert "never-print-this-key" not in public
    assert '"nexus_api_key_set": true' in public


def test_load_rejects_unsafe_timeout(tmp_path: Path) -> None:
    config_path = tmp_path / "fo4ap.toml"
    config_path.write_text("[xedit]\ntimeout_s = 1\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="timeout_s"):
        Config.load(config_path)

