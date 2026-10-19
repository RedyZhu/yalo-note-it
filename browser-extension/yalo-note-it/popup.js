const statusElement = document.querySelector("#status");
const actionButtons = [...document.querySelectorAll("button")];
const recordButton = document.querySelector("#record");
const stopButton = document.querySelector("#stop");
const openArchiveButton = document.querySelector("#open-archive");
const configureButton = document.querySelector("#configure");
const ARCHIVE_FOLDER_NAMES = ["YaloNote", "yalo note", "yalo-note"];
const RECOGNIZED_ARCHIVE_FOLDER_NAMES = new Set(
  ARCHIVE_FOLDER_NAMES.map((name) => name.toLocaleLowerCase()),
);
const targetTabParameter = new URLSearchParams(location.search).get("targetTabId");
const targetTabId = targetTabParameter && /^\d+$/u.test(targetTabParameter)
  ? Number(targetTabParameter)
  : null;

let operationState = "initializing";
let stopPending = false;
let archiveRootConfigured = false;
let currentArchiveContext = null;
let targetAvailable = false;
let taskRunning = false;
let activeRecordPhase = null;
let statusPollInFlight = false;
let recordCommandPending = false;

async function getTargetTab() {
  if (!Number.isInteger(targetTabId) || targetTabId <= 0) {
    throw new Error("记录窗口缺少目标标签页信息，请从需要记录的页面点击扩展图标重新打开。");
  }
  try {
    return await chrome.tabs.get(targetTabId);
  } catch (error) {
    throw new Error("原会话标签页已关闭，无法继续操作。", { cause: error });
  }
}

function setActionButtonsDisabled(disabled) {
  for (const button of actionButtons) button.disabled = disabled;
}

function syncOperationControls() {
  const idle = operationState === "idle" && !taskRunning && !stopPending;
  recordButton.disabled = !targetAvailable || !idle;
  configureButton.disabled = !idle;
  openArchiveButton.disabled = !targetAvailable
    || !idle
    || !archiveRootConfigured
    || !currentArchiveContext?.conversationId;
  stopButton.disabled = !targetAvailable
    || stopPending
    || !taskRunning
    || activeRecordPhase !== "scanning";
  stopButton.title = taskRunning && activeRecordPhase === "writing"
    ? "归档正在写入，当前阶段不能安全停止。"
    : "";
}

function setOperationState(state) {
  operationState = state;
  syncOperationControls();
}

function setStatus(message, kind = "info") {
  statusElement.textContent = message;
  statusElement.dataset.kind = kind;
}

function updateTaskState(response) {
  if (!response) return;
  taskRunning = response.running === true && !response.busy && !response.detached;
  activeRecordPhase = taskRunning ? response.phase ?? null : null;
  if (taskRunning && ["idle", "initializing"].includes(operationState)) {
    operationState = "recording";
  }
  if (!taskRunning && ["recording", "initializing"].includes(operationState)) {
    operationState = "idle";
  }
  syncOperationControls();
}

function responseKind(response, mode) {
  if (response?.cancelled || response?.busy) return "info";
  if (response?.ok) return "success";
  if (response?.detached) return "info";
  return mode === "record" ? "error" : "info";
}

async function readTaskStatus(tab) {
  return chrome.runtime.sendMessage({
    type: "record-control",
    mode: "status",
    tabId: tab.id,
    pageUrl: tab.url,
  });
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
  for (const name of ARCHIVE_FOLDER_NAMES) {
    try {
      return await base.getDirectoryHandle(name);
    } catch (error) {
      if (error?.name !== "NotFoundError") throw error;
    }
  }
  throw new Error("所选目录中没有 Codex 已配置的 YaloNote 文件夹。");
}

async function control(mode) {
  if (mode === "record") {
    if (operationState !== "idle" || taskRunning || stopPending) return;
    setOperationState("preparing");
  } else {
    if (stopPending || !taskRunning || activeRecordPhase !== "scanning") return;
    stopPending = true;
    syncOperationControls();
  }

  try {
    const tab = await getTargetTab();
    setStatus(mode === "record" ? "正在确认记录目录…" : "正在停止…");
    if (mode === "record") {
      const handle = await self.YaloArchiveSettings.loadArchiveRoot();
      if (!handle) throw new Error("尚未设置目录，请先选择 Codex 已配置的 YaloNote 文件夹。");
      await verifyWritable(handle);
      await self.YaloArchiveAccess.readArchiveLocation(handle);
      setStatus("正在读取对话，请保持此窗口打开…");
      taskRunning = true;
      activeRecordPhase = "scanning";
      recordCommandPending = true;
      setOperationState("recording");
    }
    const response = await chrome.runtime.sendMessage({
      type: "record-control",
      mode,
      tabId: tab.id,
      pageUrl: tab.url,
    });
    if (!response) throw new Error("插件后台没有返回任务状态，请重试。");
    updateTaskState(response);
    setStatus(response?.message ?? "操作失败。", responseKind(response, mode));
  } catch (error) {
    if (mode === "record") {
      recordCommandPending = false;
      taskRunning = false;
      activeRecordPhase = null;
    }
    setStatus(error?.message ?? "插件后台意外停止，请重试。", "error");
  } finally {
    if (mode === "record" && !taskRunning
        && (operationState === "preparing" || operationState === "recording")) {
      setOperationState("idle");
    }
    if (mode === "record") recordCommandPending = false;
    if (mode === "stop") {
      stopPending = false;
      syncOperationControls();
    }
  }
}

