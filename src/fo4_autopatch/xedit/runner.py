"""Fresh-process FO4Edit runner with a versioned JSON result contract."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import backup
from ..config import Config, ConfigError, validate_plugin_name
from ..jsonio import load
from ..models import JSON_SCHEMA_VERSION, XEditResult

try:
    import psutil
except ImportError:  # pragma: no cover - declared dependency, defensive only
    psutil = None  # type: ignore[assignment]

_SESSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_MAX_SCRIPT_BYTES = 2 * 1024 * 1024
_DANGEROUS_FLAGS = ("-allowdirectsaves", "-allowmasterfilesedit", "-iknowwhatimdoing")


class XEditRunError(RuntimeError):
    """Raised when a scan cannot safely produce a trustworthy result."""


def _failure(log_path: Path, error: str, *, exit_code: int = -1, duration: float = 0.0) -> XEditResult:
    return XEditResult(
        ok=False,
        exit_code=exit_code,
        json_payload=None,
        log_path=log_path,
        duration_s=duration,
        error=error,
    )


class XEditSession:
    """Run one isolated xEdit process per script or cleaning request."""

    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def _validate_environment(self) -> None:
        if not self.cfg.fo4edit_exe.is_file():
            raise ConfigError(f"FO4Edit executable does not exist: {self.cfg.fo4edit_exe}")
        if not self.cfg.game_data_dir.is_dir():
            raise ConfigError(f"Fallout 4 Data directory does not exist: {self.cfg.game_data_dir}")
        if not self.cfg.plugins_txt.is_file():
            raise ConfigError(f"plugins.txt does not exist: {self.cfg.plugins_txt}")

    def _running_xedit(self) -> list[str]:
        if psutil is None:
            return []
        found: list[str] = []
        for process in psutil.process_iter(["pid", "name", "exe"]):
            try:
                name = str(process.info.get("name") or "").casefold()
                executable = str(process.info.get("exe") or "")
            except (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess):
                continue
            if name.endswith(".exe"):
                name = name[:-4]
            if name.startswith(("fo4edit", "xedit")):
                found.append(f"{process.info.get('pid')}:{executable or name}")
        return found

    def _validate_extra_args(self, extra_args: list[str] | None) -> list[str]:
        result: list[str] = []
        for raw in extra_args or []:
            if not isinstance(raw, str) or not raw or len(raw) > 4096:
                raise ConfigError("Every xEdit argument must be a non-empty string of at most 4096 characters")
            if any(ord(char) < 32 or ord(char) == 127 for char in raw):
                raise ConfigError("xEdit arguments cannot contain control characters")
            folded = raw.casefold()
            if folded.startswith(_DANGEROUS_FLAGS):
                raise ConfigError(f"Unsafe xEdit flag is not permitted: {raw}")
            if not raw.startswith("-"):
                validate_plugin_name(raw)
            elif folded != "-nobuildrefs":
                raise ConfigError(f"Unsupported caller-supplied xEdit flag: {raw}")
            result.append(raw)
        return result

    def _launch(self, argv: list[str], log_path: Path, timeout_s: int) -> tuple[int, str, float, bool]:
        started = time.monotonic()
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
        process = subprocess.Popen(
            argv,
            cwd=self.cfg.fo4edit_exe.parent,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=creationflags,
        )
        timed_out = False
        try:
            captured, _ = process.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.terminate()
            try:
                captured, _ = process.communicate(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                captured, _ = process.communicate()
        duration = time.monotonic() - started
        if not log_path.exists():
            log_path.write_text(captured or "", encoding="utf-8", newline="\n")
        elif captured:
            with log_path.open("a", encoding="utf-8", newline="\n") as stream:
                stream.write("\n[fo4ap captured process output]\n")
                stream.write(captured)
        return (-1 if timed_out else int(process.returncode or 0), captured or "", duration, timed_out)

    def run_script(
        self,
        pas_source: str,
        session_id: str,
        extra_args: list[str] | None = None,
    ) -> XEditResult:
        """Write a generated script, launch FO4Edit, and parse its JSON result."""

        if not _SESSION_RE.fullmatch(session_id):
            raise ConfigError("session_id must be 1-64 safe filename characters")
        if not isinstance(pas_source, str) or not pas_source.strip():
            raise ConfigError("A non-empty Pascal script is required")
        if "\x00" in pas_source or len(pas_source.encode("utf-8")) > _MAX_SCRIPT_BYTES:
            raise ConfigError("Pascal script is invalid or exceeds the 2 MiB limit")
        self._validate_environment()
        safe_extra = self._validate_extra_args(extra_args)
        self.cfg.ensure_work_dirs()
        script_path = self.cfg.work_dir / "scripts" / f"{session_id}.fo4pas"
        result_path = self.cfg.work_dir / "xedit_out" / f"{session_id}.json"
        log_path = self.cfg.work_dir / "logs" / f"{session_id}.log"
        if script_path.exists() or result_path.exists() or log_path.exists():
            raise ConfigError(f"Refusing to reuse xEdit session id: {session_id}")
        running = self._running_xedit()
        if running:
            return _failure(log_path, "Another xEdit/FO4Edit process is running: " + ", ".join(running))
        script_path.write_text(pas_source, encoding="utf-8", newline="\n")
        argv = [
            str(self.cfg.fo4edit_exe),
            "-fo4",
            "-autoload",
            "-autoexit",
            f"-script:{script_path}",
            f"-D:{self.cfg.game_data_dir}",
            f"-P:{self.cfg.plugins_txt}",
            f"-R:{log_path}",
            *safe_extra,
        ]
        try:
            exit_code, _captured, duration, timed_out = self._launch(argv, log_path, self.cfg.xedit_timeout_s)
        except OSError as exc:
            return _failure(log_path, f"Could not launch FO4Edit: {exc}")
        if timed_out:
            return _failure(log_path, f"FO4Edit exceeded the {self.cfg.xedit_timeout_s}s timeout", duration=duration)
        payload: dict[str, Any] | None = None
        parse_error: str | None = None
        if result_path.is_file():
            try:
                payload = load(result_path)
                if payload.get("schema_version") != JSON_SCHEMA_VERSION:
                    raise ValueError(f"unsupported bridge schema {payload.get('schema_version')!r}")
            except (OSError, ValueError) as exc:
                parse_error = f"Invalid xEdit result JSON: {exc}"
        else:
            parse_error = "xEdit did not produce its required JSON result"
        script_ok = bool(payload is not None and payload.get("ok", True) is not False)
        ok = exit_code == 0 and parse_error is None and script_ok
        error = parse_error
        if error is None and not script_ok:
            error = str(payload.get("error") or "xEdit script reported failure") if payload else "xEdit script reported failure"
        if error is None and exit_code != 0:
            error = f"FO4Edit exited with code {exit_code}"
        return XEditResult(
            ok=ok,
            exit_code=exit_code,
            json_payload=payload,
            log_path=log_path,
            duration_s=duration,
            error=error,
        )

    def quick_auto_clean(self, plugin: str) -> XEditResult:
        """Run xEdit Quick Auto Clean after a verified source checkpoint.

        This method is intentionally separate from conflict forwarding because
        it mutates the selected source plugin. Calling it is the explicit opt-in.
        """

        safe_plugin = validate_plugin_name(plugin)
        self._validate_environment()
        source = self.cfg.plugin_path(safe_plugin, require_exists=True)
        self.cfg.ensure_work_dirs()
        session_id = f"qac_{int(time.time_ns()):x}"
        log_path = self.cfg.work_dir / "logs" / f"{session_id}.log"
        running = self._running_xedit()
        if running:
            return _failure(log_path, "Another xEdit/FO4Edit process is running: " + ", ".join(running))
        backup_dir = backup.checkpoint(self.cfg, [safe_plugin], f"before-qac-{source.stem}")
        argv = [
            str(self.cfg.fo4edit_exe),
            "-fo4",
            "-quickautoclean",
            "-autoexit",
            f"-D:{self.cfg.game_data_dir}",
            f"-P:{self.cfg.plugins_txt}",
            f"-R:{log_path}",
            safe_plugin,
        ]
        try:
            exit_code, _captured, duration, timed_out = self._launch(argv, log_path, self.cfg.xedit_timeout_s)
        except OSError as exc:
            result = _failure(log_path, f"Could not launch FO4Edit: {exc}")
            result.backup_dir = backup_dir
            return result
        error = None
        if timed_out:
            error = f"FO4Edit exceeded the {self.cfg.xedit_timeout_s}s timeout; restore from the checkpoint before reuse"
        elif exit_code != 0:
            error = f"FO4Edit Quick Auto Clean exited with code {exit_code}"
        result = XEditResult(
            ok=error is None,
            exit_code=exit_code,
            json_payload={
                "schema_version": JSON_SCHEMA_VERSION,
                "kind": "quick_auto_clean",
                "plugin": safe_plugin,
                "source_sha256_after": backup.sha256_file(source) if source.is_file() else None,
            },
            log_path=log_path,
            duration_s=duration,
            error=error,
            backup_dir=backup_dir,
        )
        return result
