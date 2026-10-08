const assert = require('node:assert/strict');
global.self = globalThis;
global.Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };
global.location = { href: 'https://chatgpt.com/c/test' };
global.innerHeight = 800;
global.getComputedStyle = () => ({ overflowY: 'visible' });
require('../browser-extension/yalo-note-it/providers/chatgpt.js');
const provider = self.YaloNoteProviders[0];

class Anchor {
  constructor(href) { this.href = href; }
  click() {
    if (this.href.startsWith('sandbox:')) return new Anchor('blob:https://chatgpt.com/legacy').click();
    throw new Error('Unexpected native download');
  }
}
global.HTMLAnchorElement = Anchor;
const originalClick = Anchor.prototype.click;
const scrollRoot = {
  scrollHeight: 240000, clientHeight: 735, scrollTop: 10000, scrollLeft: 0,
  scrollTo({ top, left }) { this.scrollTop = top; this.scrollLeft = left; },
};
const button = {
  parentElement: scrollRoot,
  getAttribute: () => 'report.md',
  closest: () => row,
  scrollIntoView() { scrollRoot.scrollTop = 230000; },
};
let downloadAction;
const download = { getAttribute: () => '下载文件', click: () => downloadAction() };
const row = { querySelectorAll: () => [button, download] };
const link = new Anchor('sandbox:/mnt/data/report.md');
link.getAttribute = () => link.href;
link.scrollIntoView = () => {};
let showFileCard = true;
const content = {
  tagName: 'DIV', nodeType: 1, childNodes: [], children: [],
  getAttribute: () => 'assistant',
};
const turn = {
  contains: () => true,
  getAttribute: () => 'conversation-turn-2',
  querySelector: () => content,
  querySelectorAll: (selector) => {
    if (selector.includes('open-file')) return showFileCard ? [button] : [];
    if (selector.startsWith('a[')) return [link];
    return [];
  },
};
global.document = {
  title: 'Fixture',
  scrollingElement: { scrollTop: 0, scrollHeight: 600, clientHeight: 600 },
  querySelectorAll: (selector) => selector.startsWith('main ') ? [turn] : [],
};

(async () => {
  const artifacts = provider.inspectPage('none').artifacts;
  assert.equal(artifacts.length, 1, 'A file card and its inline link must not download twice');
  assert.equal(artifacts[0].kind, 'generated');
  assert.equal(artifacts[0].displayName, 'report.md');

  let response = { ok: true, blob: async () => new Blob(['# Saved document'], { type: 'text/markdown' }) };
  global.fetch = async () => response;
  downloadAction = () => new Anchor('blob:https://chatgpt.com/test').click();
  let result = await provider.captureArtifact(artifacts[0].key);
  assert.equal(result.status, 'saved');
  assert.equal(Buffer.from(result.base64, 'base64').toString(), '# Saved document');
  assert.equal(Anchor.prototype.click, originalClick);
  assert.equal(scrollRoot.scrollTop, 10000, 'Downloading a distant pinned card must restore the scan cursor');

  showFileCard = false;
  const legacyArtifacts = provider.inspectPage('none').artifacts;
  assert.equal(legacyArtifacts.length, 1);
  assert.match(legacyArtifacts[0].key, /::sandbox::/);
  result = await provider.captureArtifact(legacyArtifacts[0].key);
  assert.equal(result.status, 'saved');
  assert.equal(result.displayName, 'report.md');
  showFileCard = true;

  response = { ok: false, status: 410 };
  result = await provider.captureArtifact(artifacts[0].key);
  assert.equal(result.status, 'unavailable');
  assert.match(result.error, /410/);
  assert.equal(scrollRoot.scrollTop, 10000, 'Failed downloads must also restore the scan cursor');
  assert.equal(Anchor.prototype.click, originalClick);

  response = { ok: true, blob: async () => ({ size: 26 * 1024 * 1024 }) };
  result = await provider.captureArtifact(artifacts[0].key);
  assert.equal(result.status, 'too_large');
  assert.equal(result.base64, undefined);

  downloadAction = () => { throw new Error('Page download failed'); };
  result = await provider.captureArtifact(artifacts[0].key);
  assert.equal(result.status, 'unavailable');
  assert.equal(Anchor.prototype.click, originalClick);

  result = await provider.captureArtifact('conversation-turn-404::generated::missing.pdf');
  assert.equal(result.status, 'unavailable');
  console.log('Generated attachment regression checks passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
