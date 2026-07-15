from __future__ import annotations

from fo4_autopatch.conflicts.fixer import classify
from fo4_autopatch.models import ConflictClass


def test_sensitive_records_are_never_auto_fixed() -> None:
    navmesh, _ = classify({"signature": "NAVM", "form_id": "00000001", "evidence_complete": True})
    scripted, _ = classify({"signature": "QUST", "form_id": "00000002", "has_vmad": True})
    precombine, _ = classify({"signature": "REFR", "form_id": "00000003", "has_precombine": True})

    assert navmesh is ConflictClass.MANUAL_NAVMESH
    assert scripted is ConflictClass.MANUAL_SCRIPTED
    assert precombine is ConflictClass.MANUAL_PRECOMBINE


def test_forwarding_requires_complete_winner_evidence() -> None:
    incomplete, _ = classify({"signature": "WEAP", "winning_plugin": "A.esp"})
    complete, _ = classify(
        {
            "signature": "WEAP",
            "winning_plugin": "A.esp",
            "evidence_complete": True,
            "source_values_complete": True,
            "record_forward_safe": True,
            "forward_plugin": "A.esp",
            "field_sources": {"DNAM": "A.esp"},
        }
    )

    assert incomplete is ConflictClass.MANUAL_OTHER
    assert complete is ConflictClass.AUTO_FORWARDABLE

