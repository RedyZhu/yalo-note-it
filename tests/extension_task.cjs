const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.join(__dirname, '../browser-extension/yalo-note-it');

async function popupCase(search) {
  const nodes = new Map();
  const events = [];
  const context = vm.createContext({
    URLSearchParams, location: { search }, console,
    document: { querySelector(selector) {
      if (!nodes.has(selector)) nodes.set(selector, { addEventListener() {}, textContent: '' });
      return nodes.get(selector);
    } },
    window: { close: () => events.push('close') },
    chrome: {
      tabs: { get: async id => ({ id }), query: async () => [{ id: 42 }] },
      windows: { create: async options => events.push(options) },
      runtime: { getURL: url => url, sendMessage: async message => {
        events.push(message);
        return { ok: false, message: 'Permission expired at write time' };
      } },
    },
  });
  // Exercise the actual control flow without starting the initial directory read.
  const source = fs.readFileSync(path.join(root, 'popup.js'), 'utf8');
  vm.runInContext(source.slice(0, source.lastIndexOf('\nreadArchiveRoot()')), context);
  vm.runInContext('readArchiveRoot = async () => ({}); verifyWritable = async () => {};', context);
  await context.control('record');
  {
    assert.equal(events[0].tabId, 42);
    assert.equal(events.includes('close'), false, 'Task page must stay open after failure');
    assert.match(nodes.get('#status').textContent, /Permission expired/);
    assert.equal(nodes.get('#record').disabled, false);
  }
}

(async () => {
  await popupCase('?targetTabId=42');
  let windowsCreated = 0;
  let windowsFocused = 0;
  let existingTabs = [];
  const context = vm.createContext({
    console: { error() {} }, importScripts() {}, self: {},
    chrome: {
      action: { onClicked: { addListener() {} } },
      tabs: { query: async () => existingTabs, update: async () => {} },
      windows: {
        create: async options => {
          windowsCreated++;
          existingTabs = [{ id: 10, windowId: 20, url: options.url }];
        },
        update: async () => { windowsFocused++; },
      },
      runtime: { getURL: name => `chrome-extension://test/${name}`, onMessage: { addListener() {} } },
    },
  });
  vm.runInContext(fs.readFileSync(path.join(root, 'service-worker.js'), 'utf8'), context);
  await Promise.all([context.openTaskWindow({ id: 42 }), context.openTaskWindow({ id: 42 })]);
  assert.equal(windowsCreated, 1, 'Rapid toolbar clicks must create only one window');
  await context.openTaskWindow({ id: 42 });
  assert.equal(windowsCreated, 1);
  assert.equal(windowsFocused, 1, 'Repeated toolbar clicks must focus the existing window');
  vm.runInContext(`
    var stored;
    runStatus = async value => value ? (stored = value) : stored;
    setBadge = async () => {};
    inspectRecordPage = async () => { throw new Error('Specific export failure'); };
  `, context);
  const result = await context.runRecordExport({ id: 42 }, {});
  assert.equal(result.ok, false);
  const status = await context.handleRecordControl({ mode: 'status', tabId: 42 });
  assert.equal(status.message, 'Specific export failure');
  assert.equal(status.running, false);
  assert.equal(await context.handleRecordControl({ mode: 'status', tabId: 99 }), null);
  vm.runInContext('stored = { tabId: 42, running: true };', context);
  assert.match((await context.handleRecordControl({ mode: 'status', tabId: 42 })).message, /中断/);
  console.log('Task window and error reporting regression checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
