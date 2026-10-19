const assert = require('node:assert/strict');
const cryptoModule = require('node:crypto');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const extensionRoot = path.join(__dirname, '../browser-extension/yalo-note-it');

function notFound(name) {
  const error = new Error(`Missing entry: ${name}`);
  error.name = 'NotFoundError';
  return error;
}

function bytesOf(value) {
  if (typeof value === 'string') return new TextEncoder().encode(value);
  if (value instanceof ArrayBuffer) return new Uint8Array(value.slice(0));
  if (ArrayBuffer.isView(value)) {
    return new Uint8Array(value.buffer.slice(value.byteOffset, value.byteOffset + value.byteLength));
  }
  throw new TypeError(`Unsupported file contents: ${typeof value}`);
}

class MemoryFileHandle {
  constructor(name, parent, state) {
    this.name = name;
    this.parent = parent;
    this.state = state;
    this.bytes = new Uint8Array();
  }

  get fullPath() {
    return this.parent.childPath(this.name);
  }

  async createWritable() {
    let pending = null;
    const file = this;
    return {
      async write(contents) {
        file.state.events.push({ operation: 'write', path: file.fullPath });
        pending = bytesOf(contents);
      },
      async close() {
        file.state.events.push({ operation: 'close', path: file.fullPath });
        file.bytes = pending ?? new Uint8Array();
      },
      async abort(error) {
        file.state.events.push({ operation: 'abort', path: file.fullPath, error });
        pending = null;
      },
    };
  }

  async getFile() {
    const snapshot = this.bytes.slice();
    return {
      async arrayBuffer() {
        return snapshot.buffer.slice(snapshot.byteOffset, snapshot.byteOffset + snapshot.byteLength);
      },
    };
  }
}

class MemoryDirectoryHandle {
  constructor(name = '', parent = null, state = null) {
    this.name = name;
    this.parent = parent;
    this.state = state ?? { events: [] };
    this.directories = new Map();
    this.files = new Map();
  }

  get fullPath() {
    if (!this.parent) return '';
    return this.parent.childPath(this.name);
  }

  childPath(name) {
    return this.fullPath ? `${this.fullPath}/${name}` : name;
  }

  async queryPermission() {
    return 'granted';
  }

  async getDirectoryHandle(name, options = {}) {
    if (this.directories.has(name)) return this.directories.get(name);
    if (!options.create) throw notFound(name);
    const directory = new MemoryDirectoryHandle(name, this, this.state);
    this.directories.set(name, directory);
    return directory;
  }

  async getFileHandle(name, options = {}) {
    if (this.files.has(name)) return this.files.get(name);
    if (!options.create) throw notFound(name);
    const file = new MemoryFileHandle(name, this, this.state);
    this.files.set(name, file);
    return file;
  }
}

async function directoryAt(root, ...segments) {
  let directory = root;
  for (const segment of segments) directory = await directory.getDirectoryHandle(segment);
  return directory;
}

async function fileBytes(directory, name) {
  const file = await directory.getFileHandle(name);
  return new Uint8Array(await (await file.getFile()).arrayBuffer());
}

async function fileText(directory, name) {
  return new TextDecoder().decode(await fileBytes(directory, name));
}

function createContext(root) {
  const eventListeners = {};
  const context = vm.createContext({
    ArrayBuffer,
    TextEncoder,
    Uint8Array,
    URL,
    atob: value => Buffer.from(value, 'base64').toString('binary'),
    console: { error() {} },
    crypto: cryptoModule.webcrypto,
    setTimeout,
    clearTimeout,
    self: {
      YaloArchiveSettings: { loadArchiveRoot: async () => root },
      YaloTaskStatusStore: { read: async () => null, write: async () => null },
      YaloNoteProviders: [],
    },
    importScripts() {},
    chrome: {
      action: {
        onClicked: { addListener: listener => { eventListeners.actionClicked = listener; } },
        setBadgeBackgroundColor: async () => {},
        setBadgeText: async () => {},
      },
      runtime: {
        getURL: name => `chrome-extension://test/${name}`,
        onMessage: { addListener: listener => { eventListeners.message = listener; } },
      },
      scripting: { executeScript: async () => [{ result: null }] },
      tabs: {
        get: async () => { throw new Error('Not available in writer test'); },
        query: async () => [],
        update: async () => {},
        onRemoved: { addListener: listener => { eventListeners.tabRemoved = listener; } },
        onUpdated: { addListener: listener => { eventListeners.tabUpdated = listener; } },
      },
      windows: { create: async () => {}, update: async () => {} },
    },
  });
  vm.runInContext(
    fs.readFileSync(path.join(extensionRoot, 'service-worker.js'), 'utf8'),
    context,
  );
  return context;
}

