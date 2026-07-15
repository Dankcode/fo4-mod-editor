# Vortex bridge installation

Copy this folder to `%APPDATA%\@vortex\main\plugins\for4-mod-editor` and restart Vortex. Keep Fallout 4 and the intended profile active when submitting a bridge request.

The extension watches `%LOCALAPPDATA%\for4-mod-editor\vortex-bridge\requests` (or `FO4AP_BRIDGE_DIR`), imports the generated ZIP through Vortex, installs it, and deploys only when the reviewed Python request explicitly asks for deployment. It does not read Vortex credentials or modify Vortex state files.

