"""Strict parsing for short-lived Nexus Mod Manager download links."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Mapping
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from .errors import NXMURLValidationError

_GAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_KEY_RE = re.compile(r"^[^\s\x00-\x1f]{1,1024}$")


@dataclass(frozen=True, slots=True)
class NXMDownloadLink:
    """Validated NXM authorization kept in memory only.

    ``key`` and ``user_id`` are deliberately excluded from the representation.
    Callers should pass this object straight to :class:`NexusClient`, not log it.
    """

    game: str
    mod_id: int
    file_id: int
    expires: int
    key: str = field(repr=False)
    user_id: str | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return (
            f"NXMDownloadLink(game={self.game!r}, mod_id={self.mod_id}, "
            f"file_id={self.file_id}, expires={self.expires}, authorization='<redacted>')"
        )

    @property
    def authorization_params(self) -> Mapping[str, str]:
        """Return the parameters required by Nexus's v1 download-link route."""

        return {"key": self.key, "expires": str(self.expires)}

    def matches(self, *, game: str, mod_id: int, file_id: int) -> bool:
        return (
            self.game.casefold() == game.casefold()
            and self.mod_id == int(mod_id)
            and self.file_id == int(file_id)
        )

    def redacted_url(self) -> str:
        return (
            f"nxm://{self.game}/mods/{self.mod_id}/files/{self.file_id}"
            f"?key=%3Credacted%3E&expires=%3Credacted%3E"
        )


def _single(query: Mapping[str, list[str]], name: str, *, required: bool) -> str | None:
    values = query.get(name, [])
    if not values:
        if required:
            raise NXMURLValidationError(f"NXM URL is missing required {name!r} authorization")
        return None
    if len(values) != 1 or values[0] == "":
        raise NXMURLValidationError(f"NXM URL contains an invalid {name!r} value")
    return values[0]


def parse_nxm_url(
    value: str,
    *,
    expected_game: str | None = None,
    now: float | None = None,
    require_authorization: bool = True,
) -> NXMDownloadLink:
    """Validate a Nexus ``nxm://`` link and reject expired authorization.

    Free-account authorization consists of ``key`` and ``expires`` values that
    come from the user's manual **Slow Download** click. This parser never
    invents them and does not accept credentials in any other URL component.
    """

    if not isinstance(value, str) or not value:
        raise NXMURLValidationError("NXM URL must be a non-empty string")
    if len(value) > 4096 or any(ord(ch) < 32 for ch in value):
        raise NXMURLValidationError("NXM URL contains unsafe characters")
    try:
        parts = urlsplit(value)
    except ValueError as exc:
        raise NXMURLValidationError("NXM URL could not be parsed") from exc
    if parts.scheme.casefold() != "nxm":
        raise NXMURLValidationError("Expected an nxm:// URL")
    if parts.username or parts.password or parts.port:
        raise NXMURLValidationError("NXM URL must not contain userinfo or a port")

    game = (parts.hostname or "").casefold()
    if not _GAME_RE.fullmatch(game):
        raise NXMURLValidationError("NXM URL contains an invalid game domain")
    if expected_game is not None and game != expected_game.casefold():
        raise NXMURLValidationError(
            f"NXM URL is for {game!r}, expected {expected_game.casefold()!r}"
        )

    match = re.fullmatch(r"/mods/([1-9][0-9]*)/files/([1-9][0-9]*)/?", parts.path, re.IGNORECASE)
    if match is None:
        raise NXMURLValidationError("NXM URL path must be /mods/<mod-id>/files/<file-id>")
    mod_id, file_id = (int(match.group(1)), int(match.group(2)))

    query = parse_qs(parts.query, keep_blank_values=True, strict_parsing=True)
    allowed = {"key", "expires", "user_id"}
    unknown = set(query).difference(allowed)
    if unknown:
        raise NXMURLValidationError(f"NXM URL has unsupported query fields: {', '.join(sorted(unknown))}")
    key = _single(query, "key", required=require_authorization) or ""
    expires_raw = _single(query, "expires", required=require_authorization)
    user_id = _single(query, "user_id", required=False)

    if require_authorization and not _KEY_RE.fullmatch(key):
        raise NXMURLValidationError("NXM URL contains an invalid download key")
    try:
        expires = int(expires_raw or "0")
    except ValueError as exc:
        raise NXMURLValidationError("NXM URL expiry must be a Unix timestamp") from exc
    if require_authorization and expires <= int(time.time() if now is None else now):
        raise NXMURLValidationError("NXM download authorization has expired; click Slow Download again")
    if user_id is not None and not re.fullmatch(r"[0-9]{1,32}", user_id):
        raise NXMURLValidationError("NXM URL contains an invalid user_id")

    return NXMDownloadLink(
        game=game,
        mod_id=mod_id,
        file_id=file_id,
        key=key,
        expires=expires,
        user_id=user_id,
    )


def redact_nxm_url(value: str) -> str:
    """Remove all NXM query values while retaining a useful file identity."""

    try:
        parts = urlsplit(value)
    except (TypeError, ValueError):
        return "<redacted-nxm-url>"
    if parts.scheme.casefold() != "nxm":
        return "<redacted-nxm-url>"
    query = parse_qs(parts.query, keep_blank_values=True)
    redacted = urlencode([(name, "<redacted>") for name in sorted(query)]) if query else ""
    return urlunsplit(("nxm", parts.netloc, parts.path, redacted, ""))
