importScripts("providers/chatgpt.js", "providers/deepseek.js");

const LOAD_SETTLE_MS = 4000;
const MAX_RECORD_STEPS = 2000;

const PROVIDERS = self.YaloNoteProviders ?? [];

let activeRun = null;

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function timestampForPath() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function setBadge(tabId, text, color = "#2563eb") {
  await chrome.action.setBadgeBackgroundColor({ tabId, color });
  await chrome.action.setBadgeText({ tabId, text });
}
function resolveProvider(url) {
  return PROVIDERS.find((provider) => provider.matchesUrl(url)) ?? null;
}

async function inspectRecordPage(tabId, provider, moveTo) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    args: [moveTo],
    func: provider.inspectPage,
  });
  return result;
}

async function captureArtifact(tabId, provider, artifactKey) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    args: [artifactKey],
    func: provider.captureArtifact,
  });
  return result;
}

async function loadArchiveRoot() {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open("yalo-note-it", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("settings");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction("settings", "readonly");
      const request = transaction.objectStore("settings").get("archive-root");
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(request.error);
    });
  } finally {
    database.close();
  }
}

function safeFileName(displayName, fallback = "attachment") {
  const cleaned = String(displayName ?? "")
    .replace(/[<>:"/\\|?*\u0000-\u001f]/gu, "_")
    .replace(/[. ]+$/gu, "")
    .trim();
  return cleaned && cleaned !== "." && cleaned !== ".." ? cleaned : fallback;
}

function base64ToBytes(base64) {
  const binary = atob(base64);
  return Uint8Array.from(binary, (character) => character.charCodeAt(0));
}

async function sha256(bytes) {
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function writeFile(directory, name, contents) {
  const file = await directory.getFileHandle(name, { create: true });
  const writable = await file.createWritable();
  try {
    await writable.write(contents);
  } finally {
    await writable.close();
  }
}

async function writeWebArchive(providerId, conversationId, body, artifacts, sourceUrl) {
  const root = await loadArchiveRoot();
  if (!root) throw new Error("请先在插件中录入一个明确的本地路径。" );
  if ((await root.queryPermission({ mode: "readwrite" })) !== "granted") {
    throw new Error("记录目录权限已失效，请在插件中重新设置目录。" );
  }
  let directory = root;
  for (const name of ["sessions", providerId, conversationId]) {
    directory = await directory.getDirectoryHandle(name, { create: true });
  }
  await writeFile(directory, "conversation.raw-record.md", body);
  const assetsDirectory = await directory.getDirectoryHandle("assets", { create: true });
  const usedNames = new Set();
  const manifestArtifacts = [];
  for (const artifact of artifacts.values()) {
    const entry = {
      key: artifact.key,
      messageId: artifact.messageId,
      displayName: artifact.displayName,
      status: artifact.status,
      capturedAt: artifact.capturedAt,
    };
    if (artifact.sourceUrl) entry.sourceUrl = artifact.sourceUrl;
    if (artifact.error) entry.error = artifact.error;
    if (artifact.status === "saved" && artifact.base64) {
      const bytes = base64ToBytes(artifact.base64);
      const baseName = safeFileName(artifact.displayName);
      let localName = baseName;
      let suffix = 2;
      while (usedNames.has(localName.toLocaleLowerCase())) {
        const dot = baseName.lastIndexOf(".");
        localName = dot > 0
          ? `${baseName.slice(0, dot)}-${suffix}${baseName.slice(dot)}`
          : `${baseName}-${suffix}`;
        suffix += 1;
      }
      usedNames.add(localName.toLocaleLowerCase());
      await writeFile(assetsDirectory, localName, bytes);
      entry.localPath = `assets/${localName}`;
      entry.mimeType = artifact.mimeType;
      entry.size = bytes.byteLength;
      entry.sha256 = await sha256(bytes);
    } else if (Number.isFinite(artifact.size)) {
      entry.size = artifact.size;
    }
    manifestArtifacts.push(entry);
  }
  const manifest = {
    schemaVersion: 1,
    provider: providerId,
    conversationId,
    sourceUrl,
    exportedAt: new Date().toISOString(),
    artifacts: manifestArtifacts,
  };
  await writeFile(directory, "manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
}

async function runRecordExport(tab, provider, cutoffId = null) {
  const run = { tabId: tab.id, stopped: false, startedAt: new Date().toISOString() };
  activeRun = run;
  const records = new Map();
  const artifacts = new Map();
  try {
    await setBadge(tab.id, "REC", "#7c3aed");
    let state = await inspectRecordPage(tab.id, provider, "top");
    if (state?.error) throw new Error(state.error);
    let stableTop = 0;
    let topSignature = "";
    for (let step = 0; step < 20 && !run.stopped; step += 1) {
      await sleep(LOAD_SETTLE_MS);
      state = await inspectRecordPage(tab.id, provider, "top");
      if (state?.error) throw new Error(state.error);
      const signature = `${state.scrollHeight}|${state.records[0]?.id ?? ""}`;
      stableTop = signature === topSignature ? stableTop + 1 : 0;
      topSignature = signature;
      if (stableTop >= 2) break;
    }
    if (stableTop < 2) throw new Error("达到等待上限前仍未确认稳定的对话顶部。");
    state = await inspectRecordPage(tab.id, provider, "none");
    let stableBottom = 0;
    for (let step = 0; step < MAX_RECORD_STEPS && !run.stopped; step += 1) {
      for (const record of state.records) records.set(record.id, record);
      if (provider.captureArtifact) {
        for (const candidate of state.artifacts ?? []) {
          if (artifacts.has(candidate.key)) continue;
          const captured = await captureArtifact(tab.id, provider, candidate.key);
          artifacts.set(candidate.key, {
            ...candidate,
            ...captured,
            capturedAt: new Date().toISOString(),
          });
        }
      }
      stableBottom = state.atBottom ? stableBottom + 1 : 0;
      if (stableBottom >= 3) break;
      state = await inspectRecordPage(tab.id, provider, "next");
      if (state?.error) throw new Error(state.error);
      await sleep(120);
      state = await inspectRecordPage(tab.id, provider, "none");
      await setBadge(tab.id, String(records.size), "#7c3aed");
    }
    let ordered = [...records.values()].sort((left, right) => {
      const leftNumber = Number(left.id.match(/(\d+)$/u)?.[1] ?? 0);
      const rightNumber = Number(right.id.match(/(\d+)$/u)?.[1] ?? 0);
      return leftNumber - rightNumber;
    });
    if (cutoffId) {
      const cutoffIndex = ordered.findIndex((record) => record.id === cutoffId);
      if (cutoffIndex < 0) throw new Error("没有在完整扫描中找到触发消息，未生成不完整记录。");
      ordered = ordered.slice(0, cutoffIndex + 1);
    }
    const conversationId = provider.conversationId(state.url) ?? timestampForPath();
    const lines = [
      `# ${provider.label} Conversation Raw Record`,
      "",
      `> Source: ${state.url}`,
      `> Page title: ${state.title}`,
      `> Exported at: ${new Date().toISOString()}`,
      `> Message count: ${ordered.length}`,
      `> Cutoff: ${cutoffId ?? "manual-current-page"}`,
      "> This file preserves the conversation order and rendered message content. It is not a summary.",
      "",
    ];
    for (const [index, record] of ordered.entries()) {
      const role = record.role === "user" ? "User" : record.role === "assistant" ? "Assistant" : record.role;
      lines.push(`## ${String(index + 1).padStart(3, "0")} · ${role}`, "", `<!-- ${record.id} -->`);
      for (const attachment of record.attachments) lines.push(`- [附件：${attachment}]`);
      if (record.attachments.length) lines.push("");
      lines.push(record.markdown || "[无法辨认]", "");
    }
    const body = lines.join("\n");
    await writeWebArchive(provider.id, conversationId, body, artifacts, state.url);
    await setBadge(tab.id, ordered.length ? "OK" : "ERR", ordered.length ? "#16a34a" : "#dc2626");
    const savedArtifacts = [...artifacts.values()].filter((artifact) => artifact.status === "saved").length;
    const failedArtifacts = artifacts.size - savedArtifacts;
    const artifactSummary = artifacts.size
      ? `，保存 ${savedArtifacts} 个文件${failedArtifacts ? `，${failedArtifacts} 个不可用` : ""}`
      : "";
    return { ok: true, message: `已记录 ${ordered.length} 条消息${artifactSummary}。` };
  } catch (error) {
    console.error("Raw Record export failed", error);
    await setBadge(tab.id, "ERR", "#dc2626");
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

async function handleRecordControl(message) {
  if (message.mode === "stop") {
    if (!activeRun) return { ok: false, message: "当前没有正在执行的任务。" };
    activeRun.stopped = true;
    await setBadge(activeRun.tabId, "…", "#ca8a04");
    return { ok: true, message: "正在停止。" };
  }

  if (message.mode !== "record") return { ok: false, message: "不支持的操作。" };
  if (activeRun) return { ok: false, message: "已有记录任务正在执行。" };

  const tab = await chrome.tabs.get(message.tabId);
  const provider = resolveProvider(tab.url);
  if (!provider) {
    await setBadge(tab.id, "NO", "#dc2626");
    return { ok: false, message: "当前网页尚未配置 Yalo note it 页面适配器。" };
  }

  return runRecordExport(tab, provider);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "record-control") return false;
  handleRecordControl(message)
    .then(sendResponse)
    .catch((error) => sendResponse({
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    }));
  return true;
});
