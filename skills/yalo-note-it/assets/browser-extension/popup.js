const statusElement = document.querySelector("#status");

async function openSettingsDatabase() {
  return new Promise((resolve, reject) => {
    const request = indexedDB.open("yalo-note-it", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("settings");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
}

async function readArchiveRoot() {
  const database = await openSettingsDatabase();
  try {
    return await new Promise((resolve, reject) => {
      const request = database.transaction("settings", "readonly")
        .objectStore("settings").get("archive-root");
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(request.error);
    });
  } finally {
    database.close();
  }
}

async function saveArchiveRoot(handle) {
  const database = await openSettingsDatabase();
  try {
    await new Promise((resolve, reject) => {
      const transaction = database.transaction("settings", "readwrite");
      transaction.objectStore("settings").put(handle, "archive-root");
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally {
    database.close();
  }
}

async function clearArchiveRoot() {
  const database = await openSettingsDatabase();
  try {
    await new Promise((resolve, reject) => {
      const transaction = database.transaction("settings", "readwrite");
      transaction.objectStore("settings").delete("archive-root");
      transaction.oncomplete = resolve;
      transaction.onerror = () => reject(transaction.error);
      transaction.onabort = () => reject(transaction.error);
    });
  } finally {
    database.close();
  }
}

async function verifyWritable(handle) {
  const permission = await handle.requestPermission({ mode: "readwrite" });
  if (permission !== "granted") {
    throw new Error("Chrome 未授予所选目录的读写权限，请重新选择并允许访问。");
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

async function control(mode) {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab?.id) {
    statusElement.textContent = "找不到当前标签页。";
    return;
  }

  statusElement.textContent = mode === "record" ? "正在读取对话，请保持此窗口打开…" : "正在停止…";
  try {
    const response = await chrome.runtime.sendMessage({
      type: "record-control",
      mode,
      tabId: tab.id,
    });
    statusElement.textContent = response?.message ?? "操作失败。";
    if (response?.ok) setTimeout(() => window.close(), 1200);
  } catch (error) {
    statusElement.textContent = error?.message ?? "插件后台意外停止，请重试。";
  }
}

async function configureRoot() {
  let handle;
  try {
    handle = await window.showDirectoryPicker({ id: "yalo-note-root", mode: "readwrite" });
    statusElement.textContent = "正在验证目录读写权限…";
    await verifyWritable(handle);
    await saveArchiveRoot(handle);
    statusElement.textContent = `目录已验证并保存：${handle.name}`;
  } catch (error) {
    if (error?.name !== "AbortError") {
      if (handle) await clearArchiveRoot().catch(() => {});
      statusElement.textContent = error?.message ?? "目录设置失败。";
    }
  }
}

document.querySelector("#configure").addEventListener("click", configureRoot);
document.querySelector("#record").addEventListener("click", () => control("record"));
document.querySelector("#stop").addEventListener("click", () => control("stop"));

readArchiveRoot()
  .then(async (handle) => {
    if (!handle) {
      statusElement.textContent = "尚未设置目录，请选择 D:\\MyData\\yalo-note。";
      return;
    }
    const permission = await handle.queryPermission({ mode: "readwrite" });
    statusElement.textContent = permission === "granted"
      ? `当前记录目录：${handle.name}`
      : `已保存目录 ${handle.name}，但缺少写权限，请重新设置。`;
  })
  .catch((error) => { statusElement.textContent = error?.message ?? "无法读取目录设置。"; });