async function testCommittedArchiveLayout() {
  const root = new MemoryDirectoryHandle();
  const context = createContext(root);
  const firstBytes = Buffer.from('first attachment');
  const secondBytes = Buffer.from('second attachment');
  const artifacts = new Map([
    ['artifact-1', {
      key: 'artifact-1',
      messageId: 'assistant-1',
      displayName: 'result.png',
      kind: 'generated',
      status: 'saved',
      capturedAt: '2026-10-19T00:00:00.000Z',
      mimeType: 'image/png',
      size: firstBytes.byteLength,
      base64: firstBytes.toString('base64'),
    }],
    ['artifact-2', {
      key: 'artifact-2',
      messageId: 'assistant-2',
      displayName: 'result.png',
      kind: 'generated',
      status: 'saved',
      capturedAt: '2026-10-19T00:00:01.000Z',
      mimeType: 'image/png',
      size: secondBytes.byteLength,
      base64: secondBytes.toString('base64'),
    }],
  ]);
  const body = '# Raw Record\n\n中文正文。\n';

  const result = await context.writeWebArchive(
    'chatgpt',
    'conversation-1',
    body,
    artifacts,
    'https://chatgpt.com/c/conversation-1',
  );
  assert.equal(result.canonicalUpdated, true);

  const archive = await directoryAt(root, 'sessions', 'chatgpt', 'conversation-1');
  const assets = await archive.getDirectoryHandle('assets');
  const records = await archive.getDirectoryHandle('records');
  assert.deepEqual(await fileBytes(assets, 'result.png'), new Uint8Array(firstBytes));
  assert.deepEqual(await fileBytes(assets, 'result-2.png'), new Uint8Array(secondBytes));

  const digest = cryptoModule.createHash('sha256').update(body, 'utf8').digest('hex');
  const recordName = `${digest}.md`;
  assert.equal(await fileText(records, recordName), body);
  assert.equal(await fileText(archive, 'conversation.raw-record.md'), body);

  const manifest = JSON.parse(await fileText(archive, 'manifest.json'));
  assert.equal(manifest.schemaVersion, 2);
  assert.deepEqual(manifest.record, {
    localPath: `records/${recordName}`,
    size: Buffer.byteLength(body, 'utf8'),
    sha256: digest,
  });
  assert.deepEqual(
    manifest.artifacts.map(artifact => artifact.localPath),
    ['assets/result.png', 'assets/result-2.png'],
    'Same-name files with different bytes must receive distinct archive paths',
  );

  const closeOrder = root.state.events
    .filter(event => event.operation === 'close')
    .map(event => event.path);
  assert.deepEqual(closeOrder, [
    'sessions/chatgpt/conversation-1/assets/result.png',
    'sessions/chatgpt/conversation-1/assets/result-2.png',
    `sessions/chatgpt/conversation-1/records/${recordName}`,
    'sessions/chatgpt/conversation-1/manifest.json',
    'sessions/chatgpt/conversation-1/conversation.raw-record.md',
  ]);
}

async function testWriteFailureAbortsAndPropagates() {
  const context = createContext(new MemoryDirectoryHandle());
  const failure = new Error('write failed');
  const calls = [];
  const directory = {
    async getFileHandle() {
      return {
        async createWritable() {
          return {
            async write() { calls.push('write'); throw failure; },
            async close() { calls.push('close'); },
            async abort(error) { calls.push(['abort', error]); },
          };
        },
      };
    },
  };

  await assert.rejects(context.writeFile(directory, 'failed.txt', 'body'), error => error === failure);
  assert.deepEqual(calls, ['write', ['abort', failure]]);
}

async function testCloseFailureAbortsAndPropagates() {
  const context = createContext(new MemoryDirectoryHandle());
  const failure = new Error('close failed');
  const calls = [];
  const directory = {
    async getFileHandle() {
      return {
        async createWritable() {
          return {
            async write() { calls.push('write'); },
            async close() { calls.push('close'); throw failure; },
            async abort(error) { calls.push(['abort', error]); },
          };
        },
      };
    },
  };

  await assert.rejects(context.writeFile(directory, 'failed.txt', 'body'), error => error === failure);
  assert.deepEqual(calls, ['write', 'close', ['abort', failure]]);
}

async function testInvalidArtifactStateFailsBeforeCommit() {
  const root = new MemoryDirectoryHandle();
  const context = createContext(root);
  const sourceUrl = 'https://chatgpt.com/c/conversation-1';
  const validArtifact = {
    key: 'artifact-1',
    messageId: 'assistant-1',
    displayName: 'result.png',
    kind: 'generated',
    status: 'saved',
    capturedAt: '2026-10-19T00:00:00.000Z',
    mimeType: 'image/png',
    size: 1,
    base64: Buffer.from('x').toString('base64'),
  };

  await assert.rejects(
    context.writeWebArchive(
      'chatgpt',
      'conversation-1',
      '# Raw Record\n',
      new Map([['artifact-1', { ...validArtifact, status: 'pending' }]]),
      sourceUrl,
    ),
    /捕获状态无效/u,
  );
  await assert.rejects(
    context.writeWebArchive(
      'chatgpt',
      'conversation-1',
      '# Raw Record\n',
      new Map([['artifact-1', { ...validArtifact, base64: undefined }]]),
      sourceUrl,
    ),
    /缺少已保存内容/u,
  );
  await assert.rejects(
    context.writeWebArchive(
      'chatgpt',
      'conversation-1',
      '# Raw Record\n',
      new Map([['different-key', validArtifact]]),
      sourceUrl,
    ),
    /索引键与附件身份不一致/u,
  );
  await assert.rejects(
    context.writeWebArchive(
      'chatgpt',
      'conversation-1',
      '# Raw Record\n',
      new Map([['artifact-1', { ...validArtifact, size: 25 * 1024 * 1024 + 1 }]]),
      sourceUrl,
    ),
    /超过 25 MB/u,
  );

  await assert.rejects(
    root.getDirectoryHandle('sessions'),
    error => error.name === 'NotFoundError',
    'Invalid artifacts must be rejected before archive directories are created',
  );
}

(async () => {
  await testCommittedArchiveLayout();
  await testWriteFailureAbortsAndPropagates();
  await testCloseFailureAbortsAndPropagates();
  await testInvalidArtifactStateFailsBeforeCommit();
  console.log('web archive writer checks passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
