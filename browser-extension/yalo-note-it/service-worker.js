importScripts(
  "archive-settings.js",
  "task-status-store.js",
  "providers/chatgpt.js",
  "providers/deepseek.js",
);

const LOAD_SETTLE_MS = 4000;
const MAX_RECORD_STEPS = 2000;
const MAX_STALLED_SCROLL_STEPS = 12;
const MAX_GENERATING_STEPS = 150;
const MAX_ARTIFACT_BYTES = 25 * 1024 * 1024;

const PROVIDERS = self.YaloNoteProviders ?? [];
const ARCHIVE_SETTINGS = self.YaloArchiveSettings;
const TASK_STATUS_STORE = self.YaloTaskStatusStore;

let activeRun = null;
let openingTaskWindow = null;

async function openTaskWindow(tab) {
  if (openingTaskWindow) return openingTaskWindow;
  openingTaskWindow = (async () => {
    const page = chrome.runtime.getURL("popup.html");
    const existing = (await chrome.tabs.query({ url: `${page}*` }))[0];
    const targetTabId = activeRun?.tabId ?? tab.id;
    const url = `${page}?targetTabId=${targetTabId}`;
    if (existing) {
      // A task window is permanently bound to its original conversation. This
      // remains safe even when the MV3 worker restarts during a native picker.
      await chrome.windows.update(existing.windowId, { focused: true });
      return;
    }
    await chrome.windows.create({ url, type: "popup", width: 380, height: 390 });
  })();
  try {
    await openingTaskWindow;
  } finally {
    openingTaskWindow = null;
  }
}

chrome.action.onClicked.addListener((tab) => {
  openTaskWindow(tab).catch(console.error);
});

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

async function saveTaskStatus(tabId, pageUrl, status) {
  const contextIdentity = recordContextIdentity(pageUrl);
  const value = {
    schemaVersion: 1,
    tabId,
    pageUrl,
    updatedAt: new Date().toISOString(),
    ...status,
  };
  return TASK_STATUS_STORE.write(tabId, contextIdentity, value);
}

async function trySaveTaskStatus(tabId, pageUrl, status) {
  try {
    await saveTaskStatus(tabId, pageUrl, status);
    return true;
  } catch (error) {
    console.error("Failed to persist Yalo note it task status", error);
    return false;
  }
}

async function readTaskStatus(tabId, pageUrl) {
  const value = await TASK_STATUS_STORE.read(tabId, recordContextIdentity(pageUrl));
  return value?.schemaVersion === 1 ? value : null;
}

