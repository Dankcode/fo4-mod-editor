from __future__ import annotations

import json
import zipfile
from pathlib import Path

from fo4_autopatch.vortex import BridgeQueue, build_vortex_archive


def test_build_archive_and_submit_bridge_request(tmp_path: Path) -> None:
    plugin = tmp_path / "Patch.esp"
    plugin.write_bytes(b"TES4 fixture")

    archive = build_vortex_archive([plugin], tmp_path / "packages", mod_name="Patch", version="1.0")
    queue = BridgeQueue(tmp_path / "bridge")
    request = queue.submit_archive(archive.path, install=True, deploy=False)

    with zipfile.ZipFile(archive.path) as bundle:
        assert "Patch.esp" in bundle.namelist()
        manifest = json.loads(bundle.read("fomod/fo4ap-manifest.json"))
        assert manifest["name"] == "Patch"
    assert queue.pending()[0].request_id == request.request_id
    assert "key" not in (queue.requests_dir / f"{request.request_id}.json").read_text(encoding="utf-8")

