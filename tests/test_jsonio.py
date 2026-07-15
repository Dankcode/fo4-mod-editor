from __future__ import annotations

from fo4_autopatch.jsonio import dumps, redact_url


def test_redact_url_removes_nxm_authorization_query() -> None:
    raw = "nxm://fallout4/mods/1/files/2?key=temporary&expires=123&user_id=4"

    redacted = redact_url(raw)

    assert "temporary" not in redacted
    assert "123" not in redacted
    assert "user_id" not in redacted
    assert "<redacted>" in redacted


def test_dumps_redacts_nested_credentials() -> None:
    rendered = dumps({"safe": 1, "authorization": "Bearer secret", "child": {"token": "abc"}})

    assert "secret" not in rendered
    assert '"abc"' not in rendered
    assert rendered.count("<redacted>") == 2

