"""Read-only environment diagnostics."""

from __future__ import annotations

import importlib.util
import os
import platform
import sys
from pathlib import Path
from typing import Any

from .config import Config


def _check(name: str, ok: bool, detail: str, required: bool = True) -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "required": required, "detail": detail}


def _is_file(path: Path | None) -> bool:
    try:
        return bool(path and path.is_file())
    except OSError:
        return False


def _is_dir(path: Path | None) -> bool:
    try:
        return bool(path and path.is_dir())
    except OSError:
        return False


def _exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def run(cfg: Config) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    checks.append(_check("windows", os.name == "nt", platform.platform()))
    checks.append(_check("python", sys.version_info >= (3, 11), sys.version.split()[0]))
    checks.append(_check("config", _is_file(cfg.config_path), str(cfg.config_path), required=False))
    checks.append(_check("configured_paths", not cfg.missing_values, ", ".join(cfg.missing_values) or "all required path keys present"))
    checks.append(_check("fo4edit", _is_file(cfg.fo4edit_exe), str(cfg.fo4edit_exe)))
    checks.append(_check("game_data", _is_dir(cfg.game_data_dir), str(cfg.game_data_dir)))
    checks.append(_check("plugins_txt", _is_file(cfg.plugins_txt), str(cfg.plugins_txt)))
    checks.append(_check("vortex_downloads", _is_dir(cfg.vortex_downloads_dir), str(cfg.vortex_downloads_dir), required=False))
    checks.append(_check("vortex_staging", _is_dir(cfg.vortex_staging_dir), str(cfg.vortex_staging_dir), required=False))
    checks.append(_check("requests", importlib.util.find_spec("requests") is not None, "Python package requests"))
    checks.append(_check("psutil", importlib.util.find_spec("psutil") is not None, "Python package psutil"))
    checks.append(_check("nexus_key", cfg.nexus_api_key_is_set, "NEXUS_API_KEY set" if cfg.nexus_api_key_is_set else "not set; required only for standalone Nexus commands", required=False))
    extension_candidates = [
        Path(os.environ.get("APPDATA", "")) / "@vortex/main/plugins/for4-mod-editor",
        Path(os.environ.get("APPDATA", "")) / "Vortex/plugins/for4-mod-editor",
    ]
    extension = next((p for p in extension_candidates if _exists(p)), None)
    checks.append(_check("vortex_bridge", extension is not None, str(extension or "optional extension not installed"), required=False))
    required_ok = all(item["ok"] for item in checks if item["required"])
    return {"schema_version": 1, "kind": "doctor", "ok": required_ok, "checks": checks, "config": cfg.public_dict()}
