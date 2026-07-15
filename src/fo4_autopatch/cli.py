"""Command-line interface with deterministic JSON output and explicit write gates."""

from __future__ import annotations

import argparse
import contextlib
import inspect
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Sequence

from . import __version__, backup, doctor
from .config import Config, ConfigError, GAME_DOMAIN, validate_archive_name
from .jsonio import atomic_write, dumps, load, redact
from .models import ConflictReport, InstalledMod

EXIT_OK = 0
EXIT_CHANGED = 1
EXIT_REVIEW = 2
EXIT_ERROR = 3
EXIT_USAGE = 4


class UsageError(ValueError):
    """Raised for command-line combinations that argparse cannot express."""


class SafeArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


@dataclass(slots=True)
class Outcome:
    code: int
    payload: Any


def _conflict_modules():
    from .conflicts import fixer, notifier, scanner

    return fixer, notifier, scanner


def _merge_module():
    from .merge import merger

    return merger


def _nexus_modules():
    from . import nexus as downloader
    from .nexus import NexusClient, updates

    return NexusClient, downloader, updates


def _vortex_module():
    from . import vortex as bridge

    return bridge


def _xedit_session_class():
    from .xedit.runner import XEditSession

    return XEditSession


def _payload(kind: str, **values: Any) -> dict[str, Any]:
    return {"schema_version": 1, "kind": kind, **values}


def _emit(value: Any, *, compact: bool) -> None:
    # Stdout is always JSON. --json selects compact output for agent callers.
    print(dumps(value, pretty=not compact))


def _diagnose(message: str) -> None:
    clean = redact(str(message))
    print(clean if isinstance(clean, str) else str(clean), file=sys.stderr)


def _confirmation(args: argparse.Namespace, plan: Any, action: str) -> Outcome | None:
    if getattr(args, "yes", False) and not getattr(args, "apply", False):
        raise UsageError("--yes is valid only together with --apply")
    if getattr(args, "apply", False) and not getattr(args, "yes", False):
        return Outcome(
            EXIT_REVIEW,
            _payload(
                "approval_required",
                action=action,
                message="Review the dry-run plan, then repeat with both --apply and --yes.",
                plan=plan,
            ),
        )
    return None


def _write_optional(path: Path | None, value: Any) -> None:
    if path is not None:
        atomic_write(path, value)


def _report_and_notify(
    cfg: Config,
    report: ConflictReport,
    notifier: Any,
    *,
    output: Path | None,
    send_notification: bool,
) -> ConflictReport:
    report_path = notifier.write_report(cfg, report)
    report.report_path = Path(report_path)
    _write_optional(output, report)
    if send_notification:
        # Notification fallbacks may print; preserve the JSON-only stdout contract.
        with contextlib.redirect_stdout(sys.stderr):
            notifier.notify(cfg, report, report.report_path)
    return report


def _handle_doctor(cfg: Config, args: argparse.Namespace) -> Outcome:
    result = doctor.run(cfg)
    return Outcome(EXIT_OK if result.get("ok") else EXIT_ERROR, result)


def _handle_scan(cfg: Config, args: argparse.Namespace) -> Outcome:
    _fixer, notifier, scanner = _conflict_modules()
    report = scanner.scan(cfg, args.plugins or None)
    _report_and_notify(cfg, report, notifier, output=args.output, send_notification=not args.no_notify)
    return Outcome(report.exit_code, report)


def _load_conflict_report(path: Path) -> ConflictReport:
    return ConflictReport.from_dict(load(path))


def _handle_fix(cfg: Config, args: argparse.Namespace) -> Outcome:
    fixer, notifier, scanner = _conflict_modules()
    source = _load_conflict_report(args.report) if args.report else scanner.scan(cfg, args.plugins or None)
    gate = _confirmation(args, source, "write a new override patch")
    if gate is not None:
        return gate
    result = fixer.apply_fixes(cfg, source, dry_run=not args.apply)
    _report_and_notify(cfg, result, notifier, output=args.output, send_notification=not args.no_notify)
    if args.apply:
        code = result.exit_code
    else:
        code = EXIT_REVIEW if result.records else EXIT_OK
    return Outcome(code, result)


def _handle_merge(cfg: Config, args: argparse.Namespace) -> Outcome:
    merger = _merge_module()
    plan = merger.plan_merge(cfg, args.sources, args.out)
    _write_optional(args.plan_output, plan)
    if not getattr(plan, "ready", not getattr(plan, "blockers", [])):
        return Outcome(EXIT_REVIEW, plan)
    gate = _confirmation(args, plan, "create the planned merge output")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    plan.dry_run = False
    result = merger.execute(cfg, plan)
    payload = _payload("merge_execution", plan=plan, result=result)
    return Outcome(EXIT_CHANGED if getattr(result, "ok", False) else EXIT_ERROR, payload)


