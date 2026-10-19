const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const nodeCrypto = require('node:crypto');

const extension = path.join(__dirname, '../browser-extension/yalo-note-it');

function source(name) {
  return fs.readFileSync(path.join(extension, name), 'utf8');
}

function notFound() {
  return Object.assign(new Error('missing'), { name: 'NotFoundError' });
}

function directory(children = {}, files = {}, name = '', accesses = []) {
  return {
    kind: 'directory',
    name,
    async getDirectoryHandle(entryName, options) {
      accesses.push({ type: 'directory', name: entryName, create: options?.create === true });
      if (options?.create) throw new Error('read path attempted to create a directory');
      if (!(entryName in children)) throw notFound();
      return children[entryName];
    },
    async getFileHandle(entryName, options) {
      accesses.push({ type: 'file', name: entryName, create: options?.create === true });
      if (options?.create) throw new Error('read path attempted to create a file');
      if (!(entryName in files)) throw notFound();
      const contents = files[entryName];
      const bytes = Buffer.isBuffer(contents) ? contents : Buffer.from(contents);
      return {
        getFile: async () => ({
          size: bytes.byteLength,
          text: async () => bytes.toString('utf8'),
          arrayBuffer: async () => bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength),
        }),
      };
    },
  };
}

async function archiveAccessCase() {
  const conversationId = '6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be';
  const sourceUrl = `https://chatgpt.com/g/project/c/${conversationId}`;
  const accesses = [];
  const context = vm.createContext({ console, Buffer, crypto: nodeCrypto.webcrypto });
  context.self = context;
  context.YaloNoteProviders = [{
    id: 'chatgpt',
    label: 'ChatGPT',
    matchesUrl: (url) => /^https:\/\/chatgpt\.com\//u.test(url),
    conversationId: (url) => new URL(url).pathname.match(/\/c\/([^/]+)/u)?.[1] ?? null,
  }];
  vm.runInContext(source('archive-access.js'), context);

  const manifest = JSON.stringify({
    schemaVersion: 1,
    provider: 'chatgpt',
    conversationId,
    sourceUrl,
    exportedAt: '2026-10-08T00:00:00.000Z',
    artifacts: [],
  });
  const location = JSON.stringify({
    schemaVersion: 1,
    kind: 'yalo-note-archive-root',
    archive_root: 'F:\\My Files\\石#\\literal%25\\%2e%2e\\YaloNote',
  });
  const conversation = directory({}, {
    'manifest.json': manifest,
    'conversation.raw-record.md': '# Raw Record\n',
  }, '', accesses);
  const root = directory({
    sessions: directory({
      chatgpt: directory({ [conversationId]: conversation }, {}, '', accesses),
    }, {}, '', accesses),
  }, { '.yalo-note-location.json': location }, 'YaloNote', accesses);

  const archive = await context.YaloArchiveAccess.readConversationArchive(
    root,
    'chatgpt',
    conversationId,
  );
  assert.equal(archive.manifest.sourceUrl, sourceUrl);
  assert.equal(
    await context.YaloArchiveAccess.conversationDirectoryFileUrl(root, 'chatgpt', conversationId),
    `file:///F:/My%20Files/%E7%9F%B3%23/literal%2525/%252e%252e/YaloNote/sessions/chatgpt/${conversationId}/`,
  );
  assert.equal(accesses.some((entry) => entry.create), false, 'Archive lookup must stay read-only');

  const v2Body = '# Content-addressed Raw Record\n';
  const v2Digest = nodeCrypto.createHash('sha256').update(v2Body).digest('hex');
  const v2Asset = Buffer.from('captured image');
  const v2AssetDigest = nodeCrypto.createHash('sha256').update(v2Asset).digest('hex');
  const v2Manifest = JSON.stringify({
    schemaVersion: 2,
    provider: 'chatgpt',
    conversationId,
    sourceUrl,
    exportedAt: '2026-10-09T00:00:00.000Z',
    record: {
      localPath: `records/${v2Digest}.md`,
      size: Buffer.byteLength(v2Body),
      sha256: v2Digest,
    },
    artifacts: [{
      key: 'generated-image-1',
      messageId: 'assistant-1',
      displayName: 'result.png',
      kind: 'generated',
      status: 'saved',
      capturedAt: '2026-10-09T00:00:00.000Z',
      localPath: 'assets/result.png',
      mimeType: 'image/png',
      size: v2Asset.byteLength,
      sha256: v2AssetDigest,
    }],
  });
  const v2Conversation = directory({
    records: directory({}, { [`${v2Digest}.md`]: v2Body }, '', accesses),
    assets: directory({}, { 'result.png': v2Asset }, '', accesses),
  }, { 'manifest.json': v2Manifest }, '', accesses);
  const v2Root = directory({
    sessions: directory({
      chatgpt: directory({ [conversationId]: v2Conversation }, {}, '', accesses),
    }, {}, '', accesses),
  }, { '.yalo-note-location.json': location }, 'YaloNote', accesses);
  const v2Archive = await context.YaloArchiveAccess.readConversationArchive(
    v2Root,
    'chatgpt',
    conversationId,
  );
  assert.equal(await v2Archive.rawFile.text(), v2Body);

  const missingAssetConversation = directory({
    records: directory({}, { [`${v2Digest}.md`]: v2Body }, '', accesses),
    assets: directory({}, {}, '', accesses),
  }, { 'manifest.json': v2Manifest }, '', accesses);
  const missingAssetRoot = directory({
    sessions: directory({
      chatgpt: directory({ [conversationId]: missingAssetConversation }, {}, '', accesses),
    }, {}, '', accesses),
  }, { '.yalo-note-location.json': location }, 'YaloNote', accesses);
  await assert.rejects(
    context.YaloArchiveAccess.readConversationArchive(missingAssetRoot, 'chatgpt', conversationId),
    (error) => error.code === 'incomplete',
  );

  const corruptAssetConversation = directory({
    records: directory({}, { [`${v2Digest}.md`]: v2Body }, '', accesses),
    assets: directory({}, { 'result.png': Buffer.from('tampered image') }, '', accesses),
  }, { 'manifest.json': v2Manifest }, '', accesses);
  const corruptAssetRoot = directory({
    sessions: directory({
      chatgpt: directory({ [conversationId]: corruptAssetConversation }, {}, '', accesses),
    }, {}, '', accesses),
  }, { '.yalo-note-location.json': location }, 'YaloNote', accesses);
  await assert.rejects(
    context.YaloArchiveAccess.readConversationArchive(corruptAssetRoot, 'chatgpt', conversationId),
    (error) => error.code === 'incomplete',
  );

  const corruptConversation = directory({
    records: directory({}, { [`${v2Digest}.md`]: `${v2Body}tampered` }, '', accesses),
  }, { 'manifest.json': v2Manifest }, '', accesses);
  const corruptRoot = directory({
    sessions: directory({
      chatgpt: directory({ [conversationId]: corruptConversation }, {}, '', accesses),
    }, {}, '', accesses),
  }, { '.yalo-note-location.json': location }, 'YaloNote', accesses);
  await assert.rejects(
    context.YaloArchiveAccess.readConversationArchive(corruptRoot, 'chatgpt', conversationId),
    (error) => error.code === 'incomplete',
  );

  await assert.rejects(
    context.YaloArchiveAccess.readConversationArchive(root, 'chatgpt', '../secret'),
    (error) => error.code === 'invalid-context',
  );
  const missingRoot = directory({
    sessions: directory({ chatgpt: directory({}, {}, '', accesses) }, {}, '', accesses),
  }, {}, 'YaloNote', accesses);
  await assert.rejects(
    context.YaloArchiveAccess.readConversationArchive(missingRoot, 'chatgpt', conversationId),
    (error) => error.code === 'not-found',
  );
}

