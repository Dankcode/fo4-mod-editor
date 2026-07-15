from __future__ import annotations

import pytest

from fo4_autopatch.xedit.pas_templates import PascalTemplateError, render_batch_patch, render_conflict_export


def test_conflict_template_escapes_output_path() -> None:
    rendered = render_conflict_export("C:/work/user's scan.json", ["A.esp"])

    assert "user''s scan.json" in rendered
    assert "A.esp" in rendered
    assert "schema_version" in rendered


def test_batch_patch_rejects_unknown_operations() -> None:
    with pytest.raises(PascalTemplateError):
        render_batch_patch("C:/out.json", "Patch.esp", [{"action": "delete_everything"}])


def test_batch_patch_converts_load_order_form_id_for_source_file() -> None:
    rendered = render_batch_patch(
        "C:/out.json",
        "Patch.esp",
        [{"action": "forward_record", "form_id": "01001234", "source_plugin": "A.esp"}],
    )

    assert "LoadOrderFormIDtoFileFormID" in rendered

