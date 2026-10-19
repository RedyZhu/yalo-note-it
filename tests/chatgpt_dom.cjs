const assert = require('node:assert/strict');

global.self = globalThis;
global.Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };
global.innerHeight = 900;

function attributeValue(element, name) {
  return Object.prototype.hasOwnProperty.call(element.attributes, name)
    ? element.attributes[name]
    : null;
}

function matchesSimpleSelector(element, selector) {
  const trimmed = selector.trim();
  const tagMatch = trimmed.match(/^[a-z][a-z0-9-]*/iu);
  if (tagMatch && element.tagName !== tagMatch[0].toUpperCase()) return false;
  const classNames = [...trimmed.matchAll(/\.([a-zA-Z0-9_-]+)/gu)].map((match) => match[1]);
  const elementClasses = String(attributeValue(element, 'class') ?? '').split(/\s+/u).filter(Boolean);
  if (!classNames.every((className) => elementClasses.includes(className))) return false;
  const attributes = [...trimmed.matchAll(/\[([^\]=~^$*]+)(?:(\^=|\$=|=)'([^']*)')?\]/gu)];
  if (!tagMatch && !classNames.length && !attributes.length) return false;
  return attributes.every((match) => {
    const value = attributeValue(element, match[1]);
    if (!match[2]) return value !== null;
    if (value === null) return false;
    if (match[2] === '=') return value === match[3];
    if (match[2] === '^=') return value.startsWith(match[3]);
    if (match[2] === '$=') return value.endsWith(match[3]);
    return false;
  });
}

function matchesSelector(element, selector) {
  return selector.split(',').some((part) => matchesSimpleSelector(element, part));
}

class FakeElement {
  constructor(tagName, attributes = {}, text = null) {
    this.nodeType = Node.ELEMENT_NODE;
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.childNodes = [];
    this.children = [];
    this.parentElement = null;
    this.clientHeight = 0;
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.classList = String(attributes.class ?? '').split(/\s+/u).filter(Boolean);
    if (text !== null) this.appendText(text);
  }

  appendText(value) {
    this.childNodes.push({ nodeType: Node.TEXT_NODE, textContent: value, parentElement: this });
    return this;
  }

  append(child) {
    child.parentElement = this;
    this.childNodes.push(child);
    this.children.push(child);
    return this;
  }

  getAttribute(name) { return attributeValue(this, name); }
  hasAttribute(name) { return attributeValue(this, name) !== null; }
  matches(selector) { return matchesSelector(this, selector); }

  closest(selector) {
    for (let current = this; current; current = current.parentElement) {
      if (current.matches(selector)) return current;
    }
    return null;
  }

  contains(candidate) {
    return candidate === this || this.children.some((child) => child.contains(candidate));
  }

  querySelectorAll(selector) {
    const matches = [];
    const visit = (element) => {
      for (const child of element.children) {
        if (child.matches(selector)) matches.push(child);
        visit(child);
      }
    };
    visit(this);
    return matches;
  }

  querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
  scrollTo({ top }) { this.scrollTop = top; }
  scrollIntoView() {}

  get innerText() {
    return this.childNodes
      .map((child) => child.nodeType === Node.TEXT_NODE ? child.textContent : child.innerText)
      .join('');
  }

  get textContent() { return this.innerText; }
}

function installDocument(roots) {
  const scrollingElement = {
    scrollTop: 0,
    scrollHeight: 600,
    clientHeight: 600,
    scrollTo({ top }) { this.scrollTop = top; },
  };
  const allElements = () => {
    const elements = [];
    const visit = (element) => {
      elements.push(element);
      element.children.forEach(visit);
    };
    roots.forEach(visit);
    return elements;
  };
  global.document = {
    title: 'Fixture conversation',
    scrollingElement,
    querySelectorAll: (selector) => allElements().filter((element) => element.matches(selector)),
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
  };
  global.location = { href: 'https://chatgpt.com/c/test' };
}

global.getComputedStyle = () => ({ overflowY: 'visible', flexDirection: 'column' });
require('../browser-extension/yalo-note-it/providers/chatgpt.js');
const provider = self.YaloNoteProviders[0];

function fileCard(name) {
  return new FakeElement('div', { role: 'group', 'aria-label': name })
    .append(new FakeElement('span', { 'data-testid': 'library-file-icon' }));
}

{
  const hiddenCard = fileCard('hidden.tmp');
  hiddenCard.attributes['aria-hidden'] = 'true';
  const user = new FakeElement('div', {
    'data-chatgpt-search-unit-key': 'shared:user',
    'data-chatgpt-search-message-ids': 'user-1',
  }).append(new FakeElement('p', {}, '用户问题'))
    .append(fileCard('user.pdf'))
    .append(fileCard('user.pdf'))
    .append(hiddenCard);
  const hiddenBody = new FakeElement('div', { 'aria-hidden': 'true' })
    .append(new FakeElement('div', { 'data-markdown-text-style': 'assistant-message' }, '旧回答'));
  const body = new FakeElement('div', { 'data-markdown-text-style': 'assistant-message' })
    .append(new FakeElement('h4', {}, 'ChatGPT 说：'))
    .append(new FakeElement('p', {}, '当前回答').append(new FakeElement('button', {}, '复制')));
  const assistant = new FakeElement('div', {
    'data-chatgpt-search-unit-key': 'shared:assistant',
    'data-chatgpt-search-message-ids': 'assistant-1 assistant-1',
  }).append(hiddenBody).append(body).append(fileCard('answer.docx'));
  const owner = new FakeElement('article', { 'data-turn-key': 'shared' }).append(user).append(assistant);
  installDocument([owner]);

  const result = provider.inspectPage('none');
  assert.equal(result.error, undefined);
  assert.deepEqual(result.records.map(({ id, role, markdown }) => ({ id, role, markdown })), [
    { id: 'user-1', role: 'user', markdown: '用户问题' },
    { id: 'assistant-1', role: 'assistant', markdown: '当前回答' },
  ]);
  assert.deepEqual(result.artifacts.map(({ messageId, displayName, kind }) => ({ messageId, displayName, kind })), [
    { messageId: 'user-1', displayName: 'user.pdf', kind: 'uploaded' },
    { messageId: 'user-1', displayName: 'user.pdf', kind: 'uploaded' },
    { messageId: 'assistant-1', displayName: 'answer.docx', kind: 'generated' },
  ]);
}

{
  const assistant = new FakeElement('div', {
    'data-message-author-role': 'assistant',
    'data-message-id': 'generating-1',
  }).append(new FakeElement('div', { 'data-markdown-text-style': 'assistant-message' }));
  const stop = new FakeElement('button', { 'data-testid': 'stop-button' }, '停止生成');
  installDocument([assistant, stop]);
  const generating = provider.inspectPage('none');
  assert.equal(generating.error, undefined);
  assert.equal(generating.generating, true);
  assert.equal(generating.records[0].markdown, '');
  stop.attributes['aria-hidden'] = 'true';
  const stopped = provider.inspectPage('none');
  assert.match(stopped.error, /未提取到正文/u);
}

{
  const legacy = new FakeElement('div', {
    'data-message-author-role': 'assistant',
    'data-message-id': 'legacy-1',
  }).append(new FakeElement('h4', { class: 'sr-only' }, 'ChatGPT 说：'))
    .append(new FakeElement('div', { class: 'markdown prose' }).append(new FakeElement('p', {}, '旧版正文')));
  installDocument([legacy]);
  assert.equal(provider.inspectPage('none').records[0].markdown, '旧版正文');
}

{
  installDocument([new FakeElement('div', { 'data-message-author-role': 'user' }, '无 ID')]);
  assert.match(provider.inspectPage('none').error, /缺少稳定 ID/u);

  installDocument([new FakeElement('div', {
    'data-message-author-role': 'user', 'data-testid': 'conversation-turn-3',
  }, '只有位置索引')]);
  assert.match(provider.inspectPage('none').error, /缺少稳定 ID/u);

  const ambiguous = new FakeElement('div', {
    'data-chatgpt-search-unit-key': 'rollout:user',
    'data-chatgpt-search-message-ids': 'first second',
  }, '歧义');
  installDocument([ambiguous]);
  assert.match(provider.inspectPage('none').error, /存在歧义/u);

  const duplicateA = new FakeElement('div', {
    'data-message-author-role': 'user', 'data-message-id': 'duplicate',
  }, 'A');
  const duplicateB = new FakeElement('div', {
    'data-message-author-role': 'assistant', 'data-message-id': 'duplicate',
  }).append(new FakeElement('div', { 'data-markdown-text-style': 'assistant-message' }, 'B'));
  installDocument([duplicateA, duplicateB]);
  assert.match(provider.inspectPage('none').error, /重复消息 ID/u);
}

{
  const user = new FakeElement('div', {
    'data-chatgpt-search-unit-key': 'reverse:user',
    'data-chatgpt-search-message-ids': 'reverse-user',
  }, '问题');
  const assistant = new FakeElement('div', {
    'data-chatgpt-search-unit-key': 'reverse:assistant',
    'data-chatgpt-search-message-ids': 'reverse-assistant',
  }).append(new FakeElement('div', { 'data-markdown-text-style': 'assistant-message' }, '回答'));
  const owner = new FakeElement('article', { 'data-turn-key': 'reverse' }).append(user).append(assistant);
  const scroller = new FakeElement('div').append(owner);
  scroller.clientHeight = 800;
  scroller.scrollHeight = 4000;
  global.getComputedStyle = (element) => ({
    overflowY: element === scroller ? 'auto' : 'visible',
    flexDirection: element === scroller ? 'column-reverse' : 'column',
  });
  installDocument([scroller]);
  const atTop = provider.inspectPage('top');
  const next = provider.inspectPage('next');
  scroller.scrollTop = 0.5;
  const atBottom = provider.inspectPage('none');
  assert.equal(atTop.scrollTop, -3200);
  assert.equal(atTop.atTop, true);
  assert.equal(atTop.atBottom, false);
  assert.equal(next.scrollTop, -2600);
  assert.equal(next.atTop, false);
  assert.equal(atBottom.atBottom, true);
}

console.log('ChatGPT DOM discovery regression checks passed');