function fakeIndexedDb() {
  const values = new Map();
  const database = {
    objectStoreNames: { contains: () => true },
    close() {},
    transaction(_storeName, _mode) {
      const transaction = {
        error: null,
        objectStore() {
          return {
            get(key) {
              const request = {};
              queueMicrotask(() => {
                request.result = values.get(key);
                transaction.oncomplete?.();
              });
              return request;
            },
            put(value, key) {
              const request = {};
              queueMicrotask(() => {
                values.set(key, value);
                transaction.oncomplete?.();
              });
              return request;
            },
            delete(key) {
              const request = {};
              queueMicrotask(() => {
                values.delete(key);
                transaction.oncomplete?.();
              });
              return request;
            },
          };
        },
      };
      return transaction;
    },
  };
  return {
    open() {
      const request = {};
      queueMicrotask(() => {
        request.result = database;
        request.onsuccess?.();
      });
      return request;
    },
  };
}

async function archiveSettingsCase() {
  const context = vm.createContext({ console, indexedDB: fakeIndexedDb(), queueMicrotask });
  context.self = context;
  vm.runInContext(source('archive-settings.js'), context);
  const settings = context.YaloArchiveSettings;
  assert.deepEqual(Object.keys(settings).sort(), [
    'clearArchiveRoot',
    'loadArchiveRoot',
    'saveArchiveRoot',
  ]);
  const handle = { kind: 'directory', name: 'YaloNote' };
  await settings.saveArchiveRoot(handle);
  assert.equal((await settings.loadArchiveRoot()).name, 'YaloNote');
  await settings.clearArchiveRoot();
  assert.equal(await settings.loadArchiveRoot(), null);
  await assert.rejects(
    settings.saveArchiveRoot({ kind: 'file' }),
    (error) => error.name === 'TypeError' && /目录句柄/u.test(error.message),
  );
}

