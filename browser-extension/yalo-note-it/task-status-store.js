(function registerTaskStatusStore(scope) {
  const DATABASE_NAME = "yalo-note-it";
  const DATABASE_VERSION = 1;
  const STORE_NAME = "settings";
  const KEY_PREFIX = "task-status:";

  async function openDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => {
        if (!request.result.objectStoreNames.contains(STORE_NAME)) {
          request.result.createObjectStore(STORE_NAME);
        }
      };
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  function statusKey(tabId, contextIdentity) {
    if (!Number.isInteger(tabId) || tabId <= 0) {
      throw new Error("无效的标签页 ID。");
    }
    if (typeof contextIdentity !== "string" || !contextIdentity) {
      throw new Error("当前页面没有可识别的记录上下文。");
    }
    return `${KEY_PREFIX}${tabId}:${contextIdentity}`;
  }

  async function read(tabId, contextIdentity) {
    const database = await openDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const request = database.transaction(STORE_NAME, "readonly")
          .objectStore(STORE_NAME).get(statusKey(tabId, contextIdentity));
        request.onsuccess = () => resolve(request.result ?? null);
        request.onerror = () => reject(request.error);
      });
    } finally {
      database.close();
    }
  }

  async function write(tabId, contextIdentity, value) {
    const database = await openDatabase();
    try {
      await new Promise((resolve, reject) => {
        const transaction = database.transaction(STORE_NAME, "readwrite");
        transaction.objectStore(STORE_NAME).put(value, statusKey(tabId, contextIdentity));
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
      return value;
    } finally {
      database.close();
    }
  }

  scope.YaloTaskStatusStore = Object.freeze({ read, write });
})(self);