def _make_nexus_client(client_class: type, cfg: Config):
    parameters = list(inspect.signature(client_class).parameters.values())
    first_name = parameters[0].name if parameters else "api_key"
    if first_name in {"cfg", "config"}:
        return client_class(cfg)
    return client_class(cfg.nexus_api_key)


def _parse_installed(value: str) -> InstalledMod:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise UsageError("--installed must use MOD_ID:FILE_ID:VERSION")
    try:
        mod_id, file_id = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise UsageError("MOD_ID and FILE_ID in --installed must be integers") from exc
    if mod_id <= 0 or file_id <= 0 or not parts[2].strip():
        raise UsageError("--installed values must be positive and include a version")
    return InstalledMod(
        instance_id=f"cli:{mod_id}",
        name=f"Nexus mod {mod_id}",
        mod_id=mod_id,
        file_id=file_id,
        version=parts[2].strip(),
    )


def _handle_update(cfg: Config, args: argparse.Namespace) -> Outcome:
    client_class, _downloader, updates = _nexus_modules()
    client = _make_nexus_client(client_class, cfg)
    if hasattr(updates, "check_updates"):
        found = updates.check_updates(cfg, client)
        rendered = updates.report(found) if hasattr(updates, "report") else None
    else:
        installed = [_parse_installed(value) for value in args.installed]
        if not installed:
            raise UsageError("At least one --installed MOD_ID:FILE_ID:VERSION is required")
        available: dict[int, Any] = {}
        for item in installed:
            assert item.mod_id is not None
            available[item.mod_id] = client.list_mod_files(GAME_DOMAIN, item.mod_id)
        decisions = updates.compare_updates(installed, available)
        found = [decision for decision in decisions if decision.update_available]
        rendered = None
    result = _payload(
        "updates",
        count=len(found),
        updates=found,
        report=rendered,
        rate_limits=getattr(client, "rate_limits", None),
    )
    return Outcome(EXIT_CHANGED if found else EXIT_OK, result)


def _handle_download(cfg: Config, args: argparse.Namespace) -> Outcome:
    plan = _payload(
        "download_plan",
        game=GAME_DOMAIN,
        mod_id=args.mod_id,
        file_id=args.file_id,
        file_name=args.file_name,
        destination=str(cfg.vortex_downloads_dir / args.file_name),
        sequential=True,
    )
    gate = _confirmation(args, plan, "download one Nexus file")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    client_class, downloader, _updates = _nexus_modules()
    client = _make_nexus_client(client_class, cfg)
    nxm_value = os.environ.get(args.nxm_url_env, "") if args.nxm_url_env else ""
    nxm_link = (
        downloader.parse_nxm_url(
            nxm_value,
            expected_game=GAME_DOMAIN,
            now=getattr(client, "clock", None)() if callable(getattr(client, "clock", None)) else None,
        )
        if nxm_value
        else None
    )
    links = client.download_links(GAME_DOMAIN, args.mod_id, args.file_id, nxm_link=nxm_link)
    metadata = next(
        (item for item in client.list_mod_files(GAME_DOMAIN, args.mod_id) if item.file_id == args.file_id),
        None,
    )
    destination = cfg.vortex_downloads_dir / args.file_name
    spec = downloader.DownloadSpec(
        links[0],
        destination,
        expected_size=metadata.size_bytes if metadata and metadata.size_bytes > 0 else None,
        md5=metadata.md5 if metadata else None,
    )
    result = downloader.SequentialDownloader().download(spec)
    return Outcome(EXIT_CHANGED, _payload("download_result", result=result))


def _parse_queue_item(value: str) -> tuple[int, int, str]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise UsageError("Queue items must use MOD_ID:FILE_ID:FILE_NAME")
    try:
        mod_id, file_id = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise UsageError("Queue MOD_ID and FILE_ID must be integers") from exc
    return mod_id, file_id, validate_archive_name(parts[2])


def _handle_queue_download(cfg: Config, args: argparse.Namespace) -> Outcome:
    items = [_parse_queue_item(value) for value in args.items]
    plan = _payload("download_queue_plan", items=[{"mod_id": m, "file_id": f, "file_name": n} for m, f, n in items])
    gate = _confirmation(args, plan, "persist the download queue")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    client_class, downloader, _updates = _nexus_modules()
    if hasattr(downloader, "free_queue"):
        client = _make_nexus_client(client_class, cfg)
        downloader.free_queue(cfg, client, [(mod_id, file_id) for mod_id, file_id, _name in items])
        result: Any = {"queued": len(items)}
    elif hasattr(downloader, "DownloadQueue"):
        queue = downloader.DownloadQueue(args.queue or cfg.work_dir / "queues" / "downloads.json")
        result = [queue.enqueue(GAME_DOMAIN, mod_id, file_id, name) for mod_id, file_id, name in items]
    else:
        raise RuntimeError("The installed downloader does not expose a queue API")
    return Outcome(EXIT_CHANGED, _payload("download_queue", items=result))


