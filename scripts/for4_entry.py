"""PyInstaller entry point for the standalone Windows executable."""

from fo4_autopatch.cli import main


if __name__ == "__main__":
    raise SystemExit(main())
