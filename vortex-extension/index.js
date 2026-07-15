"use strict";

// Vortex cannot load Python directly. This intentionally tiny JavaScript
// entrypoint consumes local JSON requests produced by the Python engine and
// delegates every state-changing action to Vortex's public event API.

const fs = require("fs");
const os = require("os");
const path = require("path");

const SCHEMA_VERSION = 1;
const REQUEST_ID = /^[a-f0-9]{32}$/;
let processing = false;

function bridgeRoot() {
  const configured = process.env.FO4AP_BRIDGE_DIR;
  if (configured && path.isAbsolute(configured)) return path.resolve(configured);
  const local = process.env.LOCALAPPDATA || path.join(os.homedir(), "AppData", "Local");
  return path.join(local, "for4-mod-editor", "vortex-bridge");
}

function readRequest(filePath) {
  const raw = JSON.parse(fs.readFileSync(filePath, "utf8"));
  if (
    raw.schema_version !== SCHEMA_VERSION ||
    raw.kind !== "vortex_bridge_request" ||
    raw.action !== "import_archive" ||
    !REQUEST_ID.test(raw.id)
  ) {
    throw new Error("Unsupported or malformed for4 bridge request");
  }
  const archive = path.resolve(String(raw.archive_path || ""));
  if (!path.isAbsolute(archive) || path.extname(archive).toLowerCase() !== ".zip") {
    throw new Error("Bridge archive must be an absolute ZIP path");
  }
  const stat = fs.lstatSync(archive);
  if (!stat.isFile() || stat.isSymbolicLink()) throw new Error("Bridge archive is missing or unsafe");
  if (raw.deploy && !raw.install) throw new Error("Deployment requires installation");
  return { ...raw, archive_path: archive };
}

function importDownloads(api, archivePath) {
  return new Promise((resolve, reject) => {
    api.events.emit("import-downloads", [archivePath], (downloadIds) => {
      if (!Array.isArray(downloadIds) || downloadIds.length === 0) {
        reject(new Error("Vortex did not return an imported download ID"));
      } else {
        resolve(downloadIds);
      }
    });
  });
}

function installDownload(api, downloadId) {
  return new Promise((resolve, reject) => {
    api.events.emit("start-install-download", downloadId, true, (error, modId) => {
      if (error) reject(error);
      else resolve(modId || null);
    });
  });
}

function deployMods(api) {
  return new Promise((resolve, reject) => {
    api.events.emit("deploy-mods", (error) => (error ? reject(error) : resolve()));
  });
}

function writeResult(root, request, values) {
  const results = path.join(root, "results");
  fs.mkdirSync(results, { recursive: true });
  const target = path.join(results, `${request.id}.json`);
  const temporary = `${target}.${process.pid}.tmp`;
  const payload = {
    schema_version: SCHEMA_VERSION,
    kind: "vortex_bridge_result",
    id: request.id,
    completed_at: new Date().toISOString(),
    ...values,
  };
  fs.writeFileSync(temporary, `${JSON.stringify(payload, null, 2)}\n`, { encoding: "utf8", flag: "wx" });
  fs.renameSync(temporary, target);
}

async function processOne(api, root, filePath) {
  let request;
  try {
    request = readRequest(filePath);
    const state = api.getState();
    const activeGame = state && state.session && state.session.gameMode
      ? state.session.gameMode.activeGameId
      : undefined;
    if (activeGame && activeGame !== request.game_id) {
      throw new Error(`Activate the ${request.game_id} game/profile before processing this request`);
    }
    const downloadIds = await importDownloads(api, request.archive_path);
    const modIds = [];
    if (request.install) {
      for (const downloadId of downloadIds) modIds.push(await installDownload(api, downloadId));
    }
    if (request.deploy) await deployMods(api);
    writeResult(root, request, { ok: true, download_ids: downloadIds, mod_ids: modIds, deployed: !!request.deploy });
    api.sendNotification({
      type: "success",
      title: "for4-mod-editor",
      message: request.deploy ? "Patch installed and deployed" : "Patch imported into Vortex",
    });
  } catch (error) {
    const safeMessage = error instanceof Error ? error.message : String(error);
    if (request && REQUEST_ID.test(request.id)) {
      writeResult(root, request, { ok: false, error: safeMessage.slice(0, 500) });
    }
    api.showErrorNotification("for4-mod-editor bridge failed", safeMessage, { allowReport: false });
  } finally {
    try { fs.unlinkSync(filePath); } catch (_) { /* already consumed */ }
  }
}

async function poll(api) {
  if (processing) return;
  processing = true;
  try {
    const root = bridgeRoot();
    const requests = path.join(root, "requests");
    fs.mkdirSync(requests, { recursive: true });
    const names = fs.readdirSync(requests)
      .filter((name) => REQUEST_ID.test(path.basename(name, ".json")) && name.endsWith(".json"))
      .sort();
    if (names.length) await processOne(api, root, path.join(requests, names[0]));
  } finally {
    processing = false;
  }
}

function init(context) {
  context.once(() => {
    poll(context.api);
    setInterval(() => poll(context.api), 2000);
  });
  return true;
}

exports.default = init;

