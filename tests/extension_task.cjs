const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const extensionRoot = path.join(__dirname, '../browser-extension/yalo-note-it');

class FakeElement {
  constructor() {
    this.disabled = false;
    this.textContent = '';
    this.dataset = {};
    this.title = '';
  }

  addEventListener() {}
}

(async () => {
  const elements = Object.fromEntries([
    '#status',
    '#record',
    '#stop',
    '#open-archive',
    '#configure',
  ].map(selector => [selector, new FakeElement()]));
  const buttons = Object.values(elements).filter(element => element !== elements['#status']);
  const sent = [];
  const target = {
    id: 42,
    windowId: 7,
    url: 'https://chatgpt.com/c/6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be',
  };
  const temporaryFile = {
    createWritable: async () => ({ write: async () => {}, close: async () => {} }),
  };
  const archiveRoot = {
    kind: 'directory',
    name: 'YaloNote',
    queryPermission: async () => 'granted',
    requestPermission: async () => 'granted',
    getFileHandle: async () => temporaryFile,
    removeEntry: async () => {},
  };
  const context = vm.createContext({
    URLSearchParams,
    console,
    crypto: { randomUUID: () => 'test-id' },
    location: { search: '?targetTabId=42' },
    setInterval: () => 1,
    document: {
      querySelector: selector => elements[selector],
      querySelectorAll: selector => selector === 'button' ? buttons : [],
    },
    window: { showDirectoryPicker: async () => { throw new Error('not used'); } },
    chrome: {
      tabs: {
        get: async tabId => {
          assert.equal(tabId, 42);
          return target;
        },
        query: async () => { throw new Error('Task UI must not select its own active tab'); },
      },
      runtime: {
        sendMessage: async message => {
          sent.push(message);
          if (message.mode === 'status') return null;
          return {
            ok: false,
            running: false,
            phase: 'finished',
            message: 'Permission expired at write time',
          };
        },
      },
    },
  });
  context.self = context;
  context.YaloArchiveSettings = { loadArchiveRoot: async () => archiveRoot };
  context.YaloArchiveAccess = {
    contextForUrl: () => ({ providerId: 'chatgpt', conversationId: 'test' }),
    readArchiveLocation: async () => 'F:\\YaloNote',
  };

  vm.runInContext(
    fs.readFileSync(path.join(extensionRoot, 'popup.js'), 'utf8'),
    context,
  );
  await new Promise(resolve => setImmediate(resolve));
  await context.control('record');

  const recordMessage = sent.find(message => message.mode === 'record');
  assert.equal(recordMessage.tabId, 42);
  assert.equal(recordMessage.pageUrl, target.url);
  assert.match(elements['#status'].textContent, /Permission expired/);
  assert.equal(elements['#status'].dataset.kind, 'error');
  assert.equal(elements['#record'].disabled, false);

  vm.runInContext("operationState = 'initializing'", context);
  context.updateTaskState({ running: true, phase: 'scanning' });
  assert.equal(vm.runInContext('operationState', context), 'recording');
  context.updateTaskState({ running: false, phase: 'finished' });
  assert.equal(vm.runInContext('operationState', context), 'idle');
  assert.equal(elements['#record'].disabled, false, 'A reloaded task window must unlock after completion');

  console.log('Task window and error reporting regression checks passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
