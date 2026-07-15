# Upstream implementation notes

This project keeps its architecture decisions beside the code so later coding agents do not have to rediscover them.

## xEdit / FO4Edit

- xEdit is the shared executable; Fallout 4 mode is selected by the `FO4Edit` executable name or the `-FO4` switch.
- The supported extension surface is JvInterpreter Pascal with `Initialize`, `Process`, and `Finalize`; there is no documented embedded Python plugin interface.
- `-script`, `-autoload`, and `-autoexit` are supported command-line modes. Python passes arguments as a list and exchanges versioned JSON files with generated `.fo4pas` scripts.
- `wbCopyElementToFile` can create overrides in a new patch, but that patch retains source plugins as masters. It is not a safe general replacement for all source ESPs.
- xEdit describes automatic Merged Patch behavior as obsolete/unsupported. This project therefore defaults to an override patch and treats source-plugin elimination as experimental.
- Conflict status is evidence, not intent. VMAD, NAVM, CELL/WRLD, precombine/previs, new FormIDs, and external assets require review.

Primary references: [TES5Edit source](https://github.com/TES5Edit/TES5Edit), [scripting functions](https://tes5edit.github.io/docs/13-Scripting-Functions.html), [conflict detection](https://tes5edit.github.io/docs/5-conflict-detection-and-resolution.html), and [managing mod files](https://tes5edit.github.io/docs/8-managing-mod-files.html).

## Vortex

- Vortex extensions load JavaScript. The main implementation remains Python, with a deliberately thin TypeScript bridge for the unavoidable in-process integration.
- Current extensions import `@nexusmods/vortex-api`; the previous standalone `vortex-api` repository is archived.
- The bridge uses public events such as `import-downloads`, `start-install-download`, and `deploy-mods`. It never edits Redux/LevelDB state, copies directly into `Data`, or extracts Vortex credentials.
- Python and Vortex communicate through local, versioned JSON request/result files. Executables are launched with `shell: false`; secrets never appear in arguments.

Primary references: [Vortex source](https://github.com/Nexus-Mods/Vortex), [API migration](https://github.com/Nexus-Mods/vortex-api/blob/master/docs/MIGRATION.md), and [extension events](https://github.com/Nexus-Mods/vortex-api/blob/master/docs/EVENTS.md).

## Nexus Mods

- The preferred path is Vortex's own authenticated download flow. The standalone REST client is a personal/testing fallback and reads only `NEXUS_API_KEY` from the environment.
- Free accounts must obtain `key` and `expires` through a user-initiated Nexus download action. The project never automates the website click or fabricates those values.
- Slow downloads are sequential, resumable, and retried. Parallelism is not used to evade a speed cap.
- Rate-limit headers are tracked. Work pauses on exhaustion or HTTP 429 instead of retrying aggressively.
- A public release using personal API keys must be replaced with a registered Nexus application/SSO flow.

Primary references: [API Acceptable Use Policy](https://help.nexusmods.com/article/114-api-acceptable-use-policy), [rate limits](https://help.nexusmods.com/article/105-i-have-reached-a-daily-or-hourly-limit-api-requests-have-been-consumed-rate-limit-exceeded-what-does-this-mean), [download speed policy](https://help.nexusmods.com/article/96-download-speed-caps-adblockers-and-different-types-of-membership), and [Terms of Service](https://help.nexusmods.com/article/18-terms-of-service).