async function currentTaskStatus(tabId, pageUrl) {
  const status = await readTaskStatus(tabId, pageUrl);
  if (!status) return null;
  if (status.running && (!activeRun || activeRun.tabId !== tabId
      || !isSameRecordContext(activeRun.sourceUrl, pageUrl))) {
    const interrupted = {
      ok: false,
      running: false,
      phase: "finished",
      message: "上次记录任务已中断，未确认完成。请在独立记录页重新运行。",
      finishedAt: new Date().toISOString(),
    };
    await trySaveTaskStatus(tabId, pageUrl, interrupted);
    return interrupted;
  }
  if (activeRun?.tabId === tabId && isSameRecordContext(activeRun.sourceUrl, pageUrl)) {
    return { ...status, running: true, phase: activeRun.phase };
  }
  return status;
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

function errorMessage(error) {
  return error instanceof Error ? error.message : String(error);
}

function recordPayloadSignature(records) {
  return JSON.stringify((records ?? []).map((record) => [
    record.id,
    record.role,
    record.markdown,
    record.attachments ?? [],
  ]));
}

function scanProgressSignature(state) {
  const scrollTop = Number.isFinite(state?.scrollTop) ? Math.round(state.scrollTop) : "unknown";
  const scrollHeight = Number.isFinite(state?.scrollHeight) ? Math.round(state.scrollHeight) : "unknown";
  return `${scrollTop}|${scrollHeight}|${recordPayloadSignature(state?.records)}`;
}

async function clearTabBadge(tabId) {
  try {
    await chrome.action.setBadgeText({ tabId, text: "" });
  } catch (error) {
    console.error("Failed to clear Yalo note it badge", error);
  }
}

function detachedResult(message) {
  return { ok: false, running: false, detached: true, message };
}

async function refreshRunAttachment(run) {
  if (run.cancelReason || run.detached) return;
  if (await isTabInRecordContext(run.tabId, run.sourceUrl)) return;
  if (run.phase === "writing") run.detached = true;
  else run.cancelReason = "navigation";
}

async function finishCancelledRun(run) {
  await clearTabBadge(run.tabId);
  const result = {
    ok: false,
    running: false,
    phase: "finished",
    cancelled: true,
    message: run.cancelReason === "navigation"
      ? "页面已切换，原记录任务已安全停止，未生成档案。"
      : "记录任务已停止，未生成或修改档案。",
    finishedAt: new Date().toISOString(),
  };
  if (!run.detached && !(await trySaveTaskStatus(run.tabId, run.sourceUrl, result))) {
    result.message += "；任务状态未保存。";
  }
  return result;
}

async function finishRunWithError(run, message) {
  await refreshRunAttachment(run);
  if (run.cancelReason) return await finishCancelledRun(run);
  if (run.detached) return detachedResult(message);
  const result = {
    ok: false,
    running: false,
    phase: "finished",
    message,
    finishedAt: new Date().toISOString(),
  };
  if (!(await trySaveTaskStatus(run.tabId, run.sourceUrl, result))) {
    result.message += "；任务状态未保存。";
  }
  await refreshRunAttachment(run);
  if (run.detached) {
    await clearTabBadge(run.tabId);
    return detachedResult(message);
  }
  await setBadge(run.tabId, "ERR", "#dc2626");
  return result;
}

async function finishRunWithSuccess(run, message) {
  await refreshRunAttachment(run);
  const result = {
    ok: true,
    running: false,
    phase: "finished",
    message,
    finishedAt: new Date().toISOString(),
  };
  if (run.detached) {
    result.detached = true;
    result.message += "；归档已提交，但原会话标签页已关闭或切换。";
  }
  if (!(await trySaveTaskStatus(run.tabId, run.sourceUrl, result))) {
    result.message += "；归档已提交，但任务状态未保存。";
  }
  await refreshRunAttachment(run);
  if (run.detached && !result.detached) {
    result.detached = true;
    result.message += "；归档已提交，但原会话标签页已关闭或切换。";
    await trySaveTaskStatus(run.tabId, run.sourceUrl, result);
  }
  if (run.detached) {
    await clearTabBadge(run.tabId);
  } else {
    await setBadge(run.tabId, "OK", "#16a34a");
  }
  return result;
}

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function timestampForPath() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function setBadge(tabId, text, color = "#2563eb") {
  try {
    await chrome.action.setBadgeBackgroundColor({ tabId, color });
    await chrome.action.setBadgeText({ tabId, text });
  } catch (error) {
    console.error("Failed to update Yalo note it badge", error);
  }
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

function nonEmptyString(value) {
  return typeof value === "string" && value.trim().length > 0;
}

function prepareArtifactsForWrite(artifacts) {
  if (!artifacts || typeof artifacts.entries !== "function") {
    throw new Error("附件集合格式无效。");
  }
  const prepared = [];
  for (const [mapKey, artifact] of artifacts.entries()) {
    if (!nonEmptyString(mapKey) || !artifact || typeof artifact !== "object") {
      throw new Error("附件记录格式无效。");
    }
    const label = artifact.displayName ?? artifact.key ?? mapKey;
    if (artifact.key !== mapKey) {
      throw new Error(`文件 ${label} 的索引键与附件身份不一致。`);
    }
    if (!nonEmptyString(artifact.key)
        || !nonEmptyString(artifact.messageId)
        || !nonEmptyString(artifact.displayName)
        || !nonEmptyString(artifact.capturedAt)
        || !Number.isFinite(Date.parse(artifact.capturedAt))) {
      throw new Error(`文件 ${label} 的捕获元数据无效。`);
    }
    const kind = artifact.kind ?? "uploaded";
    if (!nonEmptyString(kind)
        || !["saved", "unavailable", "too_large"].includes(artifact.status)) {
      throw new Error(`文件 ${label} 的捕获状态无效。`);
    }
    if (artifact.sourceUrl !== undefined && !nonEmptyString(artifact.sourceUrl)) {
      throw new Error(`文件 ${label} 的来源地址无效。`);
    }
    if (artifact.error !== undefined && !nonEmptyString(artifact.error)) {
      throw new Error(`文件 ${label} 的错误信息无效。`);
    }
    const hasSize = artifact.size !== undefined;
    if (hasSize && (!Number.isSafeInteger(artifact.size) || artifact.size < 0)) {
      throw new Error(`文件 ${label} 的大小无效。`);
    }

    let bytes = null;
    if (artifact.status === "saved") {
      if (!hasSize || !nonEmptyString(artifact.mimeType)
          || typeof artifact.base64 !== "string") {
        throw new Error(`文件 ${label} 缺少已保存内容。`);
      }
      if (artifact.size > MAX_ARTIFACT_BYTES) {
        throw new Error(`文件 ${label} 超过 25 MB 的插件保存上限。`);
      }
      if (artifact.base64.length % 4 !== 0
          || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/u.test(artifact.base64)) {
        throw new Error(`文件 ${label} 的已保存内容不是有效的 Base64。`);
      }
      bytes = base64ToBytes(artifact.base64);
      if (bytes.byteLength !== artifact.size) {
        throw new Error(`文件 ${label} 的内容大小与捕获元数据不一致。`);
      }
    } else if (artifact.status === "too_large"
        && (!hasSize || artifact.size <= MAX_ARTIFACT_BYTES)) {
      throw new Error(`文件 ${label} 的超限状态与大小不一致。`);
    }
    prepared.push({ artifact, bytes, kind });
  }
  return prepared;
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
    await writable.close();
  } catch (error) {
    try {
      await writable.abort?.(error);
    } catch (abortError) {
      console.error("Failed to abort incomplete archive file", abortError);
    }
    throw error;
  }
}

function bytesEqual(left, right) {
  if (left.byteLength !== right.byteLength) return false;
  return left.every((byte, index) => byte === right[index]);
}

async function existingFileBytes(directory, name) {
  try {
    const handle = await directory.getFileHandle(name);
    return new Uint8Array(await (await handle.getFile()).arrayBuffer());
  } catch (error) {
    if (error?.name === "NotFoundError") return null;
    throw error;
  }
}

function suffixedFileName(baseName, suffix) {
  const dot = baseName.lastIndexOf(".");
  return dot > 0
    ? `${baseName.slice(0, dot)}-${suffix}${baseName.slice(dot)}`
    : `${baseName}-${suffix}`;
}

async function storeAssetFile(directory, displayName, bytes, digest, reservedNames) {
  const baseName = safeFileName(displayName);
  for (let suffix = 1; ; suffix += 1) {
    const localName = suffix === 1 ? baseName : suffixedFileName(baseName, suffix);
    const key = localName.toLocaleLowerCase();
    const reserved = reservedNames.get(key);
    if (reserved) {
      if (reserved.digest === digest) return reserved.localName;
      continue;
    }
    const existing = await existingFileBytes(directory, localName);
    if (existing && !bytesEqual(existing, bytes)) continue;
    if (!existing) await writeFile(directory, localName, bytes);
    reservedNames.set(key, { digest, localName });
    return localName;
  }
}

async function writeWebArchive(providerId, conversationId, body, artifacts, sourceUrl) {
  const preparedArtifacts = prepareArtifactsForWrite(artifacts);
  const root = await ARCHIVE_SETTINGS.loadArchiveRoot();
  if (!root) throw new Error("请先在插件中选择 Codex 已初始化的 YaloNote 目录。");
  if ((await root.queryPermission({ mode: "readwrite" })) !== "granted") {
    throw new Error("记录目录写权限已失效。请在独立记录页点击 Yalo note it 重新授权，并保持记录页打开直到完成。" );
  }
  let directory = root;
  for (const name of ["sessions", providerId, conversationId]) {
    directory = await directory.getDirectoryHandle(name, { create: true });
  }
  const assetsDirectory = await directory.getDirectoryHandle("assets", { create: true });
  const reservedNames = new Map();
  const manifestArtifacts = [];
  for (const { artifact, bytes, kind } of preparedArtifacts) {
    const entry = {
      key: artifact.key,
      messageId: artifact.messageId,
      displayName: artifact.displayName,
      kind,
      status: artifact.status,
      capturedAt: artifact.capturedAt,
    };
    if (artifact.sourceUrl) entry.sourceUrl = artifact.sourceUrl;
    if (artifact.error) entry.error = artifact.error;
    if (artifact.status === "saved") {
      const digest = await sha256(bytes);
      const localName = await storeAssetFile(
        assetsDirectory,
        artifact.displayName,
        bytes,
        digest,
        reservedNames,
      );
      entry.localPath = `assets/${localName}`;
      entry.mimeType = artifact.mimeType;
      entry.size = bytes.byteLength;
      entry.sha256 = digest;
    } else if (Number.isFinite(artifact.size)) {
      entry.size = artifact.size;
    }
    manifestArtifacts.push(entry);
  }

  const recordBytes = new TextEncoder().encode(body);
  const recordDigest = await sha256(recordBytes);
  const recordsDirectory = await directory.getDirectoryHandle("records", { create: true });
  const recordName = `${recordDigest}.md`;
  const existingRecord = await existingFileBytes(recordsDirectory, recordName);
  if (existingRecord && !bytesEqual(existingRecord, recordBytes)) {
    throw new Error("归档正文哈希冲突，未覆盖已有记录。");
  }
  if (!existingRecord) await writeFile(recordsDirectory, recordName, recordBytes);

  const manifest = {
    schemaVersion: 2,
    provider: providerId,
    conversationId,
    sourceUrl,
    exportedAt: new Date().toISOString(),
    record: {
      localPath: `records/${recordName}`,
      size: recordBytes.byteLength,
      sha256: recordDigest,
    },
    artifacts: manifestArtifacts,
  };
  await writeFile(directory, "manifest.json", `${JSON.stringify(manifest, null, 2)}\n`);
  let canonicalUpdated = true;
  try {
    await writeFile(directory, "conversation.raw-record.md", recordBytes);
  } catch (error) {
    canonicalUpdated = false;
    console.error("Committed archive but failed to update convenience Raw Record", error);
  }
  return { canonicalUpdated };
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
    await trySaveTaskStatus(tab.id, tab.url, {
      ok: true,
      running: true,
      phase: run.phase,
      message: "正在扫描对话，请保持记录窗口打开。",
      startedAt: run.startedAt,
    });
    await setBadge(tab.id, "REC", "#7c3aed");
    let state = await inspectRecordPage(tab.id, provider, "top");
    if (state?.error) throw new Error(state.error);
    let stableTop = 0;
    let topSignature = "";
    for (let step = 0; step < 20 && !run.cancelReason; step += 1) {
      await sleep(LOAD_SETTLE_MS);
      await refreshRunAttachment(run);
      if (run.cancelReason) return await finishCancelledRun(run);
      state = await inspectRecordPage(tab.id, provider, "top");
      if (state?.error) throw new Error(state.error);
      const signature = `${state.scrollHeight}|${state.records?.[0]?.id ?? ""}`;
      stableTop = state.atTop && signature === topSignature ? stableTop + 1 : 0;
      topSignature = signature;
      if (stableTop >= 2) break;
    }
    if (run.cancelReason) return await finishCancelledRun(run);
    if (stableTop < 2) throw new Error("达到等待上限前仍未确认稳定的对话顶部。");
    state = await inspectRecordPage(tab.id, provider, "none");
    if (state?.error) throw new Error(state.error);
    if (!state.atTop) {
      throw new Error("对话在顶部稳定检查后发生回退，已停止以避免生成不完整档案。");
    }
    let stableBottom = 0;
    let bottomSignature = "";
    let stalledScrollSteps = 0;
    let generatingSteps = 0;
    let progressSignature = scanProgressSignature(state);
    for (let step = 0; step < MAX_RECORD_STEPS && !run.cancelReason; step += 1) {
      for (const record of state.records ?? []) records.set(record.id, record);
      if (provider.captureArtifact) {
        for (const candidate of state.artifacts ?? []) {
          await refreshRunAttachment(run);
          if (run.cancelReason) return await finishCancelledRun(run);
          const previous = artifacts.get(candidate.key);
          const retryNow = previous?.retryWhenSettled && !state.generating;
          if (previous && !retryNow) continue;
          let captured;
          try {
            captured = await captureArtifact(tab.id, provider, candidate.key);
            if (!captured) throw new Error("文件捕获未返回结果。");
          } catch (error) {
            captured = { status: "unavailable", error: String(error.message ?? error) };
          }
          artifacts.set(candidate.key, {
            ...candidate,
            ...captured,
            capturedAt: new Date().toISOString(),
            retryWhenSettled: Boolean(state.generating && captured.status === "unavailable"),
          });
        }
      }
      generatingSteps = state.atBottom && state.generating ? generatingSteps + 1 : 0;
      if (generatingSteps >= MAX_GENERATING_STEPS) {
        throw new Error("页面持续显示正在生成，达到等待上限后停止，未写入可能不完整的归档。");
      }
      if (state.atBottom && !state.generating) {
        const signature = `${state.scrollHeight}|${artifacts.size}|${recordPayloadSignature([...records.values()])}`;
        stableBottom = signature === bottomSignature ? stableBottom + 1 : 0;
        bottomSignature = signature;
      } else {
        stableBottom = 0;
        bottomSignature = "";
      }
      if (stableBottom >= 3) break;
      state = await inspectRecordPage(tab.id, provider, "next");
      if (state?.error) throw new Error(state.error);
      await sleep(state.atBottom ? LOAD_SETTLE_MS : 400);
      await refreshRunAttachment(run);
      if (run.cancelReason) return await finishCancelledRun(run);
      state = await inspectRecordPage(tab.id, provider, "none");
      if (state?.error) throw new Error(state.error);
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
    if (provider.captureArtifact) {
      for (const previous of [...artifacts.values()]) {
        if (!previous.retryWhenSettled) continue;
        await refreshRunAttachment(run);
        if (run.cancelReason) return await finishCancelledRun(run);
        let captured;
        try {
          captured = await captureArtifact(tab.id, provider, previous.key);
          if (!captured) throw new Error("文件捕获未返回结果。");
        } catch (error) {
          captured = { status: "unavailable", error: String(error.message ?? error) };
        }
        artifacts.set(previous.key, {
          ...previous,
          ...captured,
          capturedAt: new Date().toISOString(),
          retryWhenSettled: false,
        });
      }
    }
    let ordered = [...records.values()];
    if (cutoffId) {
      const cutoffIndex = ordered.findIndex((record) => record.id === cutoffId);
      if (cutoffIndex < 0) throw new Error("没有在完整扫描中找到触发消息，未生成不完整记录。");
      ordered = ordered.slice(0, cutoffIndex + 1);
    }
    if (!ordered.length) {
      throw new Error("没有识别到任何可归档消息；请确认当前页面是已支持的完整会话页。");
    }
    const conversationId = provider.conversationId(run.sourceUrl) ?? timestampForPath();
    const lines = [
      `# ${provider.label} Conversation Raw Record`,
      "",
      `> Source: ${run.sourceUrl}`,
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
      for (const attachment of record.attachments ?? []) lines.push(`- [附件：${attachment}]`);
      if (record.attachments?.length) lines.push("");
      lines.push(record.markdown || "[无文本正文]", "");
    }
    const body = lines.join("\n");
    await refreshRunAttachment(run);
    if (run.cancelReason) return await finishCancelledRun(run);
    run.phase = "writing";
    await trySaveTaskStatus(run.tabId, run.sourceUrl, {
      ok: true,
      running: true,
      phase: run.phase,
      message: "扫描完成，正在写入归档；此阶段不能安全停止。",
      startedAt: run.startedAt,
    });
    const archiveWrite = await writeWebArchive(
      provider.id,
      conversationId,
      body,
      artifacts,
      run.sourceUrl,
    );
    const savedArtifacts = [...artifacts.values()].filter((artifact) => artifact.status === "saved").length;
    const failedArtifacts = artifacts.size - savedArtifacts;
    const artifactSummary = artifacts.size
      ? `，保存 ${savedArtifacts} 个文件${failedArtifacts ? `，${failedArtifacts} 个不可用` : ""}`
      : "";
    const canonicalSummary = archiveWrite?.canonicalUpdated === false
      ? "；归档已提交，但便捷 Raw Record 副本未更新，请以 manifest 为准"
      : "";
    return await finishRunWithSuccess(
      run,
      `已记录 ${ordered.length} 条消息${artifactSummary}${canonicalSummary}。`,
    );
  } catch (error) {
    console.error("Raw Record export failed", error);
    if (run.cancelReason) return await finishCancelledRun(run);
    return await finishRunWithError(run, errorMessage(error));
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

async function handleRecordControl(message) {
  if (message.mode === "status") {
    const tab = await chrome.tabs.get(message.tabId);
    return currentTaskStatus(message.tabId, message.pageUrl ?? tab.url);
  }
  if (message.mode === "stop") {
    if (!activeRun || activeRun.tabId !== message.tabId) {
      return { ok: false, running: false, message: "当前标签页没有正在执行的任务。" };
    }
    if (activeRun.phase === "writing") {
      return {
        ok: false,
        running: true,
        phase: activeRun.phase,
        message: "档案正在写入，已无法安全停止；请等待写入完成。",
      };
    }
    activeRun.cancelReason = "user";
    await setBadge(activeRun.tabId, "…", "#ca8a04");
    return { ok: true, running: true, phase: activeRun.phase, message: "正在安全停止，不会保存不完整档案。" };
  }

  if (message.mode !== "record") return { ok: false, message: "不支持的操作。" };
  if (activeRun) {
    return {
      ok: false,
      busy: true,
      running: true,
      phase: activeRun.phase,
      message: "已有记录任务正在执行。",
    };
  }

  const tab = await chrome.tabs.get(message.tabId);
  if (message.pageUrl && !isSameRecordContext(message.pageUrl, tab.url)) {
    return { ok: false, detached: true, message: "目标页面已经切换，请重新打开记录窗口。" };
  }
  const provider = resolveProvider(tab.url);
  if (!provider) {
    await setBadge(tab.id, "NO", "#dc2626");
    const result = {
      ok: false,
      running: false,
      phase: "finished",
      message: "当前网页尚未配置 Yalo note it 页面适配器。",
      finishedAt: new Date().toISOString(),
    };
    await trySaveTaskStatus(tab.id, tab.url, result);
    return result;
  }

  return runRecordExport(tab, provider);
}

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  const operation = message?.type === "record-control"
    ? handleRecordControl(message)
    : null;
  if (!operation) return false;
  operation
    .then(sendResponse)
    .catch((error) => sendResponse({
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    }));
  return true;
});

chrome.tabs.onRemoved.addListener((tabId) => {
  const run = activeRun?.tabId === tabId ? activeRun : null;
  if (!run) return;
  if (run.phase === "writing") run.detached = true;
  else run.cancelReason = "navigation";
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  if (!changeInfo.url) return;
  const run = activeRun?.tabId === tabId ? activeRun : null;
  if (run && isSameRecordContext(run.sourceUrl, changeInfo.url)) return;
  if (run?.phase === "writing") run.detached = true;
  else if (run) run.cancelReason = "navigation";
  clearTabBadge(tabId).catch((error) => console.error("Failed to reset Yalo note it badge", error));
});
