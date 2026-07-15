"""Deterministic, dry-run-first wrapper for the fo4ap JSON CLI."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _approved(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if not args.approve_write:
        parser.error("write commands require --approve-write after reviewing the matching plan")


def _config_prefix(args: argparse.Namespace) -> list[str]:
    command = [sys.executable, "-m", "fo4_autopatch"]
    if args.config:
        command.extend(["--config", str(args.config)])
    return command


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, help="path to fo4ap.toml")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor")

    scan = subparsers.add_parser("scan")
    scan.add_argument("--plugins", nargs="*", default=[])
    scan.add_argument("--output", type=Path)

    for name in ("plan-fix", "apply-fix"):
        fix = subparsers.add_parser(name)
        fix.add_argument("--report", type=Path, required=True)
        fix.add_argument("--output", type=Path)
        if name == "apply-fix":
            fix.add_argument("--approve-write", action="store_true")

    for name in ("plan-merge", "apply-merge"):
        merge = subparsers.add_parser(name)
        merge.add_argument("--out", required=True)
        merge.add_argument("sources", nargs="+")
        merge.add_argument("--plan-output", type=Path)
        if name == "apply-merge":
            merge.add_argument("--approve-write", action="store_true")

    for name in ("plan-install", "apply-install"):
        install = subparsers.add_parser(name)
        install.add_argument("--name", required=True)
        install.add_argument("--version", required=True)
        install.add_argument("files", nargs="+")
        install.add_argument("--bridge", action="store_true")
        install.add_argument("--deploy", action="store_true")
        if name == "apply-install":
            install.add_argument("--approve-write", action="store_true")

    for name in ("plan-restore", "apply-restore"):
        restore = subparsers.add_parser(name)
        restore.add_argument("backup_dir", type=Path)
        if name == "apply-restore":
            restore.add_argument("--approve-write", action="store_true")

    return parser


def build_command(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[str]:
    command = _config_prefix(args)
    if args.command == "doctor":
        command.extend(["doctor", "--json"])
    elif args.command == "scan":
        command.append("scan")
        if args.plugins:
            command.extend(["--plugins", *args.plugins])
        if args.output:
            command.extend(["--output", str(args.output)])
        command.append("--json")
    elif args.command in {"plan-fix", "apply-fix"}:
        command.extend(["fix", "--report", str(args.report)])
        if args.output:
            command.extend(["--output", str(args.output)])
        if args.command == "apply-fix":
            _approved(args, parser)
            command.extend(["--apply", "--yes"])
        command.append("--json")
    elif args.command in {"plan-merge", "apply-merge"}:
        command.extend(["merge", "--out", args.out, *args.sources])
        if args.plan_output:
            command.extend(["--plan-output", str(args.plan_output)])
        if args.command == "apply-merge":
            _approved(args, parser)
            command.extend(["--apply", "--yes"])
        command.append("--json")
    elif args.command in {"plan-install", "apply-install"}:
        command.extend(["install", "--name", args.name, "--version", args.version, *args.files])
        if args.bridge:
            command.append("--bridge")
        if args.deploy:
            command.append("--deploy")
        if args.command == "apply-install":
            _approved(args, parser)
            command.extend(["--apply", "--yes"])
        command.append("--json")
    elif args.command in {"plan-restore", "apply-restore"}:
        command.extend(["backup", "restore", str(args.backup_dir)])
        if args.command == "apply-restore":
            _approved(args, parser)
            command.extend(["--apply", "--yes"])
        command.append("--json")
    else:
        parser.error(f"unsupported command: {args.command}")
    return command


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    command = build_command(args, parser)
    environment = os.environ.copy()
    environment.setdefault("PYTHONUTF8", "1")
    completed = subprocess.run(command, check=False, env=environment, shell=False)
    return completed.returncode


if __name__ == "__main__":
    raise SystemExit(main())

