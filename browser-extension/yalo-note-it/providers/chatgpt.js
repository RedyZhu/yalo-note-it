function inspectChatGptPage(targetPosition) {
  const TURN_OWNER_SELECTOR = [
    "[data-testid^='conversation-turn-']",
    "[data-turn-id]",
    "[data-turn-key]",
    "article",
    "section[data-turn]",
  ].join(", ");
  const MESSAGE_ANCHOR_SELECTOR = [
    "[data-message-author-role='user']",
    "[data-message-author-role='assistant']",
    "[data-chatgpt-search-unit-key$=':user']",
    "[data-chatgpt-search-unit-key$=':assistant']",
    "[data-turn='user']",
    "[data-turn='assistant']",
  ].join(", ");
  const MESSAGE_ID_ATTRIBUTES = [
    "data-message-id",
    "data-chatgpt-search-message-ids",
    "data-turn-id",
    "data-turn-key",
    "data-testid",
    "id",
  ];
  const ASSISTANT_CONTENT_SELECTOR = "[data-markdown-text-style='assistant-message']";
  const LEGACY_ASSISTANT_CONTENT_SELECTOR = ".markdown";
  const BLOCK_ELEMENT_SELECTOR = "p, pre, h1, h2, h3, h4, h5, h6, ul, ol, blockquote, table, hr";

  function findScroller(turns) {
    const first = turns[0] ?? document.querySelector("main");
    const last = turns.at(-1) ?? first;
    if (!first) return null;
    for (let parent = first; parent; parent = parent.parentElement) {
      if (!parent.contains(last)) continue;
      const style = getComputedStyle(parent);
      if (
        /(auto|scroll)/.test(style.overflowY) &&
        parent.clientHeight >= innerHeight * 0.45 &&
        parent.scrollHeight > parent.clientHeight + 40
      ) return parent;
    }
    return document.scrollingElement;
  }

  function cleanText(value) {
    return (value ?? "").replace(/\u00a0/gu, " ").trim();
  }

  function messageRole(element) {
    const directRole = element.getAttribute("data-message-author-role")
      ?? element.getAttribute("data-turn");
    if (directRole === "user" || directRole === "assistant") return directRole;
    const unitKey = element.getAttribute("data-chatgpt-search-unit-key") ?? "";
    return unitKey.match(/:(user|assistant)$/u)?.[1] ?? null;
  }

  function messageOwner(element) {
    if (element.matches?.(TURN_OWNER_SELECTOR)) return element;
    return element.closest?.(TURN_OWNER_SELECTOR) ?? element;
  }

  function hasMessageIdentity(element) {
    return MESSAGE_ID_ATTRIBUTES.some((name) => cleanText(element?.getAttribute?.(name)));
  }

  function isExplicitlyHidden(element) {
    const classes = (element.getAttribute?.("class") ?? "").split(/\s+/u);
    return element.getAttribute?.("aria-hidden") === "true"
      || element.getAttribute?.("hidden") !== null
      || classes.includes("sr-only");
  }

  function hasHiddenAncestor(element) {
    for (let current = element; current; current = current.parentElement) {
      if (isExplicitlyHidden(current)) return true;
    }
    return false;
  }

  function messageBoundary(owner, anchor, role) {
    if (hasMessageIdentity(anchor)) return anchor;
    const selector = [
      `[data-message-author-role='${role}']`,
      `[data-chatgpt-search-unit-key$=':${role}']`,
      `[data-turn='${role}']`,
    ].join(", ");
    return [...owner.querySelectorAll(selector)]
      .find((candidate) => !hasHiddenAncestor(candidate)) ?? anchor;
  }

  function isIgnoredElement(element, ignoreInteractive = false) {
    const tag = element.tagName?.toLowerCase?.() ?? "";
    const role = element.getAttribute?.("role") ?? "";
    return isExplicitlyHidden(element)
      || ["script", "style", "noscript", "svg"].includes(tag)
      || ["menu", "menubar", "navigation", "toolbar", "tooltip"].includes(role)
      || (ignoreInteractive && (
        ["button", "input", "select", "textarea"].includes(tag) || role === "button"
      ));
  }

  function isVisibleWithinBoundary(candidate, boundary) {
    return boundary.contains(candidate) && !hasHiddenAncestor(candidate);
  }

  function outermostVisibleRoots(boundary, selector) {
    const candidates = [
      ...(boundary.matches?.(selector) ? [boundary] : []),
      ...boundary.querySelectorAll(selector),
    ];
    const unique = [...new Set(candidates)]
      .filter((candidate) => isVisibleWithinBoundary(candidate, boundary));
    const outermost = unique.filter((candidate) =>
      !unique.some((other) => other !== candidate && other.contains(candidate)));
    return outermost;
  }

  function messageContentRoots(boundary, role) {
    if (role !== "assistant") return { error: null, mode: "message", roots: [boundary] };
    const semanticRoots = outermostVisibleRoots(boundary, ASSISTANT_CONTENT_SELECTOR);
    if (semanticRoots.length) {
      return { error: null, mode: "semantic", roots: semanticRoots };
    }
    const rolloutKey = cleanText(boundary.getAttribute("data-chatgpt-search-unit-key"));
    if (rolloutKey) {
      return {
        error: "ChatGPT 新版助手消息缺少可见正文节点，已停止以避免保存界面控件或残缺档案。",
        mode: null,
        roots: [],
      };
    }
    const legacyRoots = outermostVisibleRoots(boundary, LEGACY_ASSISTANT_CONTENT_SELECTOR);
    return {
      error: null,
      mode: legacyRoots.length ? "legacy" : "fallback",
      roots: legacyRoots.length ? legacyRoots : [boundary],
    };
  }

  function firstAttribute(elements, name) {
    for (const element of elements) {
      const value = cleanText(element?.getAttribute?.(name));
      if (value) return value;
    }
    return null;
  }

  function messageIdentity(owner, boundary, anchor) {
    const elements = [boundary, anchor, owner];
    const legacyId = firstAttribute(elements, "data-message-id");
    if (legacyId) return { id: legacyId, error: null };
    const rolloutValue = firstAttribute(elements, "data-chatgpt-search-message-ids");
    if (rolloutValue) {
      const rolloutIds = [...new Set(rolloutValue.split(/\s+/u).filter(Boolean))];
      if (rolloutIds.length !== 1) {
        return { id: null, error: "ChatGPT 消息 ID 存在歧义，已停止以避免错误合并消息。" };
      }
      return { id: rolloutIds[0], error: null };
    }
    for (const name of ["data-turn-id", "data-turn-key", "data-testid", "id"]) {
      const value = firstAttribute([owner, boundary, anchor], name);
      if (value) return { id: value, error: null };
    }
    return { id: null, error: "ChatGPT 消息结构缺少稳定 ID，已停止以避免覆盖或漏记消息。" };
  }

  function discoverTurns() {
    const anchors = [...document.querySelectorAll(MESSAGE_ANCHOR_SELECTOR)];
    const turns = [];
    const seenBoundaries = new Set();
    const seenIds = new Set();
    for (const anchor of anchors) {
      if (hasHiddenAncestor(anchor)) continue;
      const role = messageRole(anchor);
      if (!role) continue;
      const owner = messageOwner(anchor);
      const boundary = messageBoundary(owner, anchor, role);
      if (seenBoundaries.has(boundary)) continue;
      const identity = messageIdentity(owner, boundary, anchor);
      if (identity.error) return { error: identity.error, turns: [] };
      const id = identity.id;
      if (seenIds.has(id)) {
        return {
          error: `ChatGPT 页面出现重复消息 ID（${id}），已停止以避免覆盖消息。`,
          turns: [],
        };
      }
      seenBoundaries.add(boundary);
      seenIds.add(id);
      const content = messageContentRoots(boundary, role);
      if (content.error) {
        return { error: `ChatGPT 助手消息（${id}）：${content.error}`, turns: [] };
      }
      turns.push({
        owner,
        boundary,
        contentMode: content.mode,
        contentRoots: content.roots,
        role,
        id,
      });
    }
    return { error: null, turns };
  }

  function inlineMarkdown(node, ignoreInteractive = false) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const element = node;
    if (isIgnoredElement(element, ignoreInteractive)) return "";
    const tag = element.tagName.toLowerCase();
    const content = [...element.childNodes]
      .map((child) => inlineMarkdown(child, ignoreInteractive))
      .join("");
    if (tag === "br") return "\n";
    if (tag === "code" && element.parentElement?.tagName !== "PRE") {
      return `\`${content.replace(/`/gu, "\\`")}\``;
    }
    if (tag === "strong" || tag === "b") return `**${content}**`;
    if (tag === "em" || tag === "i") return `*${content}*`;
    if (tag === "a") {
      const href = element.getAttribute("href") ?? "";
      return href ? `[${content || href}](${href})` : content;
    }
    return content;
  }

  function blockMarkdown(root, ignoreInteractive = false) {
    const blocks = [];
    const pushInline = (value) => {
      const text = cleanText(value);
      if (text) blocks.push(text);
    };
    const visit = (element, depth = 0) => {
      if (isIgnoredElement(element, ignoreInteractive)) return;
      const tag = element.tagName.toLowerCase();
      if (tag === "pre") {
        const code = element.querySelector("code");
        const language = [...(code?.classList ?? [])]
          .find((name) => name.startsWith("language-"))
          ?.slice(9) ?? "";
        blocks.push(`\`\`\`${language}\n${(code?.innerText ?? element.innerText ?? "").trimEnd()}\n\`\`\``);
        return;
      }
      if (/^h[1-6]$/u.test(tag)) {
        blocks.push(`${"#".repeat(Number(tag[1]))} ${cleanText(inlineMarkdown(element, ignoreInteractive))}`);
        return;
      }
      if (tag === "blockquote") {
        blocks.push(cleanText(element.innerText).split("\n").map((line) => `> ${line}`).join("\n"));
        return;
      }
      if (tag === "table") {
        const rows = [...element.querySelectorAll("tr")].map((row) =>
          [...row.querySelectorAll(":scope > th, :scope > td")]
            .map((cell) => cleanText(inlineMarkdown(cell, ignoreInteractive))),
        );
        if (rows.length) {
          const width = Math.max(...rows.map((row) => row.length));
          const normalized = rows.map((row) => [...row, ...Array(width - row.length).fill("")]);
          blocks.push([
            `| ${normalized[0].join(" | ")} |`,
            `| ${Array(width).fill("---").join(" | ")} |`,
            ...normalized.slice(1).map((row) => `| ${row.join(" | ")} |`),
          ].join("\n"));
        }
        return;
      }
      if (tag === "ul" || tag === "ol") {
        const ordered = tag === "ol";
        const lines = [...element.children]
          .filter((child) => child.tagName === "LI")
          .map((item, index) => `${"  ".repeat(depth)}${ordered ? `${index + 1}.` : "-"} ${cleanText(inlineMarkdown(item, ignoreInteractive))}`);
        if (lines.length) blocks.push(lines.join("\n"));
        return;
      }
      if (tag === "p") {
        pushInline(inlineMarkdown(element, ignoreInteractive));
        return;
      }
      if (tag === "hr") {
        blocks.push("---");
        return;
      }
      if (!element.querySelector(BLOCK_ELEMENT_SELECTOR)) {
        pushInline(inlineMarkdown(element, ignoreInteractive));
        return;
      }
      let inlineBuffer = "";
      const flushInline = () => {
        pushInline(inlineBuffer);
        inlineBuffer = "";
      };
      for (const child of element.childNodes) {
        if (child.nodeType === Node.TEXT_NODE) {
          inlineBuffer += child.textContent ?? "";
          continue;
        }
        if (
          child.nodeType !== Node.ELEMENT_NODE
          || isIgnoredElement(child, ignoreInteractive)
        ) continue;
        if (child.matches(BLOCK_ELEMENT_SELECTOR) || child.querySelector(BLOCK_ELEMENT_SELECTOR)) {
          flushInline();
          visit(child, depth + 1);
        } else {
          inlineBuffer += inlineMarkdown(child, ignoreInteractive);
        }
      }
      flushInline();
    };
    visit(root);
    return blocks.join("\n\n").replace(/\n{3,}/gu, "\n\n").trim();
  }

  const discovery = discoverTurns();
  if (discovery.error) return { error: discovery.error };
  const turns = discovery.turns;
  if (!turns.length) {
    const roleNodes = document.querySelectorAll("[data-message-author-role]").length;
    const turnNodes = document.querySelectorAll("[data-testid^='conversation-turn-'], [data-turn]").length;
    const rolloutNodes = document.querySelectorAll("[data-chatgpt-search-unit-key]").length;
    return {
      error: `没有识别到 ChatGPT 会话消息（role=${roleNodes}, turn=${turnNodes}, rollout=${rolloutNodes}）。`,
    };
  }
  const scroller = findScroller(turns.map((turn) => turn.owner));
  if (!scroller) return { error: "找不到 ChatGPT 对话滚动区域。" };
  const reverseScroll = getComputedStyle(scroller).flexDirection === "column-reverse";
  const scrollExtent = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  if (targetPosition === "top") {
    scroller.scrollTo({ top: reverseScroll ? -scrollExtent : 0, behavior: "instant" });
  }
  if (targetPosition === "next") {
    const step = Math.floor(scroller.clientHeight * 0.75);
    scroller.scrollTo({
      top: reverseScroll
        ? Math.min(0, scroller.scrollTop + step)
        : Math.min(scrollExtent, scroller.scrollTop + step),
      behavior: "instant",
    });
  }
  const records = [];
  for (const turn of turns) {
    const markdown = turn.contentRoots
      .map((root) => blockMarkdown(root, turn.contentMode === "fallback"))
      .filter(Boolean)
      .join("\n\n")
      .trim();
    const attachments = [...turn.boundary.querySelectorAll("img")]
      .map((image) => image.getAttribute("alt"))
      .filter(Boolean);
    const substantiveAssistantBody = markdown
      .replace(/^#{1,6}\s*ChatGPT\s*(?:说|said)?\s*[:：]?\s*$/gimu, "")
      .trim();
    if (turn.role === "assistant" && !substantiveAssistantBody) {
      return {
        error: `ChatGPT 助手消息（${turn.id}）未提取到正文，已停止以避免保存不完整档案。`,
      };
    }
    records.push({
      id: turn.id,
      role: turn.role,
      markdown,
      attachments: [...new Set(attachments)],
    });
  }
  const artifacts = [];
  for (const turn of turns) {
    if (turn.role !== "user") continue;
    const messageId = turn.id;
    const cards = [...turn.owner.querySelectorAll("[role='group'][aria-label]")]
      .filter((card) => card.querySelector("[data-testid='library-file-icon']"));
    cards.forEach((card, index) => {
      const displayName = cleanText(card.getAttribute("aria-label"));
      if (!displayName) return;
      artifacts.push({
        key: `${messageId}::${index}::${displayName}`,
        messageId,
        displayName,
      });
    });
  }
  return {
    records,
    artifacts,
    scrollTop: scroller.scrollTop,
    scrollHeight: scroller.scrollHeight,
    viewport: scroller.clientHeight,
    atTop: reverseScroll
      ? scroller.scrollTop <= -scrollExtent + 3
      : scroller.scrollTop <= 3,
    atBottom: reverseScroll
      ? scroller.scrollTop >= -3
      : scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 3,
    title: document.title,
    url: location.href,
  };
}

