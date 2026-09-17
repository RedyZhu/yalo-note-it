const statusElement = document.querySelector("#status");
const ARCHIVE_FOLDER_NAME = "YaloNote";
const RECOGNIZED_ARCHIVE_FOLDER_NAMES = new Set(["yalonote", "yalo note", "yalo-note"]);

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

async function copyDirectory(source, destination) {
  for await (const [name, sourceEntry] of source.entries()) {
    if (sourceEntry.kind === "directory") {
      const targetDirectory = await destination.getDirectoryHandle(name, { create: true });
      await copyDirectory(sourceEntry, targetDirectory);
      continue;
    }
    const sourceFile = await sourceEntry.getFile();
    let targetFile;
    try {
      targetFile = await destination.getFileHandle(name);
      const existing = await targetFile.getFile();
      const sourceBytes = new Uint8Array(await sourceFile.arrayBuffer());
      const existingBytes = new Uint8Array(await existing.arrayBuffer());
      const identical = sourceBytes.length === existingBytes.length
        && sourceBytes.every((byte, index) => byte === existingBytes[index]);
      if (!identical) {
        throw new Error(`迁移目标存在同名冲突文件：${name}`);
      }
      continue;
    } catch (error) {
      if (error?.name !== "NotFoundError") throw error;
    }
    targetFile = await destination.getFileHandle(name, { create: true });
    const writable = await targetFile.createWritable();
    const contents = await sourceFile.arrayBuffer();
    await writable.write(contents);
    await writable.close();
    const copied = new Uint8Array(await (await targetFile.getFile()).arrayBuffer());
    const original = new Uint8Array(contents);
    if (copied.length !== original.length
        || !original.every((byte, index) => byte === copied[index])) {
      throw new Error(`迁移文件校验失败：${name}`);
    }
  }
}

async function removeDirectoryContents(directory) {
  for await (const [name, entry] of directory.entries()) {
    await directory.removeEntry(name, { recursive: entry.kind === "directory" });
  }
}

async function assertDirectoriesDoNotContainEachOther(previous, next) {
  const nextInsidePrevious = await previous.resolve(next);
  const previousInsideNext = await next.resolve(previous);
  if (nextInsidePrevious !== null || previousInsideNext !== null) {
    throw new Error("新旧记录目录不能互相包含，请选择旧目录之外的独立位置。");
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
    if (mode === "record") {
      const handle = await readArchiveRoot();
      if (!handle) throw new Error("尚未设置目录，请先录入一个明确的本地路径。");
      statusElement.textContent = "正在确认记录目录权限与写入能力…";
      await verifyWritable(handle);
      statusElement.textContent = "正在读取对话，请保持此窗口打开…";
    }
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
  try {
    const previous = await readArchiveRoot();
    const base = await window.showDirectoryPicker({ id: "yalo-note-base", mode: "readwrite" });
    const handle = RECOGNIZED_ARCHIVE_FOLDER_NAMES.has(base.name.toLocaleLowerCase())
      ? base
      : await base.getDirectoryHandle(ARCHIVE_FOLDER_NAME, { create: true });
    statusElement.textContent = "正在验证目录读写权限…";
    await verifyWritable(handle);
    if (previous && !(await previous.isSameEntry(handle))) {
      statusElement.textContent = "正在确认原记录目录权限与写入能力…";
      await verifyWritable(previous);
      await assertDirectoriesDoNotContainEachOther(previous, handle);
      const migrate = window.confirm("检测到原有记录目录。是否把原有内容全部迁移到新路径？\n\n选择“确定”迁移；选择“取消”则旧内容保留在原路径，新记录写入新路径。");
      if (migrate) {
        statusElement.textContent = "正在迁移原有记录，请勿关闭窗口…";
        await copyDirectory(previous, handle);
        await saveArchiveRoot(handle);
        try {
          await removeDirectoryContents(previous);
        } catch (error) {
          throw new Error(`内容已迁移且新路径已启用，但旧目录清理失败：${error?.message ?? "未知错误"}`);
        }
      }
    }
    await saveArchiveRoot(handle);
    statusElement.textContent = `目录已验证并保存：${handle.name}`;
  } catch (error) {
    if (error?.name !== "AbortError") {
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
      statusElement.textContent = "尚未设置目录，请录入一个明确的本地路径。";
      return;
    }
    const permission = await handle.queryPermission({ mode: "readwrite" });
    statusElement.textContent = permission === "granted"
      ? `当前记录目录：${handle.name}`
      : `已保存目录 ${handle.name}；点击 Yalo note it 时会请求恢复写权限。`;
  })
  .catch((error) => { statusElement.textContent = error?.message ?? "无法读取目录设置。"; });
