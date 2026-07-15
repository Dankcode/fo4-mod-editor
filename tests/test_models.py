from __future__ import annotations

from fo4_autopatch.models import (
    ConflictClass,
    ConflictRecord,
    ConflictReport,
    MergeMode,
    MergePlan,
)


def test_conflict_report_round_trip_and_exit_contract() -> None:
    auto = ConflictRecord(
        form_id="00123456",
        signature="REFR",
        edid=None,
        winning_plugin="Example.esp",
        losing_plugins=["Fallout4.esm"],
        fields=["Record Header\\Record Flags"],
        conflict_class=ConflictClass.AUTO_UDR,
    )
    manual = ConflictRecord(
        form_id="00ABCDEF",
        signature="NAVM",
        edid="UnsafeNavmesh",
        winning_plugin="Example.esp",
        losing_plugins=["Fallout4.esm"],
        fields=["NVNM"],
        conflict_class=ConflictClass.MANUAL_NAVMESH,
    )
    report = ConflictReport(["Example.esp"], [auto, manual], applied_fixes=1)

    restored = ConflictReport.from_dict(report.to_dict())

    assert restored.exit_code == 2
    assert len(restored.auto_fixable) == 1
    assert restored.needs_user[0].stable_id == "NAVM:00ABCDEF:Example.esp"


def test_merge_plan_round_trip_uses_origin_qualified_remaps() -> None:
    plan = MergePlan(
        sources=["A.esp", "B.esp"],
        output_name="Patch.esp",
        mode=MergeMode.OVERRIDE_PATCH,
        formid_remaps={"A.esp:WEAP:00000800": "Patch.esp:WEAP:00000800"},
        warnings=["Keep source assets installed"],
    )

    restored = MergePlan.from_dict(plan.to_dict())

    assert restored.ready
    assert restored.mode is MergeMode.OVERRIDE_PATCH
    assert restored.formid_remaps == plan.formid_remaps

