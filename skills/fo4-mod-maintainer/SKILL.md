---
name: fo4-mod-maintainer
description: Safely inspect, plan, patch, merge, download, and stage Fallout 4 mods through the for4-mod-editor JSON CLI. Use for FO4Edit conflict scans, conservative override patches, guarded merge plans, Nexus update/download bookkeeping, Vortex archive handoff, and checkpoint restoration.
---

# Fallout 4 Mod Maintainer

Use the deterministic wrapper in `scripts/fo4_mod_maintainer.py`. Keep its JSON output as the source of truth; summarize only the smallest next action for the user.

## Workflow

1. Run `doctor` and stop on required failed checks.
2. Run `scan` before proposing a fix or merge.
3. Run the matching plan command without write approval.
4. Inspect `blockers`, `warnings`, conflict classes, target files, and backup expectations.
5. Run an apply command only after the user explicitly authorizes that exact reviewed plan.
6. Re-scan after a successful write. Preserve the reported checkpoint path.

Invoke the wrapper from this skill directory:

```powershell
python scripts/fo4_mod_maintainer.py --config C:/path/fo4ap.toml doctor
python scripts/fo4_mod_maintainer.py --config C:/path/fo4ap.toml scan --output C:/work/scan.json
python scripts/fo4_mod_maintainer.py --config C:/path/fo4ap.toml plan-fix --report C:/work/scan.json
```

For an explicitly approved write, include the wrapper's `--approve-write` flag:

```powershell
python scripts/fo4_mod_maintainer.py --config C:/path/fo4ap.toml apply-fix --report C:/work/scan.json --approve-write
```

## Safety boundary

- Treat exit `0` as clean/success, `1` as a completed safe change or available update, `2` as review/approval required, and `3+` as failure.
- Never infer approval from a request to scan, diagnose, plan, sort, or explain.
- Never auto-fix NAVM, VMAD/script properties, precombine/previs data, or ambiguous leveled lists.
- Prefer a new override patch. Do not mutate source mods during conflict fixing.
- Keep source mods enabled for override patches; a merged ESP does not replace their assets.
- Use a disposable Vortex profile and copied save for first validation.
- Keep `NEXUS_API_KEY` in a secret-bearing environment. Never request, print, persist, or pass it as an argument.
- Preserve the manual Nexus authorization click for free downloads; never synthesize NXM authorization or bypass speed limits.
- Do not report success until the post-write scan and xEdit result are clean.

