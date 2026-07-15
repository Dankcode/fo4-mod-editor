"""Safe process and Pascal-script boundary for xEdit/FO4Edit."""

from .pas_templates import (
    BATCH_EDIT_PAS,
    CONFLICT_EXPORT_PAS,
    MERGE_PAS,
    PascalTemplateError,
    render,
    render_batch_patch,
    render_conflict_export,
    render_override_merge,
)
from .runner import XEditRunError, XEditSession

__all__ = [
    "BATCH_EDIT_PAS",
    "CONFLICT_EXPORT_PAS",
    "MERGE_PAS",
    "PascalTemplateError",
    "XEditRunError",
    "XEditSession",
    "render",
    "render_batch_patch",
    "render_conflict_export",
    "render_override_merge",
]
