"""Validated TOML configuration with environment-only secret handling."""

from __future__ import annotations

import os
import re
import sys
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

GAME_DOMAIN = "fallout4"
PLUGIN_RE = re.compile(r"^[^<>:\"/\\|?*\x00-\x1f]{1,240}\.(?:esm|esp|esl)$", re.IGNORECASE)


class ConfigError(RuntimeError):
    """Raised for missing, malformed, or unsafe configuration."""


def _reject_persisted_secrets(value: Any, trail: tuple[str, ...] = ()) -> None:
    """Fail closed if a TOML file contains credential-shaped fields."""
    if not isinstance(value, dict):
        return
    forbidden = {"apikey", "api_key", "nexus_api_key", "token", "access_token", "authorization"}
    for key, child in value.items():
        name = str(key)
        if name.lower() in forbidden:
            location = ".".join((*trail, name))
            raise ConfigError(
                f"Refusing persisted secret field {location!r}; use NEXUS_API_KEY from a secret-bearing environment"
            )
        _reject_persisted_secrets(child, (*trail, name))


def expand_path(value: str | os.PathLike[str] | None, default: Path) -> Path:
    if value in (None, ""):
        return default.expanduser().resolve(strict=False)
    expanded = os.path.expandvars(os.path.expanduser(os.fspath(value)))
    return Path(expanded).resolve(strict=False)


def _safe_is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


def validate_plugin_name(name: str) -> str:
    if name in {".", ".."} or not PLUGIN_RE.fullmatch(name) or Path(name).name != name:
        raise ConfigError(f"Unsafe plugin name: {name!r}")
    stem = Path(name).stem.rstrip(" .").upper()
    if stem in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", stem):
        raise ConfigError(f"Reserved Windows plugin name: {name!r}")
    return name


def validate_archive_name(name: str) -> str:
    if Path(name).name != name or name in {".", ".."}:
        raise ConfigError(f"Unsafe archive name: {name!r}")
    if any(ord(ch) < 32 or ch in '<>:"/\\|?*' for ch in name):
        raise ConfigError(f"Unsafe archive name: {name!r}")
    return name


