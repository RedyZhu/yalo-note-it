const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const extensionRoot = path.join(__dirname, '../browser-extension/yalo-note-it');
const conversationUrl = 'https://chatgpt.com/c/conversation-one';
const otherConversationUrl = 'https://chatgpt.com/c/conversation-two';

const taskStatuses = new Map();
const tabUpdates = [];
const badgeTexts = [];
const events = {};
let existingTaskTabs = [];
let windowsCreated = 0;
let windowsFocused = 0;

const provider = {
  id: 'chatgpt',
  label: 'ChatGPT',
  matchesUrl: url => typeof url === 'string' && url.startsWith('https://chatgpt.com/c/'),
  conversationId: url => new URL(url).pathname.split('/').at(-1),
};

const tabs = new Map([
  [42, { id: 42, windowId: 7, url: conversationUrl }],
  [99, { id: 99, windowId: 8, url: 'https://example.com/page' }],
]);

const context = vm.createContext({
  URL,
  console: { error() {} },
  setTimeout: callback => { callback(); return 0; },
  clearTimeout() {},
  self: {
    YaloArchiveSettings: { loadArchiveRoot: async () => null },
    YaloTaskStatusStore: {
      read: async (tabId, identity) => taskStatuses.get(`${tabId}:${identity}`) ?? null,
      write: async (tabId, identity, value) => {
        taskStatuses.set(`${tabId}:${identity}`, value);
        return value;
      },
    },
    YaloNoteProviders: [provider],
  },
  importScripts() {},
  chrome: {
    action: {
      onClicked: { addListener: listener => { events.actionClicked = listener; } },
      setBadgeBackgroundColor: async () => {},
      setBadgeText: async value => { badgeTexts.push(value); },
    },
    runtime: {
      getURL: name => `chrome-extension://test/${name}`,
      onMessage: { addListener: listener => { events.message = listener; } },
    },
    scripting: { executeScript: async () => [{ result: null }] },
    tabs: {
      get: async tabId => {
        const tab = tabs.get(tabId);
        if (!tab) throw new Error('No such tab');
        return tab;
      },
      query: async () => existingTaskTabs,
      update: async (tabId, options) => {
        tabUpdates.push({ tabId, options });
        const tab = existingTaskTabs.find(candidate => candidate.id === tabId);
        if (tab && options.url) tab.url = options.url;
        return tab;
      },
      onRemoved: { addListener: listener => { events.tabRemoved = listener; } },
      onUpdated: { addListener: listener => { events.tabUpdated = listener; } },
    },
    windows: {
      create: async options => {
        windowsCreated += 1;
        existingTaskTabs = [{ id: 10, windowId: 20, url: options.url }];
      },
      update: async () => { windowsFocused += 1; },
    },
  },
});

vm.runInContext(
  fs.readFileSync(path.join(extensionRoot, 'service-worker.js'), 'utf8'),
  context,
);

