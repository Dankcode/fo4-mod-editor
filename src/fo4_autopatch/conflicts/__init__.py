"""Conservative conflict scanning, patch planning, and notifications."""

from .fixer import apply_fixes, classify
from .notifier import notify, write_report
from .scanner import scan

__all__ = ["apply_fixes", "classify", "notify", "scan", "write_report"]