def _archive_path(result: Any) -> Path:
    if isinstance(result, Path):
        return result
    for name in ("archive_path", "path", "output_path"):
        value = getattr(result, name, None)
        if value:
            return Path(value)
    raise RuntimeError("Vortex archive builder did not return an archive path")


def _handle_install(cfg: Config, args: argparse.Namespace) -> Outcome:
    if args.deploy and not args.bridge:
        raise UsageError("--deploy requires --bridge so Vortex performs the deployment")
    files = [Path(value).resolve(strict=False) for value in args.files]
    missing = [str(path) for path in files if not path.is_file()]
    if missing:
        raise ConfigError(f"Install input files do not exist: {', '.join(missing)}")
    plan = _payload(
        "vortex_install_plan",
        mod_name=args.name,
        version=args.version,
        files=[str(path) for path in files],
        bridge=args.bridge,
        deploy=args.deploy,
    )
    gate = _confirmation(args, plan, "build and submit a Vortex mod archive")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    bridge = _vortex_module()
    if hasattr(bridge, "install_local_mod"):
        archive_result: Any = bridge.install_local_mod(cfg, files, args.name, args.version)
    else:
        archive_result = bridge.build_vortex_archive(
            files,
            cfg.work_dir / "packages",
            mod_name=args.name,
            version=args.version,
        )
    request = None
    if args.bridge:
        if not hasattr(bridge, "BridgeQueue"):
            raise RuntimeError("The installed Vortex integration has no bridge queue")
        queue = bridge.BridgeQueue(cfg.bridge_dir)
        request = queue.submit_archive(_archive_path(archive_result), install=True, deploy=args.deploy)
    return Outcome(EXIT_CHANGED, _payload("vortex_install", archive=archive_result, bridge_request=request))


def _handle_clean(cfg: Config, args: argparse.Namespace) -> Outcome:
    plan = _payload("quick_auto_clean_plan", plugin=args.plugin)
    gate = _confirmation(args, plan, "run xEdit Quick Auto Clean")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    result = _xedit_session_class()(cfg).quick_auto_clean(args.plugin)
    return Outcome(EXIT_CHANGED if result.ok else EXIT_ERROR, _payload("quick_auto_clean", result=result))


def _handle_backup_checkpoint(cfg: Config, args: argparse.Namespace) -> Outcome:
    plugin_paths = [str(cfg.plugin_path(name, require_exists=True)) for name in args.plugins]
    plan = _payload("backup_checkpoint_plan", label=args.label, plugins=plugin_paths)
    gate = _confirmation(args, plan, "create a checkpoint")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    path = backup.checkpoint(cfg, args.plugins, args.label)
    return Outcome(EXIT_CHANGED, _payload("backup_checkpoint", backup_dir=str(path)))


def _handle_backup_restore(cfg: Config, args: argparse.Namespace) -> Outcome:
    changes = backup.inspect_restore(args.backup_dir)
    plan = _payload("backup_restore_plan", backup_dir=str(args.backup_dir), changes=changes)
    gate = _confirmation(args, plan, "restore checkpoint files")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW if any(item["changed"] for item in changes) else EXIT_OK, plan)
    restored = backup.restore(args.backup_dir)
    return Outcome(EXIT_CHANGED if restored else EXIT_OK, _payload("backup_restore", restored=restored))


def _handle_backup_prune(cfg: Config, args: argparse.Namespace) -> Outcome:
    plan = _payload("backup_prune_plan", keep_last=args.keep_last)
    gate = _confirmation(args, plan, "delete old checkpoints")
    if gate is not None:
        return gate
    if not args.apply:
        return Outcome(EXIT_REVIEW, plan)
    removed = backup.prune(cfg, args.keep_last)
    return Outcome(EXIT_CHANGED if removed else EXIT_OK, _payload("backup_prune", removed=removed))


def _add_output_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", help="emit compact machine-readable JSON")


def _add_write_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--apply", action="store_true", help="perform the planned write")
    parser.add_argument("--yes", action="store_true", help="confirm a reviewed write plan; requires --apply")