@dataclass(slots=True)
class Config:
    fo4edit_exe: Path
    game_data_dir: Path
    plugins_txt: Path
    vortex_downloads_dir: Path
    vortex_staging_dir: Path
    work_dir: Path
    bridge_dir: Path
    xedit_timeout_s: int = 1800
    patch_plugin: str = "For4ConflictPatch.esp"
    allow_experimental_full_merge: bool = False
    config_path: Path | None = None
    missing_values: list[str] = field(default_factory=list)

    @property
    def nexus_api_key(self) -> str:
        key = os.environ.get("NEXUS_API_KEY", "").strip()
        if not key:
            raise ConfigError("NEXUS_API_KEY is not set. Generate a new personal key and keep it in a secret-bearing environment.")
        return key

    @property
    def nexus_api_key_is_set(self) -> bool:
        return bool(os.environ.get("NEXUS_API_KEY", "").strip())

    def ensure_work_dirs(self) -> None:
        for path in (
            self.work_dir,
            self.work_dir / "scripts",
            self.work_dir / "xedit_out",
            self.work_dir / "logs",
            self.work_dir / "reports",
            self.work_dir / "backups",
            self.work_dir / "downloads",
            self.work_dir / "queues",
            self.bridge_dir / "requests",
            self.bridge_dir / "results",
        ):
            path.mkdir(parents=True, exist_ok=True)

    def plugin_path(self, name: str, *, require_exists: bool = False) -> Path:
        safe = validate_plugin_name(name)
        path = (self.game_data_dir / safe).resolve(strict=False)
        try:
            path.relative_to(self.game_data_dir.resolve(strict=False))
        except ValueError as exc:
            raise ConfigError(f"Plugin escapes Data directory: {name!r}") from exc
        if require_exists and not path.is_file():
            raise ConfigError(f"Plugin does not exist: {path}")
        return path

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        local_app = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        app_data = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
        chosen = path or (Path(os.environ["FO4AP_CONFIG"]) if os.environ.get("FO4AP_CONFIG") else None)
        if chosen is None:
            cwd_candidate = Path.cwd() / "fo4ap.toml"
            chosen = cwd_candidate if cwd_candidate.is_file() else local_app / "for4-mod-editor" / "fo4ap.toml"
        chosen = Path(chosen).expanduser().resolve(strict=False)

        raw: dict[str, Any] = {}
        if _safe_is_file(chosen):
            try:
                with chosen.open("rb") as stream:
                    raw = tomllib.load(stream)
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"Could not read configuration {chosen}: {exc}") from exc
        _reject_persisted_secrets(raw)

        paths = raw.get("paths", {})
        xedit = raw.get("xedit", {})
        safety = raw.get("safety", {})
        missing: list[str] = []

        def configured(env_name: str, key: str) -> str | None:
            value = os.environ.get(env_name) or paths.get(key)
            if not value:
                missing.append(f"paths.{key}")
            return value

        default_steam_data = Path(os.environ.get("PROGRAMFILES(X86)", "C:/Program Files (x86)")) / "Steam/steamapps/common/Fallout 4/Data"
        default_plugins = local_app / "Fallout4" / "plugins.txt"
        default_work = local_app / "for4-mod-editor"

        fo4edit_value = configured("FO4EDIT_EXE", "fo4edit_exe")
        game_data_value = configured("FO4_DATA_DIR", "game_data_dir")
        plugins_value = os.environ.get("FO4_PLUGINS_TXT") or paths.get("plugins_txt")
        vortex_downloads_value = configured("VORTEX_DOWNLOADS_DIR", "vortex_downloads_dir")
        vortex_staging_value = configured("VORTEX_STAGING_DIR", "vortex_staging_dir")
        if not plugins_value and not _safe_is_file(default_plugins):
            missing.append("paths.plugins_txt")

        patch_plugin = validate_plugin_name(str(safety.get("patch_plugin", "For4ConflictPatch.esp")))
        timeout = int(xedit.get("timeout_s", 1800))
        if timeout < 30 or timeout > 86_400:
            raise ConfigError("xedit.timeout_s must be between 30 and 86400 seconds")

        cfg = cls(
            fo4edit_exe=expand_path(fo4edit_value, Path("C:/Tools/xEdit/FO4Edit64.exe")),
            game_data_dir=expand_path(game_data_value, default_steam_data),
            plugins_txt=expand_path(plugins_value, default_plugins),
            vortex_downloads_dir=expand_path(vortex_downloads_value, local_app / "for4-mod-editor" / "UNCONFIGURED-downloads"),
            vortex_staging_dir=expand_path(vortex_staging_value, local_app / "for4-mod-editor" / "UNCONFIGURED-staging"),
            work_dir=expand_path(os.environ.get("FO4AP_WORK_DIR") or paths.get("work_dir"), default_work),
            bridge_dir=expand_path(os.environ.get("FO4AP_BRIDGE_DIR") or paths.get("bridge_dir"), default_work / "vortex-bridge"),
            xedit_timeout_s=timeout,
            patch_plugin=patch_plugin,
            allow_experimental_full_merge=bool(safety.get("allow_experimental_full_merge", False)),
            config_path=chosen,
            missing_values=sorted(set(missing)),
        )
        return cfg

    def public_dict(self) -> dict[str, Any]:
        """Return diagnostic configuration without secret values."""
        return {
            "config_path": str(self.config_path) if self.config_path else None,
            "fo4edit_exe": str(self.fo4edit_exe),
            "game_data_dir": str(self.game_data_dir),
            "plugins_txt": str(self.plugins_txt),
            "vortex_downloads_dir": str(self.vortex_downloads_dir),
            "vortex_staging_dir": str(self.vortex_staging_dir),
            "work_dir": str(self.work_dir),
            "bridge_dir": str(self.bridge_dir),
            "xedit_timeout_s": self.xedit_timeout_s,
            "patch_plugin": self.patch_plugin,
            "allow_experimental_full_merge": self.allow_experimental_full_merge,
            "nexus_api_key_set": self.nexus_api_key_is_set,
            "missing_values": list(self.missing_values),
            "python": sys.version.split()[0],
        }
