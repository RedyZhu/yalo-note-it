(() => {
  const DATABASE_NAME = "yalo-note-it";
  const DATABASE_VERSION = 1;
  const SETTINGS_STORE = "settings";
  const ARCHIVE_ROOT_KEY = "archive-root";
  const ARCHIVE_LOCATION_FILE = ".yalo-note-location.json";
  const ARCHIVE_LOCATION_KIND = "yalo-note-archive-root";
  const ARCHIVE_LOCATION_SCHEMA_VERSION = 1;
  const MAX_ARCHIVE_LOCATION_BYTES = 16 * 1024;

  class ArchiveLookupError extends Error {
    constructor(code, message, cause = null) {
      super(message);
      this.name = "ArchiveLookupError";
      this.code = code;
      if (cause) this.cause = cause;
    }
  }

  async function openSettingsDatabase() {
    return new Promise((resolve, reject) => {
      const request = indexedDB.open(DATABASE_NAME, DATABASE_VERSION);
      request.onupgradeneeded = () => request.result.createObjectStore(SETTINGS_STORE);
      request.onsuccess = () => resolve(request.result);
      request.onerror = () => reject(request.error);
    });
  }

  async function readArchiveRoot() {
    const database = await openSettingsDatabase();
    try {
      return await new Promise((resolve, reject) => {
        const request = database.transaction(SETTINGS_STORE, "readonly")
          .objectStore(SETTINGS_STORE).get(ARCHIVE_ROOT_KEY);
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
        const transaction = database.transaction(SETTINGS_STORE, "readwrite");
        transaction.objectStore(SETTINGS_STORE).put(handle, ARCHIVE_ROOT_KEY);
        transaction.oncomplete = resolve;
        transaction.onerror = () => reject(transaction.error);
        transaction.onabort = () => reject(transaction.error);
      });
    } finally {
      database.close();
    }
  }

  async function ensureReadable(handle) {
    let permission = await handle.queryPermission({ mode: "read" });
    if (permission !== "granted") {
      permission = await handle.requestPermission({ mode: "read" });
    }
    if (permission !== "granted") {
      throw new ArchiveLookupError(
        "permission-denied",
        "浏览器未授予记录目录读取权限，请重新授权或设置本地目录。",
      );
    }
  }

  function providerById(providerId) {
    return (self.YaloNoteProviders ?? [])
      .find((provider) => provider.id === providerId) ?? null;
  }

  function contextForUrl(url) {
    const provider = (self.YaloNoteProviders ?? [])
      .find((candidate) => candidate.matchesUrl(url)) ?? null;
    if (!provider) return null;
    return {
      providerId: provider.id,
      providerLabel: provider.label,
      conversationId: provider.conversationId(url),
      pageUrl: url,
    };
  }

  function validPathSegment(value) {
    return typeof value === "string"
      && value.length > 0
      && value !== "."
      && value !== ".."
      && !/[\\/\u0000-\u001f]/u.test(value);
  }

  function notFound(error) {
    return error?.name === "NotFoundError";
  }

  function providerForArchive(providerId, conversationId) {
    const provider = providerById(providerId);
    if (!provider || !validPathSegment(providerId) || !validPathSegment(conversationId)) {
      throw new ArchiveLookupError("invalid-context", "当前页面没有可定位的稳定会话 ID。");
    }
    return provider;
  }

  function normalizeWindowsArchiveRoot(value) {
    if (typeof value !== "string" || !value || value.length > 1024
        || /[\u0000-\u001f]/u.test(value)) {
      return null;
    }
    const withoutTrailingSeparators = value.replace(/[\\/]+$/u, "");
    const match = withoutTrailingSeparators.match(/^([a-z]):[\\/](.+)$/iu);
    if (!match) return null;
    const segments = match[2].split(/[\\/]+/u);
    if (!segments.length || segments.some((segment) =>
      !segment || segment === "." || segment === ".." || /[<>:"|?*]/u.test(segment))) {
      return null;
    }
    return `${match[1].toUpperCase()}:\\${segments.join("\\")}`;
  }

  async function readArchiveLocation(root) {
    let handle;
    try {
      handle = await root.getFileHandle(ARCHIVE_LOCATION_FILE);
    } catch (error) {
      if (notFound(error)) {
        throw new ArchiveLookupError(
          "location-missing",
          "当前记录目录缺少 Codex 路径配置，请先用 Codex 配置或归档到同一 YaloNote 目录。",
          error,
        );
      }
      throw error;
    }

    const file = await handle.getFile();
    if (!file.size || file.size > MAX_ARCHIVE_LOCATION_BYTES) {
      throw new ArchiveLookupError("location-invalid", "Codex 路径配置无效，无法生成本地文件地址。");
    }
    let config;
    try {
      config = JSON.parse(await file.text());
    } catch (error) {
      throw new ArchiveLookupError("location-invalid", "Codex 路径配置无效，无法生成本地文件地址。", error);
    }
    const archiveRoot = normalizeWindowsArchiveRoot(config?.archive_root);
    const configuredName = archiveRoot?.split("\\").at(-1) ?? null;
    if (config?.schemaVersion !== ARCHIVE_LOCATION_SCHEMA_VERSION
        || config?.kind !== ARCHIVE_LOCATION_KIND
        || !archiveRoot
        || typeof root?.name !== "string"
        || configuredName?.toLocaleLowerCase() !== root.name.toLocaleLowerCase()) {
      throw new ArchiveLookupError("location-invalid", "Codex 路径配置与当前记录目录不匹配。");
    }
    return archiveRoot;
  }

  async function conversationDirectoryFileUrl(root, providerId, conversationId) {
    providerForArchive(providerId, conversationId);
    const archiveRoot = await readArchiveLocation(root);
    const [drive, ...rootSegments] = archiveRoot.split("\\");
    const encodedPath = [...rootSegments, "sessions", providerId, conversationId]
      .map((segment) => encodeURIComponent(segment))
      .join("/");
    return `file:///${drive}/${encodedPath}/`;
  }

  async function archiveDirectory(root, providerId, conversationId) {
    let directory = root;
    try {
      for (const name of ["sessions", providerId, conversationId]) {
        directory = await directory.getDirectoryHandle(name);
      }
      return directory;
    } catch (error) {
      if (notFound(error)) {
        throw new ArchiveLookupError("not-found", "当前会话尚无归档。", error);
      }
      throw error;
    }
  }

  async function requiredFile(directory, name) {
    try {
      return await directory.getFileHandle(name);
    } catch (error) {
      if (notFound(error)) {
        throw new ArchiveLookupError(
          "incomplete",
          `当前会话归档不完整：缺少 ${name}。`,
          error,
        );
      }
      throw error;
    }
  }

  async function readConversationArchive(root, providerId, conversationId) {
    const provider = providerForArchive(providerId, conversationId);

    const directory = await archiveDirectory(root, providerId, conversationId);
    const manifestHandle = await requiredFile(directory, "manifest.json");
    let manifest;
    try {
      manifest = JSON.parse(await (await manifestHandle.getFile()).text());
    } catch (error) {
      throw new ArchiveLookupError("invalid-manifest", "当前会话归档的 manifest.json 无法读取。", error);
    }

    const sourceConversationId = typeof manifest?.sourceUrl === "string"
      && provider.matchesUrl(manifest.sourceUrl)
      ? provider.conversationId(manifest.sourceUrl)
      : null;
    const exportedAt = typeof manifest?.exportedAt === "string"
      ? Date.parse(manifest.exportedAt)
      : Number.NaN;
    if (manifest?.schemaVersion !== 1
        || manifest.provider !== providerId
        || manifest.conversationId !== conversationId
        || sourceConversationId !== conversationId
        || !Number.isFinite(exportedAt)) {
      throw new ArchiveLookupError(
        "identity-mismatch",
        "当前会话归档的身份信息与页面 URL 不匹配。",
      );
    }

    const rawHandle = await requiredFile(directory, "conversation.raw-record.md");
    const rawFile = await rawHandle.getFile();
    if (!rawFile.size) {
      throw new ArchiveLookupError("incomplete", "当前会话归档的 Raw Record 为空。");
    }
    return { directory, manifest, rawFile };
  }

  self.YaloArchiveAccess = Object.freeze({
    ArchiveLookupError,
    conversationDirectoryFileUrl,
    contextForUrl,
    ensureReadable,
    readArchiveLocation,
    readArchiveRoot,
    readConversationArchive,
    saveArchiveRoot,
  });
})();