async function captureChatGptArtifact(artifactKey) {
  const wait = (milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds));
  const parts = String(artifactKey).split("::");
  const messageId = parts.shift() ?? "";
  const cardIndex = Number(parts.shift() ?? 0);
  const displayName = parts.join("::");
  const locatorSelector = [
    "[data-testid^='conversation-turn-']",
    "[data-turn-id]",
    "[data-turn-key]",
    "[data-message-id]",
    "[data-chatgpt-search-message-ids]",
  ].join(", ");
  const ownerSelector = [
    "[data-testid^='conversation-turn-']",
    "[data-turn-id]",
    "[data-turn-key]",
    "article",
    "section[data-turn]",
  ].join(", ");
  const identifierAttributes = [
    "data-turn-id",
    "data-message-id",
    "data-turn-key",
    "data-testid",
    "data-chatgpt-search-message-ids",
    "id",
  ];
  const located = [...document.querySelectorAll(locatorSelector)]
    .find((candidate) => identifierAttributes.some(
      (name) => {
        const value = (candidate.getAttribute(name) ?? "").trim();
        if (name !== "data-chatgpt-search-message-ids") return value === messageId;
        const ids = [...new Set(value.split(/\s+/u).filter(Boolean))];
        return ids.length === 1 && ids[0] === messageId;
      },
    ));
  const turn = located?.matches?.(ownerSelector)
    ? located
    : located?.closest?.(ownerSelector) ?? located;
  const cards = turn
    ? [...turn.querySelectorAll("[role='group'][aria-label]")]
      .filter((card) => card.querySelector("[data-testid='library-file-icon']"))
    : [];
  const card = cards[cardIndex];
  if (!card || card.getAttribute("aria-label") !== displayName) {
    return { key: artifactKey, messageId, displayName, status: "unavailable", error: "上传文件卡已不在页面中。" };
  }

  const findDownloadButton = () => [...document.querySelectorAll("button")].find((button) => {
    const label = button.getAttribute("aria-label") ?? button.textContent ?? "";
    return /^(下载|download)$/iu.test(label.trim());
  });
  const findCloseButton = () => [...document.querySelectorAll("button")].find((button) => {
    const label = button.getAttribute("aria-label") ?? button.textContent ?? "";
    return /^(关闭|close)$/iu.test(label.trim());
  });

  try {
    card.scrollIntoView({ block: "center", behavior: "instant" });
    (card.querySelector("button") ?? card).click();
    let downloadButton = null;
    for (let attempt = 0; attempt < 50 && !downloadButton; attempt += 1) {
      await wait(100);
      downloadButton = findDownloadButton();
    }
    if (!downloadButton) throw new Error("文件预览没有提供下载按钮。");

    const originalCreateObjectURL = URL.createObjectURL;
    const anchorPrototype = HTMLAnchorElement.prototype;
    const originalAnchorClick = anchorPrototype.click;
    let resolvePayload;
    const payloadPromise = new Promise((resolve) => { resolvePayload = resolve; });
    const readBlob = async (blob, sourceUrl = null) => {
      if (blob.size > 25 * 1024 * 1024) {
        resolvePayload({ status: "too_large", size: blob.size, sourceUrl, error: "文件超过 25 MB 的插件保存上限。" });
        return;
      }
      const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      resolvePayload({
        status: "saved",
        size: blob.size,
        mimeType: blob.type || "application/octet-stream",
        sourceUrl,
        base64: btoa(binary),
      });
    };
    URL.createObjectURL = (object) => {
      if (object instanceof Blob) void readBlob(object);
      return originalCreateObjectURL.call(URL, object);
    };
    anchorPrototype.click = function interceptedClick() {
      const href = this.href;
      if (href && !href.startsWith("blob:")) {
        void fetch(href, { credentials: "include" })
          .then((response) => {
            if (!response.ok) throw new Error(`下载请求失败：HTTP ${response.status}`);
            return response.blob();
          })
          .then((blob) => readBlob(blob, href))
          .catch((error) => resolvePayload({ status: "unavailable", sourceUrl: href, error: String(error.message ?? error) }));
      }
      return undefined;
    };

    try {
      downloadButton.click();
      const payload = await Promise.race([
        payloadPromise,
        wait(15000).then(() => ({ status: "unavailable", error: "等待文件下载超时。" })),
      ]);
      return { key: artifactKey, messageId, displayName, ...payload };
    } finally {
      URL.createObjectURL = originalCreateObjectURL;
      anchorPrototype.click = originalAnchorClick;
      findCloseButton()?.click();
    }
  } catch (error) {
    findCloseButton()?.click();
    return {
      key: artifactKey,
      messageId,
      displayName,
      status: "unavailable",
      error: error instanceof Error ? error.message : String(error),
    };
  }
}

self.YaloNoteProviders = [
  ...(self.YaloNoteProviders ?? []),
  {
    id: "chatgpt",
    label: "ChatGPT",
    matchesUrl: (url) => /^https:\/\/(chatgpt\.com|chat\.openai\.com)\//u.test(url || ""),
    conversationId: (url) => url.match(/\/c\/([0-9a-f-]+)/iu)?.[1] ?? null,
    inspectPage: inspectChatGptPage,
    captureArtifact: captureChatGptArtifact,
  },
];