def _command(
    subparsers: Any,
    name: str,
    help_text: str,
    handler: Callable[[Config, argparse.Namespace], Outcome],
) -> argparse.ArgumentParser:
    parser = subparsers.add_parser(name, help=help_text)
    _add_output_flag(parser)
    parser.set_defaults(handler=handler)
    return parser


def build_parser() -> argparse.ArgumentParser:
    parser = SafeArgumentParser(prog="fo4ap", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.add_argument("--config", type=Path, help="path to fo4ap.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    _command(subparsers, "doctor", "validate configuration and external tools", _handle_doctor)

    scan = _command(subparsers, "scan", "scan the active load order", _handle_scan)
    scan.add_argument("--plugins", nargs="+", default=[])
    scan.add_argument("--output", type=Path, help="also save the JSON report")
    scan.add_argument("--no-notify", action="store_true")

    fix = _command(subparsers, "fix", "plan or create a conservative override patch", _handle_fix)
    fix.add_argument("--report", type=Path, help="existing scan report")
    fix.add_argument("--plugins", nargs="+", default=[])
    fix.add_argument("--output", type=Path, help="also save the residual JSON report")
    fix.add_argument("--no-notify", action="store_true")
    _add_write_flags(fix)

    merge = _command(subparsers, "merge", "plan or execute an override merge", _handle_merge)
    merge.add_argument("--out", required=True, help="output ESP name")
    merge.add_argument("sources", nargs="+")
    merge.add_argument("--plan-output", type=Path)
    _add_write_flags(merge)

    clean = _command(subparsers, "clean", "plan or run xEdit Quick Auto Clean", _handle_clean)
    clean.add_argument("plugin")
    _add_write_flags(clean)

    update = _command(subparsers, "update", "check Nexus metadata for updates", _handle_update)
    update.add_argument("--installed", action="append", default=[], metavar="MOD_ID:FILE_ID:VERSION")

    download = _command(subparsers, "download", "plan or download one Nexus file", _handle_download)
    download.add_argument("--mod-id", type=int, required=True)
    download.add_argument("--file-id", type=int, required=True)
    download.add_argument("--file-name", type=validate_archive_name, required=True)
    download.add_argument("--nxm-url-env", default="NEXUS_NXM_URL", help="environment variable containing a user-authorized NXM URL")
    _add_write_flags(download)

    queue = _command(subparsers, "queue-download", "plan or persist sequential downloads", _handle_queue_download)
    queue.add_argument("items", nargs="+", metavar="MOD_ID:FILE_ID:FILE_NAME")
    queue.add_argument("--queue", type=Path)
    _add_write_flags(queue)

    install = _command(subparsers, "install", "plan or package files for Vortex", _handle_install)
    install.add_argument("--name", required=True)
    install.add_argument("--version", required=True)
    install.add_argument("files", nargs="+")
    install.add_argument("--bridge", action="store_true", help="submit the archive to the optional Vortex bridge")
    install.add_argument("--deploy", action="store_true", help="request deployment through the bridge")
    _add_write_flags(install)

    backups = subparsers.add_parser("backup", help="checkpoint and rollback operations")
    backup_subparsers = backups.add_subparsers(dest="backup_command", required=True)
    checkpoint = _command(backup_subparsers, "checkpoint", "plan or create a checkpoint", _handle_backup_checkpoint)
    checkpoint.add_argument("--label", default="manual")
    checkpoint.add_argument("plugins", nargs="+")
    _add_write_flags(checkpoint)
    restore_parser = _command(backup_subparsers, "restore", "inspect or restore a checkpoint", _handle_backup_restore)
    restore_parser.add_argument("backup_dir", type=Path)
    _add_write_flags(restore_parser)
    prune_parser = _command(backup_subparsers, "prune", "plan or remove old checkpoints", _handle_backup_prune)
    prune_parser.add_argument("--keep-last", type=int, default=10)
    _add_write_flags(prune_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args: argparse.Namespace | None = None
    try:
        args = parser.parse_args(argv)
        cfg = Config.load(args.config)
        outcome = args.handler(cfg, args)
        _emit(outcome.payload, compact=bool(args.json))
        return outcome.code
    except UsageError as exc:
        _diagnose(f"usage error: {exc}")
        _emit(_payload("error", error="usage", message=str(redact(str(exc)))), compact=bool(getattr(args, "json", False)))
        return EXIT_USAGE
    except (ConfigError, OSError, RuntimeError, ValueError) as exc:
        message = str(redact(str(exc)))
        _diagnose(f"error: {message}")
        _emit(_payload("error", error=exc.__class__.__name__, message=message), compact=bool(getattr(args, "json", False)))
        return EXIT_ERROR
    except KeyboardInterrupt:
        _diagnose("interrupted")
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
