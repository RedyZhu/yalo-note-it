(() => {
  const DATABASE_NAME = "yalo-note-it";
  const DATABASE_VERSION = 1;
  const SETTINGS_STORE = "settings";
  const ARCHIVE_ROOT_KEY = "archive-root";

  async function openSettingsDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(SETTINGS_STORE)) {
          request.result.createObjectStore(SETTINGS_STORE);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
      request.onblocked = () => reject(new Error("记录目录设置数据库正在被其他页面占用。"));
    });
  }

  async function withSettingsStore(mode, operation) {
    const database = await openSettingsDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const transaction = database.transaction(SETTINGS_STORE, mode);
        const request = operation(transaction.objectStore(SETTINGS_STORE));
        transaction.oncomplete = () => resolve(request?.result ?? null);
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally {
      database.close();
    }
  }

  async function loadArchiveRoot() {
    return withSettingsStore("readonly", (store) => store.get(ARCHIVE_ROOT_KEY));
  }

  async function saveArchiveRoot(handle) {
    if (!handle || handle.kind !== "directory") {
      throw new TypeError("记录目录必须是一个目录句柄。");
    }
    await withSettingsStore("readwrite", (store) => store.put(handle, ARCHIVE_ROOT_KEY));
  }

  async function clearArchiveRoot() {
    await withSettingsStore("readwrite", (store) => store.delete(ARCHIVE_ROOT_KEY));
  }

  self.YaloArchiveSettings = Object.freeze({
    clearArchiveRoot,
    loadArchiveRoot,
    saveArchiveRoot,
  });
})();
