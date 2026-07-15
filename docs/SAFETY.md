# Safety model

- Use a disposable Vortex profile and a copied save for first runs.
- Close xEdit before invoking `fo4ap`; concurrent access to the same Data directory is refused.
- Keep Vortex closed during direct Data-file writes, or use a non-deployed test Data directory.
- Review dry-run JSON before any `--apply`; automation never implies that a conflict resolution is semantically correct for a particular mod list.
- Keep source mods enabled after an override-patch merge. For a full merge, disable sources only after xEdit error checks and in-game validation.
- Do not auto-fix NAVM, VMAD/script properties, precombine/previs data, or complex leveled lists.
- Do not restore a checkpoint over a different profile without reviewing its manifest paths.
- Never place a Nexus API key in config, command-line arguments, logs, reports, issues, or commits. Use `NEXUS_API_KEY` from a secret-bearing environment.
- Free Nexus downloads require a user-generated NXM URL. Do not automate the website click, synthesize its key/expiry, evade the speed cap, or run concurrent transfers to defeat throttling.
- Treat downloaded mod archives as untrusted. Vortex performs installation; this tool only verifies transport metadata and packages files supplied by the user.