async function configureRoot() {
  if (operationState !== "idle" || taskRunning) return;
  setOperationState("configuring");
  try {
    const base = await window.showDirectoryPicker({ id: "yalo-note-base", mode: "readwrite" });
    const handle = await configuredArchiveRoot(base);
    setStatus("正在验证目录读写权限…");
    await verifyWritable(handle);
    setStatus("正在验证 Codex 路径配置…");
    await self.YaloArchiveAccess.readArchiveLocation(handle);
    await self.YaloArchiveSettings.saveArchiveRoot(handle);
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

async function ensureFileUrlAccess() {
  if (!chrome.extension?.isAllowedFileSchemeAccess
      || !(await chrome.extension.isAllowedFileSchemeAccess())) {
    throw new Error("请在扩展详情页开启“允许访问文件网址”，然后重新打开当前归档。");
  }
}

async function openCurrentArchive() {
  if (operationState !== "idle" || taskRunning || stopPending) return;
  setOperationState("opening");
  try {
    const tab = await getTargetTab();
    const context = self.YaloArchiveAccess.contextForUrl(tab.url);
    if (!context) throw new Error("当前网页尚未配置 Yalo note it 页面适配器。");
    if (!context.conversationId) {
      throw new Error("当前页面没有稳定的会话 ID，无法定位归档。");
    }
    const handle = await self.YaloArchiveSettings.loadArchiveRoot();
    if (!handle) throw new Error("尚未设置目录，请先选择 Codex 已配置的 YaloNote 文件夹。");
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
    await chrome.tabs.create({ url: archiveUrl, windowId: tab.windowId, active: true });
    setStatus("已在浏览器中打开当前会话归档目录。", "success");
  } catch (error) {
    setStatus(error?.message ?? "无法打开当前会话归档。", "error");
  } finally {
    if (operationState === "opening") setOperationState("idle");
  }
}

async function refreshRunningStatus() {
  if (!taskRunning || statusPollInFlight || stopPending) return;
  statusPollInFlight = true;
  try {
    const tab = await getTargetTab();
    const response = await readTaskStatus(tab);
    if (!response) {
      if (recordCommandPending) return;
      taskRunning = false;
      activeRecordPhase = null;
      setOperationState("idle");
      setStatus("记录任务已中断，未确认完成。请重新运行。", "error");
      return;
    }
    if (recordCommandPending && response.running !== true) return;
    if (!taskRunning) return;
    updateTaskState(response);
    if (response.message) {
      setStatus(response.message, response.running ? "info" : responseKind(response, "record"));
    }
  } catch (error) {
    setStatus(error?.message ?? "无法读取记录任务状态。", "error");
  } finally {
    statusPollInFlight = false;
  }
}

openArchiveButton.addEventListener("click", openCurrentArchive);
configureButton.addEventListener("click", configureRoot);
recordButton.addEventListener("click", () => control("record"));
stopButton.addEventListener("click", () => control("stop"));

async function initializeStatus() {
  setActionButtonsDisabled(true);
  try {
    const [tab, handle] = await Promise.all([
      getTargetTab(),
      self.YaloArchiveSettings.loadArchiveRoot(),
    ]);
    targetAvailable = true;
    currentArchiveContext = self.YaloArchiveAccess.contextForUrl(tab.url);
    archiveRootConfigured = Boolean(handle);
    const previous = await readTaskStatus(tab);
    if (previous) {
      updateTaskState(previous);
      if (previous.message) {
        const prefix = previous.running ? "当前任务" : "上次结果";
        setStatus(`${prefix}：${previous.message}`, previous.running ? "info" : responseKind(previous, "record"));
        return;
      }
    }
    if (!handle) {
      setStatus("尚未设置目录，请选择 Codex 已配置的 YaloNote 文件夹。");
      return;
    }
    const permission = await handle.queryPermission({ mode: "readwrite" });
    setStatus(permission === "granted"
      ? `当前记录目录：${handle.name}`
      : `已保存目录 ${handle.name}；点击 Yalo note it 时会请求恢复写权限。`);
  } catch (error) {
    setStatus(error?.message ?? "无法读取目录设置。", "error");
  } finally {
    if (!taskRunning) operationState = "idle";
    syncOperationControls();
  }
}

initializeStatus();
setInterval(refreshRunningStatus, 750);
