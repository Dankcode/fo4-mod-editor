from __future__ import annotations

import time

import pytest

from fo4_autopatch.models import InstalledMod, ModFileInfo
from fo4_autopatch.nexus import NexusClient, compare_updates, parse_nxm_url, redact_nxm_url


def test_nxm_authorization_is_validated_and_redacted() -> None:
    future = int(time.time()) + 3600
    raw = f"nxm://fallout4/mods/10/files/20?key=temporary&expires={future}&user_id=30"

    link = parse_nxm_url(raw, expected_game="fallout4")

    assert link.matches(game="fallout4", mod_id=10, file_id=20)
    assert "temporary" not in repr(link)
    assert "temporary" not in redact_nxm_url(raw)


def test_nexus_client_repr_and_errors_do_not_expose_key() -> None:
    client = NexusClient("top-secret", session=object())

    assert "top-secret" not in repr(client)
    assert "<redacted>" in repr(client)


def test_update_comparison_uses_file_ids() -> None:
    installed = InstalledMod("x", "Example", 10, 20, "1.0")
    latest = ModFileInfo(10, 21, "Main", "main.zip", "1.1", "MAIN", 10)

    decisions = compare_updates([installed], {10: [latest]})

    assert decisions[0].update_available
    assert decisions[0].latest is latest
