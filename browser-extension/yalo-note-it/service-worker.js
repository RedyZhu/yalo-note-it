importScripts("providers/chatgpt.js", "providers/deepseek.js");

const LOAD_SETTLE_MS = 4000;
const MAX_RECORD_STEPS = 2000;
const MAX_STALLED_SCROLL_STEPS = 12;

const PROVIDERS = self.YaloNoteProviders ?? [];
const RECORD_ERROR_PREFIX = "record-error:";

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

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function scanProgressSignature(state) {
  const scrollTop = Number.isFinite(state?.scrollTop) ? Math.round(state.scrollTop) : "unknown";
  const scrollHeight = Number.isFinite(state?.scrollHeight) ? Math.round(state.scrollHeight) : "unknown";
  const recordIds = (state?.records ?? []).map((record) => record.id).join("\u001f");
  return `${scrollTop}|${scrollHeight}|${recordIds}`;
}

function recordErrorKey(tabId) {
  if (!Number.isInteger(tabId) || tabId <= 0) throw new Error("无效的标签页 ID。");
  return `${RECORD_ERROR_PREFIX}${tabId}`;
}

async function readTabError(tabId) {
  const key = recordErrorKey(tabId);
  const values = await chrome.storage.session.get(key);
  const value = values[key];
  if (value?.schemaVersion !== 1 || typeof value.message !== "string" || !value.message) {
    return null;
  }
  return value;
}

async function rememberTabError(tabId, message, pageUrl = null) {
  try {
    const key = recordErrorKey(tabId);
    await chrome.storage.session.set({
      [key]: {
        schemaVersion: 1,
        message: String(message).slice(0, 2000),
        occurredAt: new Date().toISOString(),
        ...(typeof pageUrl === "string" && pageUrl ? { pageUrl } : {}),
      },
    });
  } catch (error) {
    console.error("Failed to persist Yalo note it error", error);
  }
}

async function clearTabError(tabId) {
  try {
    await chrome.storage.session.remove(recordErrorKey(tabId));
  } catch (error) {
    console.error("Failed to clear Yalo note it error", error);
  }
}

function resolveProvider(url) {
  return PROVIDERS.find((provider) => provider.matchesUrl(url)) ?? null;
}

function recordContextIdentity(url) {
  if (typeof url !== "string" || !url) return null;
  const provider = resolveProvider(url);
  const conversationId = provider?.conversationId(url);
  if (provider && conversationId) return `${provider.id}:${conversationId}`;
  try {
    const parsed = new URL(url);
    return `page:${parsed.origin}${parsed.pathname}`;
  } catch {
    return null;
  }
}

function isSameRecordContext(leftUrl, rightUrl) {
  const leftIdentity = recordContextIdentity(leftUrl);
  return Boolean(leftIdentity && leftIdentity === recordContextIdentity(rightUrl));
}

async function isTabInRecordContext(tabId, pageUrl) {
  if (!pageUrl) return true;
  try {
    const tab = await chrome.tabs.get(tabId);
    return isSameRecordContext(pageUrl, tab.url);
  } catch {
    return false;
  }
}

async function clearTabState(tabId) {
  await clearTabError(tabId);
  try {
    await chrome.action.setBadgeText({ tabId, text: "" });
  } catch (error) {
    console.error("Failed to clear Yalo note it badge", error);
  }
}

function detachedResult(message) {
  return { ok: false, detached: true, message };
}

async function finishWithError(tabId, message, badge = "ERR", pageUrl = null) {
  if (!(await isTabInRecordContext(tabId, pageUrl))) return detachedResult(message);
  await rememberTabError(tabId, message, pageUrl);
  if (!(await isTabInRecordContext(tabId, pageUrl))) {
    await clearTabState(tabId);
    return detachedResult(message);
  }
  try {
    await setBadge(tabId, badge, "#dc2626");
  } catch (error) {
    console.error("Failed to set Yalo note it error badge", error);
  }
  if (!(await isTabInRecordContext(tabId, pageUrl))) {
    await clearTabState(tabId);
    return detachedResult(message);
  }
  return { ok: false, message };
}

async function finishCancelledRun(run) {
  await clearTabState(run.tabId);
  return {
    ok: false,
    cancelled: true,
    message: run.cancelReason === "navigation"
      ? "页面已切换，原记录任务已安全停止，未生成档案。"
      : "记录任务已停止，未生成或修改档案。",
  };
}

async function refreshRunAttachment(run) {
  if (run.cancelReason || run.detached) return;
  if (await isTabInRecordContext(run.tabId, run.sourceUrl)) return;
  if (run.phase === "writing") run.detached = true;
  else run.cancelReason = "navigation";
}

