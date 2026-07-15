# Architecture

## Process boundary

The Python executable owns planning, JSON contracts, backups, Nexus REST calls, resumable transfers, reporting, and archive construction. It does not parse or write Bethesda plugins itself. xEdit owns record comparison and plugin writes through generated Pascal scripts launched in isolated processes.

```text
coding CLI / fo4-mod-maintainer skill
                 |
                 v
          fo4ap JSON commands
          /       |        \
         v        v         v
     xEdit     Nexus API   Vortex bridge queue
   Pascal IPC   + NXM       + tiny extension
         |        |         |
         v        v         v
   ESP/ESM/ESL  archives   import/install/deploy
```

The generated-script contract is one JSON input and one JSON output per xEdit session. Operations are batched because load-order initialization dominates runtime. Every mutating session receives a backup checkpoint and is followed by a read-only rescan.

## Conflict policy

Automation is allowlist-based. ITM removal uses xEdit Quick Auto Clean where possible. Deleted references may be undeleted and disabled only for supported reference signatures. Redundant overrides and disjoint field forwarding require evidence in the scan payload. VMAD/scripted records, NAVM, precombine/previs-sensitive records, and ambiguous leveled lists remain manual.

## Merge modes

`override-patch` copies winning overrides into a new patch while retaining source plugins as masters. This is the default because it preserves FormIDs and is reversible.

`full` attempts to replace source plugins. It is experimental and is blocked when external dependents, missing masters, sensitive record types, or unresolved conflicts are present. Source plugins must remain available during validation, and the result must be tested on a disposable Vortex profile.

## Vortex integration

Reliable Vortex import and deployment require Vortex's in-process extension API. The optional extension contains no conflict or download policy; it consumes local JSON requests produced by Python and emits Vortex's `import-downloads`, `start-install-download`, and optionally `deploy-mods` events. The fallback is a ZIP for Vortex's **Install From File** command.

## Source compatibility

The repository records the tested upstream commit hashes in `UPSTREAM.md`. Upstream trees belong under ignored `upstream/` and are not redistributed.

