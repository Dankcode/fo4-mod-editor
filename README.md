# for4-mod-editor

`for4-mod-editor` is a Python companion executable for Fallout 4 mod maintenance. It drives xEdit/FO4Edit through its supported command-line and Pascal-script surfaces, produces compact JSON for coding agents, keeps rollback checkpoints before writes, and hands generated archives to Vortex through an optional bridge extension.

It does **not** reimplement the ESP format, bypass Nexus Mods download controls, silently guess at dangerous conflicts, or write an API key to disk.

## What works

- Inspect configuration and external tools with `fo4ap doctor`.
- Scan an active load order through xEdit and emit JSON plus an actionable Markdown report.
- Classify conservative auto-fix candidates separately from VMAD, navmesh, precombine/previs, and complex leveled-list conflicts.
- Dry-run every fix and merge plan by default; checkpoint changed plugins before `--apply`.
- Run xEdit Quick Auto Clean for an explicitly named plugin.
- Build a guarded override patch or an experimental full merge plan.
- Check Nexus metadata and updates while tracking published hourly/daily rate limits.
- Resume a Nexus download after a dropped slow connection. Free accounts still require the manual Nexus download click that supplies an NXM key and expiry.
- Package patched ESPs as a normal Vortex-installable ZIP and request import/install through the optional Vortex bridge.
- Provide a repository-contained `fo4-mod-maintainer` agent skill for low-token JSON workflows.

## Important safety boundary

FO4 plugin merging is not equivalent to copying files into one archive. FormID remapping, references, NAVM, VMAD, precombines, archives, and plugins that are masters of other plugins can make a merge unsafe. `fo4ap merge` therefore creates a validated plan first. Full source-mod replacement is disabled unless the config enables the experimental path and the command includes its explicit acknowledgement. Keep the source mods enabled until the merged result has been tested on a disposable profile and save.

## Install

Requires Windows, Python 3.11+, Fallout 4, xEdit/FO4Edit, and Vortex.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[notifications]"
Copy-Item fo4ap.example.toml fo4ap.toml
```

To build the standalone Windows executable, run `scripts\build_exe.ps1`. The
same build is available as a downloadable artifact from the repository's
**Build Windows executable** GitHub Actions workflow.

Set a newly generated Nexus API key only for the current shell or through your secret manager:

```powershell
$env:NEXUS_API_KEY = "your-rotated-key"
```

Never commit `.env`, `fo4ap.toml`, Vortex state, downloaded mods, or backup data. A key pasted into chat or an issue should be revoked before use.

## Typical workflow

```powershell
fo4ap --config fo4ap.toml doctor --json
fo4ap --config fo4ap.toml scan --json --output .fo4ap\latest-scan.json
fo4ap --config fo4ap.toml fix --report .fo4ap\latest-scan.json --json
fo4ap --config fo4ap.toml fix --report .fo4ap\latest-scan.json --apply --yes --json
fo4ap --config fo4ap.toml merge --out For4MergedPatch.esp A.esp B.esp --json
fo4ap --config fo4ap.toml install --name For4MergedPatch --version 1.0 Data\For4MergedPatch.esp --json
```

Exit codes are designed for another coding CLI: `0` clean/success, `1` safe changes applied or updates found, `2` manual work/approval required, and `3+` operational/configuration failure. Standard output is JSON when `--json` is used; diagnostics go to standard error.

## Nexus and Vortex

Premium accounts can request direct API download URLs. Free accounts can use `fo4ap queue-download` to persist a queue, then click **Slow Download** on Nexus; pass the resulting `nxm://` URL to `fo4ap download --nxm-url ...`. The downloader resumes and retries at the speed Nexus grants. It never fabricates `key`/`expires`, automates website clicks, or circumvents caps.

`fo4ap install` creates a Vortex-compatible archive. With the bridge extension installed, Vortex imports and installs that archive using its own event API. Without the extension, the command leaves the archive in a clearly reported location for Vortex's **Install From File** action. Deployment remains an explicit Vortex action unless the bridge request was approved with `--deploy`.

## Upstream development references

The implementation is verified against [TES5Edit/TES5Edit](https://github.com/TES5Edit/TES5Edit) and [Nexus-Mods/Vortex](https://github.com/Nexus-Mods/Vortex). Their source trees are not vendored into this repository. Clone them under `upstream/` when auditing compatibility.

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), [docs/SAFETY.md](docs/SAFETY.md), and [docs/UPSTREAM-NOTES.md](docs/UPSTREAM-NOTES.md) for the system boundary, validation checklist, and source-backed compatibility decisions.