class FakeElement {
  constructor() {
    this.disabled = false;
    this.textContent = '';
    this.dataset = {};
    this.listeners = {};
    this.title = '';
  }

  addEventListener(type, listener) {
    this.listeners[type] = listener;
  }

  async click() {
    return this.listeners.click();
  }
}

async function popupCase() {
  const elements = Object.fromEntries([
    '#status',
    '#record',
    '#stop',
    '#open-archive',
    '#configure',
  ].map((selector) => [selector, new FakeElement()]));
  const buttons = Object.values(elements).filter((element) => element !== elements['#status']);
  const events = [];
  const opened = [];
  const conversationId = '6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be';
  const target = {
    id: 17,
    windowId: 73,
    url: `https://chatgpt.com/g/project/c/${conversationId}`,
  };
  const root = { name: 'YaloNote', kind: 'directory', queryPermission: async () => 'granted' };
  let mode = 'existing';
  const context = vm.createContext({
    console,
    URLSearchParams,
    location: { search: '?targetTabId=17' },
    setInterval: () => 1,
    crypto: { randomUUID: () => 'test' },
    document: {
      querySelector: (selector) => elements[selector],
      querySelectorAll: (selector) => selector === 'button' ? buttons : [],
    },
    window: { showDirectoryPicker: async () => { throw new Error('not used'); } },
    chrome: {
      tabs: {
        get: async (tabId) => {
          assert.equal(tabId, 17);
          events.push('target');
          return target;
        },
        query: async () => { throw new Error('popup must not query its own active tab'); },
        create: async (options) => {
          events.push('open');
          opened.push(options);
        },
      },
      extension: {
        isAllowedFileSchemeAccess: async () => {
          events.push('file-access');
          return mode !== 'file-denied';
        },
      },
      runtime: { sendMessage: async (message) => {
        assert.equal(message.tabId, 17);
        return null;
      } },
    },
  });
  context.self = context;
  context.YaloArchiveSettings = { loadArchiveRoot: async () => root, saveArchiveRoot: async () => {} };
  context.YaloArchiveAccess = {
    contextForUrl: (url) => mode === 'unsupported' ? null : ({
      providerId: 'chatgpt',
      conversationId,
      pageUrl: url,
    }),
    ensureReadable: async () => events.push('permission'),
    readConversationArchive: async () => {
      events.push('lookup');
      if (mode === 'missing') throw new Error('当前会话尚无归档。');
      return {};
    },
    conversationDirectoryFileUrl: async () => {
      events.push('location');
      return `file:///F:/YaloNote/sessions/chatgpt/${conversationId}/`;
    },
    readArchiveLocation: async () => 'F:\\YaloNote',
  };
  vm.runInContext(source('popup.js'), context);
  await new Promise((resolve) => setImmediate(resolve));

  const requestedFolderNames = [];
  context.testBase = {
    name: 'Archive parent',
    async getDirectoryHandle(name, options) {
      requestedFolderNames.push(name);
      assert.equal(options, undefined, 'Directory selection must not create a Codex archive root');
      if (name === 'YaloNote') return root;
      throw notFound();
    },
  };
  const configured = await vm.runInContext('configuredArchiveRoot(testBase)', context);
  assert.equal(configured, root);
  assert.deepEqual(requestedFolderNames, ['YaloNote']);

  await elements['#open-archive'].click();
  assert.equal(opened.length, 1);
  assert.equal(opened[0].windowId, 73, 'Archive must open beside the original conversation');
  assert.match(opened[0].url, /^file:\/\/\/F:\//u);
  assert.ok(events.indexOf('permission') < events.indexOf('lookup'));
  assert.ok(events.indexOf('lookup') < events.indexOf('location'));
  assert.ok(events.indexOf('location') < events.indexOf('file-access'));
  assert.ok(events.indexOf('file-access') < events.indexOf('open'));

  mode = 'missing';
  await elements['#open-archive'].click();
  assert.equal(opened.length, 1, 'A missing archive must not open a guessed path');
  assert.match(elements['#status'].textContent, /尚无归档/u);
  assert.equal(elements['#status'].dataset.kind, 'error');

  vm.runInContext('updateTaskState({ running: true, phase: "scanning" });', context);
  assert.equal(elements['#stop'].disabled, false, 'Scanning can be stopped safely');
  vm.runInContext('updateTaskState({ running: true, phase: "writing" });', context);
  assert.equal(elements['#stop'].disabled, true, 'Writing must not expose an unsafe stop action');
  assert.match(elements['#stop'].title, /不能安全停止/u);
}

(async () => {
  await archiveAccessCase();
  await archiveSettingsCase();
  await popupCase();
  const manifest = JSON.parse(source('manifest.json'));
  assert.equal(manifest.action.default_popup, undefined);
  assert.ok(manifest.optional_host_permissions.includes('file:///*'));
  console.log('Archive access and task-window UI regression checks passed');
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
