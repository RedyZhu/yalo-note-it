const statusElement = document.querySelector("#status");
const actionButtons = [...document.querySelectorAll("button")];
const recordButton = document.querySelector("#record");
const stopButton = document.querySelector("#stop");
const openArchiveButton = document.querySelector("#open-archive");
const configureButton = document.querySelector("#configure");
const ARCHIVE_FOLDER_NAME = "YaloNote";
const RECOGNIZED_ARCHIVE_FOLDER_NAMES = new Set(["yalonote", "yalo note", "yalo-note"]);

let operationState = "initializing";
let stopPending = false;
let archiveRootConfigured = false;
let currentArchiveContext = null;

function setActionButtonsDisabled(disabled) {
  for (const button of actionButtons) button.disabled = disabled;
}

function syncOperationControls() {
  recordButton.disabled = stopPending || operationState !== "idle";
  configureButton.disabled = stopPending || operationState !== "idle";
  openArchiveButton.disabled = stopPending
    || operationState !== "idle"
    || !archiveRootConfigured
    || !currentArchiveContext?.conversationId;
  stopButton.disabled = stopPending
    || operationState === "initializing"
    || operationState === "preparing"
    || operationState === "configuring"
    || operationState === "opening"
    || operationState === "closing";
}

function setOperationState(state) {
  operationState = state;
  syncOperationControls();
}

function setStatus(message, kind = "info") {
  statusElement.textContent = message;
  statusElement.dataset.kind = kind;
}

async function updateRecordStatus(tabId, mode, message = null, pageUrl = null) {
  const response = await chrome.runtime.sendMessage({
    type: "record-status",
    mode,
    tabId,
    ...(message ? { message } : {}),
    ...(pageUrl ? { pageUrl } : {}),
  });
  if (!response?.ok) throw new Error(response?.message ?? "无法读取记录任务状态。" );
  return response;
}

async function persistRecordError(tab, message) {
  try {
    await updateRecordStatus(tab.id, "error", message, tab.url);
  } catch (error) {
    console.error("Failed to persist Yalo note it error", error);
  }
}

async function ensureFileUrlAccess() {
  if (!chrome.extension?.isAllowedFileSchemeAccess
      || !(await chrome.extension.isAllowedFileSchemeAccess())) {
    throw new Error("请在扩展详情页开启“允许访问文件网址”，然后重新打开当前归档。");
  }
}

async function verifyWritable(handle) {
  let permission = await handle.queryPermission({ mode: "readwrite" });
  if (permission !== "granted") {
    permission = await handle.requestPermission({ mode: "readwrite" });
  }
  if (permission !== "granted") {
    throw new Error("浏览器未授予记录目录的读写权限，请重新授权或选择其他目录。");
  }

  const testName = `.yalo-note-write-test-${Date.now()}-${crypto.randomUUID()}.tmp`;
  try {
    const fileHandle = await handle.getFileHandle(testName, { create: true });
    const writable = await fileHandle.createWritable();
    await writable.write("Yalo note it write test");
    await writable.close();
    await handle.removeEntry(testName);
  } catch (error) {
    try {
      await handle.removeEntry(testName);
    } catch {
      // The test file may not have been created.
    }
    throw new Error(`无法写入所选目录：${error?.message ?? "未知文件系统错误"}`);
  }
}

async function configuredArchiveRoot(base) {
  if (RECOGNIZED_ARCHIVE_FOLDER_NAMES.has(base.name.toLocaleLowerCase())) return base;
  try {
    return await base.getDirectoryHandle(ARCHIVE_FOLDER_NAME);
  } catch (error) {
    if (error?.name === "NotFoundError") {
      throw new Error("所选目录中没有 Codex 已配置的 YaloNote 文件夹。");
    }
    throw error;
  }
}

