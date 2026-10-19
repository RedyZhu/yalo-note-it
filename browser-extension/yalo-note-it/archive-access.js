(() => {
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
          "当前记录目录缺少 Codex 路径配置，请先在 Codex 中配置该 YaloNote 目录。",
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
    providerForArchive(providerId, conversationId);
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
        throw new ArchiveLookupError("incomplete", `当前会话归档不完整：缺少 ${name}。`, error);
      }
      throw error;
    }
  }

  async function sha256(file) {
    const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
    return [...new Uint8Array(digest)]
      .map((byte) => byte.toString(16).padStart(2, "0"))
      .join("");
  }

  async function archivedRecordFile(directory, manifest) {
    if (manifest.schemaVersion === 1) {
      return (await requiredFile(directory, "conversation.raw-record.md")).getFile();
    }
    const record = manifest.record;
    const match = typeof record?.localPath === "string"
      ? record.localPath.match(/^records\/([0-9a-f]{64})\.md$/u)
      : null;
    if (!match
        || record.sha256 !== match[1]
        || !Number.isSafeInteger(record.size)
        || record.size <= 0) {
      throw new ArchiveLookupError("invalid-manifest", "当前会话归档的正文索引无效。");
    }
    let recordsDirectory;
    try {
      recordsDirectory = await directory.getDirectoryHandle("records");
    } catch (error) {
      if (notFound(error)) {
        throw new ArchiveLookupError("incomplete", "当前会话归档不完整：缺少 records。", error);
      }
      throw error;
    }
    const file = await (await requiredFile(recordsDirectory, `${match[1]}.md`)).getFile();
    if (file.size !== record.size || await sha256(file) !== record.sha256) {
      throw new ArchiveLookupError("incomplete", "当前会话归档正文与 manifest 校验值不一致。");
    }
    return file;
  }

  async function validateArtifacts(directory, manifest) {
    if (!Array.isArray(manifest.artifacts)) {
      throw new ArchiveLookupError("invalid-manifest", "当前会话归档的附件索引无效。");
    }
    let assetsDirectory = null;
    for (const artifact of manifest.artifacts) {
      if (!artifact || typeof artifact !== "object"
          || !["saved", "unavailable", "too_large"].includes(artifact.status)) {
        throw new ArchiveLookupError("invalid-manifest", "当前会话归档的附件状态无效。");
      }
      if (artifact.status !== "saved") continue;
      const match = typeof artifact.localPath === "string"
        ? artifact.localPath.match(/^assets\/([^/\\\u0000-\u001f]+)$/u)
        : null;
      if (!match || match[1] === "." || match[1] === ".."
          || !/^[0-9a-f]{64}$/u.test(artifact.sha256 ?? "")
          || !Number.isSafeInteger(artifact.size) || artifact.size < 0) {
        throw new ArchiveLookupError("invalid-manifest", "当前会话归档的附件索引无效。");
      }
      if (!assetsDirectory) {
        try {
          assetsDirectory = await directory.getDirectoryHandle("assets");
        } catch (error) {
          if (notFound(error)) {
            throw new ArchiveLookupError("incomplete", "当前会话归档不完整：缺少 assets。", error);
          }
          throw error;
        }
      }
      const file = await (await requiredFile(assetsDirectory, match[1])).getFile();
      if (file.size !== artifact.size || await sha256(file) !== artifact.sha256) {
        throw new ArchiveLookupError("incomplete", "当前会话归档附件与 manifest 校验值不一致。");
      }
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
    if (![1, 2].includes(manifest?.schemaVersion)
        || manifest.provider !== providerId
        || manifest.conversationId !== conversationId
        || sourceConversationId !== conversationId
        || !Number.isFinite(exportedAt)) {
      throw new ArchiveLookupError(
        "identity-mismatch",
        "当前会话归档的身份信息与页面 URL 不匹配。",
      );
    }

    const rawFile = await archivedRecordFile(directory, manifest);
    if (!rawFile.size) {
      throw new ArchiveLookupError("incomplete", "当前会话归档的 Raw Record 为空。");
    }
    if (manifest.schemaVersion === 2) await validateArtifacts(directory, manifest);
    return { directory, manifest, rawFile };
  }

  self.YaloArchiveAccess = Object.freeze({
    ArchiveLookupError,
    conversationDirectoryFileUrl,
    contextForUrl,
    ensureReadable,
    readArchiveLocation,
    readConversationArchive,
  });
})();