async function finishRunWithError(run, message) {
  await refreshRunAttachment(run);
  if (run.cancelReason) return await finishCancelledRun(run);
  if (run.detached) return detachedResult(message);
  await rememberTabError(run.tabId, message, run.sourceUrl);
  await refreshRunAttachment(run);
  if (run.cancelReason || run.detached) {
    await clearTabState(run.tabId);
    return run.cancelReason ? await finishCancelledRun(run) : detachedResult(message);
  }
  try {
    await setBadge(run.tabId, "ERR", "#dc2626");
  } catch (error) {
    console.error("Failed to set Yalo note it error badge", error);
  }
  await refreshRunAttachment(run);
  if (run.cancelReason || run.detached) {
    await clearTabState(run.tabId);
    return run.cancelReason ? await finishCancelledRun(run) : detachedResult(message);
  }
  return { ok: false, message };
}

async function finishRunWithSuccess(run) {
  await refreshRunAttachment(run);
  if (run.detached) return;
  await clearTabError(run.tabId);
  await refreshRunAttachment(run);
  if (run.detached) {
    await clearTabState(run.tabId);
    return;
  }
  await setBadge(run.tabId, "OK", "#16a34a");
  await refreshRunAttachment(run);
  if (run.detached) await clearTabState(run.tabId);
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
  const run = {
    tabId: tab.id,
    sourceUrl: tab.url,
    phase: "scanning",
    cancelReason: null,
    detached: false,
    startedAt: new Date().toISOString(),
  };
  activeRun = run;
  const records = new Map();
  const artifacts = new Map();
  try {
    await clearTabError(tab.id);
    await setBadge(tab.id, "REC", "#7c3aed");
    let state = await inspectRecordPage(tab.id, provider, "top");
    if (state?.error) throw new Error(state.error);
    let stableTop = 0;
    let topSignature = "";
    for (let step = 0; step < 20 && !run.cancelReason; step += 1) {
      await sleep(LOAD_SETTLE_MS);
      if (run.cancelReason) return await finishCancelledRun(run);
      state = await inspectRecordPage(tab.id, provider, "top");
      if (state?.error) throw new Error(state.error);
      const signature = `${state.scrollHeight}|${state.records[0]?.id ?? ""}`;
      stableTop = state.atTop && signature === topSignature ? stableTop + 1 : 0;
      topSignature = signature;
      if (stableTop >= 2) break;
    }
    if (run.cancelReason) return await finishCancelledRun(run);
    if (stableTop < 2) throw new Error("达到等待上限前仍未确认稳定的对话顶部。");
    state = await inspectRecordPage(tab.id, provider, "none");
    if (!state.atTop) {
      throw new Error("对话在顶部稳定检查后发生回退，已停止以避免生成不完整档案。");
    }
    let stableBottom = 0;
    let stalledScrollSteps = 0;
    let progressSignature = scanProgressSignature(state);
    for (let step = 0; step < MAX_RECORD_STEPS && !run.cancelReason; step += 1) {
      for (const record of state.records) records.set(record.id, record);
      if (provider.captureArtifact) {
        for (const candidate of state.artifacts ?? []) {
          if (run.cancelReason) return await finishCancelledRun(run);
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
      if (run.cancelReason) return await finishCancelledRun(run);
      state = await inspectRecordPage(tab.id, provider, "none");
      const nextProgressSignature = scanProgressSignature(state);
      stalledScrollSteps = !state.atBottom && nextProgressSignature === progressSignature
        ? stalledScrollSteps + 1
        : 0;
      progressSignature = nextProgressSignature;
      if (stalledScrollSteps >= MAX_STALLED_SCROLL_STEPS) {
        throw new Error("页面滚动连续多次未前进，已停止以避免生成不完整档案。");
      }
      await setBadge(tab.id, String(records.size), "#7c3aed");
    }
    if (run.cancelReason) return await finishCancelledRun(run);
    if (stableBottom < 3) {
      throw new Error("达到扫描步数上限前仍未确认对话底部，已停止以避免生成不完整档案。");
    }
    let ordered = [...records.values()].sort((left, right) => {
      const leftNumber = left.id.match(/^conversation-turn-(\d+)$/u)?.[1];
      const rightNumber = right.id.match(/^conversation-turn-(\d+)$/u)?.[1];
      return leftNumber !== undefined && rightNumber !== undefined
        ? Number(leftNumber) - Number(rightNumber)
        : 0;
    });
    if (cutoffId) {
      const cutoffIndex = ordered.findIndex((record) => record.id === cutoffId);
      if (cutoffIndex < 0) throw new Error("没有在完整扫描中找到触发消息，未生成不完整记录。");
      ordered = ordered.slice(0, cutoffIndex + 1);
    }
    if (!ordered.length) {
      throw new Error("没有识别到任何可归档消息；请确认当前页面是已支持的完整会话页。");
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
    await refreshRunAttachment(run);
    if (run.cancelReason) return await finishCancelledRun(run);
    run.phase = "writing";
    await writeWebArchive(provider.id, conversationId, body, artifacts, state.url);
    await finishRunWithSuccess(run);
    const savedArtifacts = [...artifacts.values()].filter((artifact) => artifact.status === "saved").length;
    const failedArtifacts = artifacts.size - savedArtifacts;
    const artifactSummary = artifacts.size
      ? `，保存 ${savedArtifacts} 个文件${failedArtifacts ? `，${failedArtifacts} 个不可用` : ""}`
      : "";
    return { ok: true, message: `已记录 ${ordered.length} 条消息${artifactSummary}。` };
  } catch (error) {
    console.error("Raw Record export failed", error);
    if (run.cancelReason) return await finishCancelledRun(run);
    const message = errorMessage(error);
    return await finishRunWithError(run, message);
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

async function handleRecordControl(message) {
  if (message.mode === "stop") {
    if (!activeRun) return { ok: false, message: "当前没有正在执行的任务。" };
    if (activeRun.phase === "writing") {
      return { ok: false, message: "档案正在写入，已无法安全停止；请等待写入完成。" };
    }
    activeRun.cancelReason = "user";
    return { ok: true, message: "正在安全停止，不会保存不完整档案。" };
  }

  if (message.mode !== "record") return { ok: false, message: "不支持的操作。" };
  if (activeRun) return { ok: false, busy: true, message: "已有记录任务正在执行。" };

  const tab = await chrome.tabs.get(message.tabId);
  const provider = resolveProvider(tab.url);
  if (!provider) {
    const message = "当前网页尚未配置 Yalo note it 页面适配器。";
    return finishWithError(tab.id, message, "NO", tab.url);
  }

  return runRecordExport(tab, provider);
}

async function handleRecordStatus(message) {
  if (!Number.isInteger(message.tabId) || message.tabId <= 0) {
    return { ok: false, message: "无效的标签页 ID。" };
  }
  if (message.mode === "get") {
    const savedError = await readTabError(message.tabId);
    if (savedError?.pageUrl && message.pageUrl
        && !isSameRecordContext(savedError.pageUrl, message.pageUrl)) {
      await clearTabState(message.tabId);
      return { ok: true, error: null };
    }
    return { ok: true, error: savedError };
  }
  if (message.mode === "clear") {
    await clearTabState(message.tabId);
    return { ok: true };
  }
  if (message.mode === "error") {
    const detail = typeof message.message === "string" && message.message
      ? message.message
      : "操作失败。";
    await finishWithError(message.tabId, detail, "ERR", message.pageUrl);
    return { ok: true };
  }
  return { ok: false, message: "不支持的状态操作。" };
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const handler = message?.type === "record-control"
    ? handleRecordControl
    : message?.type === "record-status"
      ? handleRecordStatus
      : null;
  if (!handler) return false;
  handler(message)
    .then(sendResponse)
    .catch(async (error) => {
      const detail = errorMessage(error);
      let errorState = null;
      if (Number.isInteger(message.tabId) && message.tabId > 0) {
        let pageUrl = typeof message.pageUrl === "string" ? message.pageUrl : null;
        if (!pageUrl) {
          try {
            pageUrl = (await chrome.tabs.get(message.tabId)).url;
          } catch {
            // The tab may already be closed.
          }
        }
        if (pageUrl) errorState = await finishWithError(message.tabId, detail, "ERR", pageUrl);
      }
      sendResponse({
        ok: false,
        message: detail,
        ...(errorState?.detached ? { detached: true } : {}),
      });
    });
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const run = activeRun?.tabId === tabId ? activeRun : null;
  if (run) {
    if (run.phase === "writing") run.detached = true;
    else run.cancelReason = "navigation";
  }
  clearTabError(tabId);
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  const run = activeRun?.tabId === tabId ? activeRun : null;
  if (run && isSameRecordContext(run.sourceUrl, changeInfo.url)) return;
  if (run) {
    if (run.phase === "writing") run.detached = true;
    else run.cancelReason = "navigation";
  }
  Promise.resolve()
    .then(async () => {
      const savedError = await readTabError(tabId);
      if (savedError?.pageUrl && isSameRecordContext(savedError.pageUrl, changeInfo.url)) return;
      await clearTabState(tabId);
    })
    .catch((error) => console.error("Failed to reset Yalo note it tab state", error));
});