async function control(mode) {
  if (mode === "record") {
    if (operationState !== "idle" || stopPending) return;
    setOperationState("preparing");
  } else {
    if (stopPending) return;
    stopPending = true;
    syncOperationControls();
  }

  let closeScheduled = false;
  let tab = null;
  try {
    [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("找不到当前标签页。");
    setStatus(mode === "record" ? "正在读取对话，请保持此窗口打开…" : "正在停止…");
    if (mode === "record") {
      const handle = await self.YaloArchiveAccess.readArchiveRoot();
      if (!handle) throw new Error("尚未设置目录，请先录入一个明确的本地路径。");
      setStatus("正在确认记录目录权限与写入能力…");
      await verifyWritable(handle);
      setStatus("正在读取对话，请保持此窗口打开…");
      setOperationState("recording");
    }
    const response = await chrome.runtime.sendMessage({
      type: "record-control",
      mode,
      tabId: tab.id,
      pageUrl: tab.url,
    });
    const message = response?.message ?? "操作失败。";
    if (mode === "record" && !response?.ok
        && !response?.cancelled && !response?.detached && !response?.busy) {
      await persistRecordError(tab, message);
    }
    const kind = response?.cancelled || response?.detached || response?.busy
      ? "info"
      : response?.ok
        ? "success"
        : mode === "record" ? "error" : "info";
    setStatus(message, kind);
    if (response?.ok) {
      closeScheduled = true;
      setOperationState("closing");
      setTimeout(() => window.close(), 1200);
    }
  } catch (error) {
    const message = error?.message ?? "插件后台意外停止，请重试。";
    if (mode === "record" && tab?.id) await persistRecordError(tab, message);
    setStatus(message, "error");
  } finally {
    if (mode === "record" && !closeScheduled
        && (operationState === "preparing" || operationState === "recording")) {
      setOperationState("idle");
    }
    if (mode === "stop") {
      stopPending = false;
      if (!closeScheduled) syncOperationControls();
    }
  }
}

async function configureRoot() {
  if (operationState !== "idle") return;
  setOperationState("configuring");
  try {
    const base = await window.showDirectoryPicker({ id: "yalo-note-base", mode: "readwrite" });
    const handle = await configuredArchiveRoot(base);
    setStatus("正在验证目录读写权限…");
    await verifyWritable(handle);
    setStatus("正在验证 Codex 路径配置…");
    await self.YaloArchiveAccess.readArchiveLocation(handle);
    await self.YaloArchiveAccess.saveArchiveRoot(handle);
    archiveRootConfigured = true;
    setStatus(`目录已验证并保存：${handle.name}`, "success");
  } catch (error) {
    if (error?.name !== "AbortError") {
      setStatus(error?.message ?? "目录设置失败。", "error");
    }
  } finally {
    if (operationState === "configuring") setOperationState("idle");
  }
}

async function openCurrentArchive() {
  if (operationState !== "idle" || stopPending) return;
  setOperationState("opening");
  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab?.id) throw new Error("找不到当前标签页。");
    const context = self.YaloArchiveAccess.contextForUrl(tab.url);
    if (!context) throw new Error("当前网页尚未配置 Yalo note it 页面适配器。");
    if (!context.conversationId) {
      throw new Error("当前页面没有稳定的会话 ID，无法定位归档。");
    }
    const handle = await self.YaloArchiveAccess.readArchiveRoot();
    if (!handle) throw new Error("尚未设置目录，请先录入一个明确的本地路径。");
    setStatus("正在查找当前会话归档…");
    await self.YaloArchiveAccess.ensureReadable(handle);
    await self.YaloArchiveAccess.readConversationArchive(
      handle,
      context.providerId,
      context.conversationId,
    );
    const archiveUrl = await self.YaloArchiveAccess.conversationDirectoryFileUrl(
      handle,
      context.providerId,
      context.conversationId,
    );
    await ensureFileUrlAccess();
    await chrome.tabs.create({ url: archiveUrl });
    setStatus("已在浏览器中打开当前会话归档目录。", "success");
  } catch (error) {
    setStatus(error?.message ?? "无法打开当前会话归档。", "error");
  } finally {
    if (operationState === "opening") setOperationState("idle");
  }
}

openArchiveButton.addEventListener("click", openCurrentArchive);
document.querySelector("#configure").addEventListener("click", configureRoot);
document.querySelector("#record").addEventListener("click", () => control("record"));
document.querySelector("#stop").addEventListener("click", () => control("stop"));

async function initializeStatus() {
  setActionButtonsDisabled(true);
  try {
    const [[tab], handle] = await Promise.all([
      chrome.tabs.query({ active: true, currentWindow: true }),
      self.YaloArchiveAccess.readArchiveRoot(),
    ]);
    currentArchiveContext = self.YaloArchiveAccess.contextForUrl(tab?.url);
    archiveRootConfigured = Boolean(handle);
    if (tab?.id) {
      const status = await updateRecordStatus(tab.id, "get", null, tab.url);
      const savedError = status.error;
      if (savedError) {
        setStatus(`上次记录失败：${savedError.message}`, "error");
        return;
      }
      const badge = await chrome.action.getBadgeText({ tabId: tab.id });
      if (badge === "ERR") {
        setStatus("上次记录失败，但旧版本没有保存错误详情。请再次运行以获取具体原因。", "error");
        return;
      }
    }
    if (!handle) {
      setStatus("尚未设置目录，请录入一个明确的本地路径。");
      return;
    }
    const permission = await handle.queryPermission({ mode: "readwrite" });
    setStatus(permission === "granted"
      ? `当前记录目录：${handle.name}`
      : `已保存目录 ${handle.name}；点击 Yalo note it 时会请求恢复写权限。`);
  } catch (error) {
    setStatus(error?.message ?? "无法读取目录设置。", "error");
  } finally {
    operationState = "idle";
    syncOperationControls();
  }
}

initializeStatus();