(async () => {
  await Promise.all([
    context.openTaskWindow(tabs.get(42)),
    context.openTaskWindow(tabs.get(42)),
  ]);
  assert.equal(windowsCreated, 1, 'Rapid clicks must create one task window');
  await context.openTaskWindow(tabs.get(42));
  assert.equal(windowsCreated, 1);
  assert.equal(windowsFocused, 1, 'A later click focuses the existing task window');
  await context.openTaskWindow(tabs.get(99));
  assert.equal(tabUpdates.length, 0, 'An existing task window must keep its original target');

  vm.runInContext(
    "inspectRecordPage = async () => { throw new Error('Specific export failure'); }",
    context,
  );
  const failed = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(failed.ok, false);
  assert.equal(failed.message, 'Specific export failure');
  const persisted = await context.handleRecordControl({
    mode: 'status',
    tabId: 42,
    pageUrl: conversationUrl,
  });
  assert.equal(persisted.message, 'Specific export failure');
  assert.equal(persisted.running, false);
  assert.equal(
    await context.handleRecordControl({
      mode: 'status',
      tabId: 42,
      pageUrl: otherConversationUrl,
    }),
    null,
    'Statuses from another conversation must not leak into the current task',
  );

  await context.saveTaskStatus(42, conversationUrl, {
    ok: true,
    running: true,
    phase: 'scanning',
    message: 'running',
  });
  const interrupted = await context.handleRecordControl({
    mode: 'status',
    tabId: 42,
    pageUrl: conversationUrl,
  });
  assert.equal(interrupted.running, false);
  assert.match(interrupted.message, /中断/);

  const pageState = (overrides = {}) => ({
    records: [],
    artifacts: [],
    scrollTop: 0,
    scrollHeight: 100,
    viewport: 100,
    atTop: true,
    atBottom: true,
    url: conversationUrl,
    title: 'Test conversation',
    ...overrides,
  });
  let archiveWrites = 0;
  context.writeWebArchive = async () => { archiveWrites += 1; };
  context.inspectRecordPage = async () => pageState();
  const empty = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(empty.ok, false);
  assert.match(empty.message, /没有识别到任何可归档消息/);
  assert.equal(archiveWrites, 0, 'An empty scan must never write an archive');

  let stalledInspections = 0;
  context.inspectRecordPage = async () => {
    stalledInspections += 1;
    return pageState({
      records: [{ id: 'message-1', role: 'user', markdown: 'partial', attachments: [] }],
      scrollHeight: 4000,
      viewport: 800,
      atBottom: false,
    });
  };
  const stalled = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(stalled.ok, false);
  assert.match(stalled.message, /滚动连续多次未前进/);
  assert.ok(stalledInspections < 50, 'A stalled page must fail before the global scan limit');
  assert.equal(archiveWrites, 0);

  context.inspectRecordPage = async () => pageState({
    records: [{ id: 'message-1', role: 'user', markdown: 'complete', attachments: [] }],
    atTop: false,
  });
  const missingTop = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(missingTop.ok, false);
  assert.match(missingTop.message, /未确认稳定的对话顶部/);
  assert.equal(archiveWrites, 0);

  context.inspectRecordPage = async () => pageState({
    records: [{ id: 'message-1', role: 'user', markdown: 'complete', attachments: [] }],
  });
  const originalBadgeText = context.chrome.action.setBadgeText;
  const originalStatusWrite = context.self.YaloTaskStatusStore.write;
  context.chrome.action.setBadgeText = async value => {
    badgeTexts.push(value);
    if (value.text === 'OK') throw new Error('Decorative badge unavailable');
  };
  context.self.YaloTaskStatusStore.write = async () => {
    throw new Error('IndexedDB unavailable after commit');
  };
  const complete = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(complete.ok, true);
  assert.match(complete.message, /已记录 1 条消息/);
  assert.match(complete.message, /归档已提交，但任务状态未保存/);
  assert.equal(archiveWrites, 1);
  context.chrome.action.setBadgeText = originalBadgeText;
  context.self.YaloTaskStatusStore.write = originalStatusWrite;

  let artifactInspection = 0;
  let artifactCaptures = 0;
  const artifactCandidate = {
    key: 'message-2::generated::0::result.png',
    messageId: 'message-2',
    displayName: 'result.png',
    kind: 'generated',
  };
  context.inspectRecordPage = async () => {
    artifactInspection += 1;
    return pageState({
      records: [{ id: 'message-2', role: 'assistant', markdown: 'image', attachments: [] }],
      artifacts: [artifactCandidate],
      generating: artifactInspection < 7,
    });
  };
  context.captureArtifact = async () => {
    artifactCaptures += 1;
    return artifactCaptures === 1
      ? { status: 'unavailable', error: 'still generating' }
      : { status: 'saved', base64: 'aW1hZ2U=', mimeType: 'image/png', size: 5 };
  };
  const generated = await context.runRecordExport(tabs.get(42), {
    ...provider,
    captureArtifact() {},
  });
  assert.equal(generated.ok, true);
  assert.equal(artifactCaptures, 2, 'An unavailable in-progress artifact must retry after generation');

  context.inspectRecordPage = async () => pageState({
    records: [{ id: 'message-3', role: 'user', markdown: 'commit then close', attachments: [] }],
  });
  context.writeWebArchive = async () => {
    archiveWrites += 1;
    tabs.delete(42);
  };
  const committedAfterClose = await context.runRecordExport(tabs.get(42), provider);
  assert.equal(committedAfterClose.ok, true, 'Closing the source after commit must not reverse success');
  assert.equal(committedAfterClose.detached, true);
  assert.match(committedAfterClose.message, /归档已提交.*原会话标签页已关闭/);
  tabs.set(42, { id: 42, windowId: 7, url: conversationUrl });

  vm.runInContext(`activeRun = {
    tabId: 42,
    sourceUrl: ${JSON.stringify(conversationUrl)},
    phase: 'writing',
    cancelReason: null,
    detached: false,
  }`, context);
  const writingStop = await context.handleRecordControl({ mode: 'stop', tabId: 42 });
  assert.equal(writingStop.ok, false);
  assert.equal(writingStop.phase, 'writing');
  assert.match(writingStop.message, /无法安全停止/);

  vm.runInContext("activeRun.phase = 'scanning'", context);
  events.tabUpdated(42, { url: otherConversationUrl });
  assert.equal(vm.runInContext('activeRun.cancelReason', context), 'navigation');
  vm.runInContext('activeRun = null', context);

  const unsupported = await context.handleRecordControl({
    mode: 'record',
    tabId: 99,
    pageUrl: tabs.get(99).url,
  });
  assert.equal(unsupported.ok, false);
  assert.match(unsupported.message, /尚未配置/);
  assert.ok(badgeTexts.some(entry => entry.tabId === 99 && entry.text === 'NO'));

  console.log('Service worker context and lifecycle regression checks passed');
})().catch(error => {
  console.error(error);
  process.exitCode = 1;
});
