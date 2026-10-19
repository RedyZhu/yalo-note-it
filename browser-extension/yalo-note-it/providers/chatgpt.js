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
    const style = typeof getComputedStyle === "function" ? getComputedStyle(element) : null;
    const screenReaderOnly = style?.position === "absolute"
      && (style?.clip !== "auto" || style?.clipPath === "inset(50%)")
      && (Number.parseFloat(style?.width) <= 1 || Number.parseFloat(style?.height) <= 1);
    return element.getAttribute?.("aria-hidden") === "true"
      || Boolean(element.hasAttribute?.("hidden"))
      || Boolean(element.hasAttribute?.("inert"))
      || style?.display === "none"
      || style?.visibility === "hidden"
      || screenReaderOnly;
  }

  function hasHiddenAncestor(element) {
    for (let current = element; current; current = current.parentElement) {
      if (isExplicitlyHidden(current)) return true;
    }
    return false;
  }

  function visibleArtifactElements(boundary) {
    return {
      generatedButtons: [...boundary.querySelectorAll("button.group\\/open-file")]
        .filter((button) => !hasHiddenAncestor(button)),
      sandboxLinks: [...boundary.querySelectorAll('a[href^="sandbox:/"]')]
        .filter((link) => !hasHiddenAncestor(link)),
      cards: [...boundary.querySelectorAll("[role='group'][aria-label]")]
        .filter((card) => !hasHiddenAncestor(card)
          && card.querySelector("[data-testid='library-file-icon']")),
    };
  }

  function pageIsGenerating() {
    const controls = document.querySelectorAll([
      "button[data-testid='stop-button']",
      "button[data-testid='composer-stop-button']",
      "button[aria-label]",
    ].join(", "));
    return [...controls].some((button) => {
      if (hasHiddenAncestor(button)) return false;
      const testId = cleanText(button.getAttribute("data-testid"));
      if (testId === "stop-button" || testId === "composer-stop-button") return true;
      const label = cleanText(button.getAttribute("aria-label") ?? button.textContent);
      return /^(?:stop (?:generating|streaming)|停止(?:生成|回答|响应)?)$/iu.test(label);
    });
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

  function outermostVisibleRoots(boundary, selector) {
    const candidates = [
      ...(boundary.matches?.(selector) ? [boundary] : []),
      ...boundary.querySelectorAll(selector),
    ];
    const unique = [...new Set(candidates)]
      .filter((candidate) => boundary.contains(candidate) && !hasHiddenAncestor(candidate));
    return unique.filter((candidate) =>
      !unique.some((other) => other !== candidate && other.contains(candidate)));
  }

  function messageContentRoots(boundary, role) {
    if (role !== "assistant") return { error: null, mode: "message", roots: [boundary] };
    const semanticRoots = outermostVisibleRoots(boundary, ASSISTANT_CONTENT_SELECTOR);
    if (semanticRoots.length) return { error: null, mode: "semantic", roots: semanticRoots };
    if (cleanText(boundary.getAttribute("data-chatgpt-search-unit-key"))) {
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
      if (name === "data-testid" && /^conversation-turn-\d+$/iu.test(value ?? "")) {
        continue;
      }
      if (value) return { id: value, error: null };
    }
    return { id: null, error: "ChatGPT 消息结构缺少稳定 ID，已停止以避免覆盖或漏记消息。" };
  }

  function discoverTurns() {
    let candidates = [...document.querySelectorAll(MESSAGE_ANCHOR_SELECTOR)]
      .map((anchor) => ({ anchor, legacyOwner: null }));
    // Retain compatibility with older pages whose role node is only reachable
    // from a legacy conversation-turn wrapper.
    if (!candidates.length) {
      candidates = [...document.querySelectorAll("main [data-testid^='conversation-turn-']")]
        .map((owner) => ({
          anchor: owner.querySelector("[data-message-author-role]"),
          legacyOwner: owner,
        }))
        .filter(({ anchor }) => Boolean(anchor));
    }
    const turns = [];
    const seenBoundaries = new Set();
    const seenIds = new Set();
    for (const { anchor, legacyOwner } of candidates) {
      if (hasHiddenAncestor(anchor)) continue;
      const role = messageRole(anchor);
      if (!role) continue;
      const owner = legacyOwner ?? messageOwner(anchor);
      const boundary = legacyOwner ?? messageBoundary(owner, anchor, role);
      if (seenBoundaries.has(boundary)) continue;
      const identity = messageIdentity(owner, boundary, anchor);
      if (identity.error) return { error: identity.error, turns: [] };
      const id = identity.id;
      if (seenIds.has(id)) {
        return { error: `ChatGPT 页面出现重复消息 ID（${id}），已停止以避免覆盖消息。`, turns: [] };
      }
      seenBoundaries.add(boundary);
      seenIds.add(id);
      const content = legacyOwner
        ? { error: null, mode: "fallback", roots: [anchor] }
        : messageContentRoots(boundary, role);
      if (content.error) {
        return { error: `ChatGPT 助手消息（${id}）：${content.error}`, turns: [] };
      }
      turns.push({
        owner,
        boundary,
        contentMode: content.mode,
        contentRoots: content.roots,
        legacyCompatibility: Boolean(legacyOwner),
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
      if (!element.querySelector?.(BLOCK_ELEMENT_SELECTOR)) {
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
        if (child.nodeType !== Node.ELEMENT_NODE || isIgnoredElement(child, ignoreInteractive)) continue;
        if (child.matches?.(BLOCK_ELEMENT_SELECTOR) || child.querySelector?.(BLOCK_ELEMENT_SELECTOR)) {
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
    return { error: `没有识别到 ChatGPT 会话消息（role=${roleNodes}, turn=${turnNodes}, rollout=${rolloutNodes}）。` };
  }
  const scroller = findScroller(turns.map((turn) => turn.owner));
  if (!scroller) return { error: "找不到 ChatGPT 对话滚动区域。" };
  const reverseScroll = getComputedStyle(scroller).flexDirection === "column-reverse";
  const scrollExtent = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
  const generating = pageIsGenerating();
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
    let markdown = turn.contentRoots
      .map((root) => blockMarkdown(root, turn.role === "assistant"))
      .filter(Boolean)
      .join("\n\n")
      .trim();
    if (turn.role === "assistant") {
      markdown = markdown
        .replace(/^(?:#{1,6}\s*)?ChatGPT\s*(?:说|said)?\s*[:：]?\s*(?:\n+|$)/iu, "")
        .trim();
    }
    const attachments = [...turn.boundary.querySelectorAll("img")]
      .filter((image) => !hasHiddenAncestor(image))
      .map((image) => image.getAttribute("alt"))
      .filter(Boolean);
    const visibleArtifacts = visibleArtifactElements(turn.boundary);
    const hasVisibleMedia = attachments.length > 0
      || visibleArtifacts.generatedButtons.length > 0
      || visibleArtifacts.sandboxLinks.length > 0
      || visibleArtifacts.cards.length > 0;
    const activeAssistantPlaceholder = generating && turn === turns.at(-1);
    if (turn.role === "assistant" && !markdown && !hasVisibleMedia
        && !turn.legacyCompatibility && !activeAssistantPlaceholder) {
      return { error: `ChatGPT 助手消息（${turn.id}）未提取到正文，已停止以避免保存不完整档案。` };
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
    const messageId = turn.id;
    const candidates = [];
    const visibleArtifacts = visibleArtifactElements(turn.boundary);
    if (turn.role === "assistant") {
      visibleArtifacts.generatedButtons
        .forEach((button, index) => {
          const displayName = cleanText(button.getAttribute("aria-label"));
          if (!displayName) return;
          candidates.push({
            key: `${messageId}::generated::${index}::${displayName}`,
            messageId,
            displayName,
            kind: "generated",
            mechanism: "generated",
          });
        });
      visibleArtifacts.sandboxLinks.forEach((link, index) => {
        const href = link.getAttribute("href");
        if (!href) return;
        let displayName = href.split("/").at(-1);
        try { displayName = decodeURIComponent(displayName); } catch { /* Keep literal name. */ }
        if (!displayName) return;
        candidates.push({
          key: `${messageId}::sandbox::${index}::${href}`,
          messageId,
          displayName,
          kind: "generated",
          mechanism: "sandbox",
        });
      });
    }
    visibleArtifacts.cards.forEach((card, index) => {
      const displayName = cleanText(card.getAttribute("aria-label"));
      if (!displayName) return;
      candidates.push({
        key: `${messageId}::card::${index}::${displayName}`,
        messageId,
        displayName,
        kind: turn.role === "assistant" ? "generated" : "uploaded",
        mechanism: "card",
      });
    });
    const representationCandidates = candidates.filter((candidate) => candidate.mechanism !== "card");
    const cardCandidates = candidates.filter((candidate) => candidate.mechanism === "card");
    const byName = new Map();
    for (const candidate of representationCandidates) {
      const mechanisms = byName.get(candidate.displayName) ?? new Map();
      const entries = mechanisms.get(candidate.mechanism) ?? [];
      entries.push(candidate);
      mechanisms.set(candidate.mechanism, entries);
      byName.set(candidate.displayName, mechanisms);
    }
    for (const mechanisms of byName.values()) {
      const targetCount = Math.max(...[...mechanisms.values()].map((entries) => entries.length));
      const selected = [];
      for (const mechanism of ["generated", "sandbox", "card"]) {
        for (const candidate of mechanisms.get(mechanism) ?? []) {
          if (selected.length >= targetCount) break;
          selected.push(candidate);
        }
      }
      artifacts.push(...selected.map(({ mechanism: _mechanism, ...candidate }) => candidate));
    }
    artifacts.push(...cardCandidates.map(({ mechanism: _mechanism, ...candidate }) => candidate));
  }
  return {
    records,
    artifacts,
    scrollTop: scroller.scrollTop,
    scrollHeight: scroller.scrollHeight,
    viewport: scroller.clientHeight,
    generating,
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
  const isHidden = (element) => {
    const style = typeof getComputedStyle === "function" ? getComputedStyle(element) : null;
    const screenReaderOnly = style?.position === "absolute"
      && (style?.clip !== "auto" || style?.clipPath === "inset(50%)")
      && (Number.parseFloat(style?.width) <= 1 || Number.parseFloat(style?.height) <= 1);
    return element?.getAttribute?.("aria-hidden") === "true"
      || Boolean(element?.hasAttribute?.("hidden"))
      || Boolean(element?.hasAttribute?.("inert"))
      || style?.display === "none"
      || style?.visibility === "hidden"
      || screenReaderOnly;
  };
  const hasHiddenAncestor = (element) => {
    for (let current = element; current; current = current.parentElement) {
      if (isHidden(current)) return true;
    }
    return false;
  };
  const visible = (elements) => [...elements].filter((element) => !hasHiddenAncestor(element));
  const parts = String(artifactKey).split("::");
  const messageId = parts.shift() ?? "";
  const selector = parts.shift() ?? "0";
  const indexedSelector = ["generated", "sandbox", "card"].includes(selector);
  const hasExplicitIndex = indexedSelector && /^\d+$/u.test(parts[0] ?? "");
  const itemIndex = indexedSelector
    ? hasExplicitIndex ? Number(parts.shift()) : 0
    : Number(selector);
  const cardIndex = selector === "card" || !indexedSelector ? itemIndex : Number.NaN;
  const identity = parts.join("::");
  let displayName = identity;
  if (selector === "sandbox") {
    displayName = identity.split("/").at(-1);
    try { displayName = decodeURIComponent(displayName); } catch { /* Keep literal name. */ }
  }
  const locatorSelector = [
    "main [data-testid]",
    "main [data-turn-id]",
    "main [data-turn-key]",
    "main [data-message-id]",
    "main [data-chatgpt-search-message-ids]",
    "main [id]",
  ].join(", ");
  const identifierAttributes = [
    "data-message-id",
    "data-turn-id",
    "data-turn-key",
    "data-testid",
    "id",
  ];
  const boundary = visible(document.querySelectorAll(locatorSelector))
    .find((candidate) => {
      if (identifierAttributes.some((name) => (candidate.getAttribute(name) ?? "").trim() === messageId)) {
        return true;
      }
      const rolloutValue = (candidate.getAttribute("data-chatgpt-search-message-ids") ?? "").trim();
      const rolloutIds = [...new Set(rolloutValue.split(/\s+/u).filter(Boolean))];
      return rolloutIds.length === 1 && rolloutIds[0] === messageId;
    });
  const cards = boundary
    ? visible(boundary.querySelectorAll("[role='group'][aria-label]"))
      .filter((card) => card.querySelector("[data-testid='library-file-icon']"))
    : [];
  const generatedButton = selector === "generated" && boundary
    ? visible(boundary.querySelectorAll("button.group\\/open-file"))[itemIndex] ?? null
    : null;
  const generatedRow = generatedButton?.closest(".group\\/artifact-row");
  const generatedDownload = generatedRow
    ? visible(generatedRow.querySelectorAll("button")).find((button) =>
      /^(下载文件|download file)$/iu.test(button.getAttribute("aria-label") ?? "")) : null;
  const sandboxLink = selector === "sandbox" && boundary
    ? visible(boundary.querySelectorAll('a[href^="sandbox:/"]'))[itemIndex] ?? null
    : null;
  const card = generatedButton ?? sandboxLink ?? cards[cardIndex];
  const identityMatches = generatedButton
    ? generatedButton.getAttribute("aria-label") === displayName
    : sandboxLink
      ? sandboxLink.getAttribute("href") === identity
      : card?.getAttribute("aria-label") === displayName;
  if (!card || !identityMatches) {
    return { key: artifactKey, messageId, displayName, status: "unavailable", error: "文件下载入口已不在页面中。" };
  }

  const findDownloadButton = () => visible(document.querySelectorAll("button")).find((button) => {
    const label = button.getAttribute("aria-label") ?? button.textContent ?? "";
    return /^(下载|download)$/iu.test(label.trim());
  });
  const findCloseButton = () => visible(document.querySelectorAll("button")).find((button) => {
    const label = button.getAttribute("aria-label") ?? button.textContent ?? "";
    return /^(关闭|close)$/iu.test(label.trim());
  });

  let openedPreview = false;
  // Preview/download controls may scroll a pinned or virtualized turn into view.
  // Preserve the scan cursor rather than continuing from the attachment's position.
  const scrollPositions = [];
  for (let parent = card.parentElement; parent; parent = parent.parentElement) {
    if (parent.scrollHeight > parent.clientHeight) {
      scrollPositions.push({ element: parent, top: parent.scrollTop, left: parent.scrollLeft });
    }
  }
  try {
    card.scrollIntoView({ block: "center", behavior: "instant" });
    let downloadButton = generatedDownload ?? sandboxLink;
    if (!downloadButton) {
      openedPreview = true;
      (card.querySelector("button") ?? card).click();
      for (let attempt = 0; attempt < 50 && !downloadButton; attempt += 1) {
        await wait(100);
        downloadButton = findDownloadButton();
      }
    }
    if (!downloadButton) throw new Error("文件预览没有提供下载按钮。");

    const anchorPrototype = HTMLAnchorElement.prototype;
    const originalAnchorClick = anchorPrototype.click;
    const anchorsBeforeDownload = new WeakSet(document.querySelectorAll("a"));
    let resolvePayload;
    const payloadPromise = new Promise((resolve) => { resolvePayload = resolve; });
    let payloadSettled = false;
    const finishPayload = (payload) => {
      if (payloadSettled) return;
      payloadSettled = true;
      resolvePayload(payload);
    };
    const readBlob = async (blob, sourceUrl = null) => {
      if (blob.size > 25 * 1024 * 1024) {
        finishPayload({ status: "too_large", size: blob.size, sourceUrl, error: "文件超过 25 MB 的插件保存上限。" });
        return;
      }
      const bytes = new Uint8Array(await blob.arrayBuffer());
      let binary = "";
      for (let offset = 0; offset < bytes.length; offset += 0x8000) {
        binary += String.fromCharCode(...bytes.subarray(offset, offset + 0x8000));
      }
      finishPayload({
        status: "saved",
        size: blob.size,
        mimeType: blob.type || "application/octet-stream",
        sourceUrl,
        base64: btoa(binary),
      });
    };
    const normalizedFileName = (value) => {
      if (!value) return "";
      const lastSegment = String(value).split(/[\\/]/u).at(-1) ?? "";
      try { return decodeURIComponent(lastSegment).toLocaleLowerCase(); } catch { return lastSegment.toLocaleLowerCase(); }
    };
    const expectedName = normalizedFileName(displayName);
    anchorPrototype.click = function interceptedClick() {
      const href = this.href;
      const declaredName = normalizedFileName(this.download);
      let urlName = "";
      try { urlName = normalizedFileName(new URL(href).pathname); } catch { /* Blob URLs may not expose a name. */ }
      const matchesExpectedName = Boolean(expectedName)
        && (declaredName === expectedName || urlName === expectedName);
      const isNewBlobAnchor = typeof href === "string"
        && href.startsWith("blob:")
        && !anchorsBeforeDownload.has(this);
      if (href && /^(https?:|blob:)/iu.test(href) && (matchesExpectedName || isNewBlobAnchor)) {
        void fetch(href, { credentials: "include" })
          .then((response) => {
            if (!response.ok) throw new Error(`下载请求失败：HTTP ${response.status}`);
            return response.blob();
          })
          .then((blob) => readBlob(blob, href))
          .catch((error) => finishPayload({ status: "unavailable", sourceUrl: href, error: String(error.message ?? error) }));
        return undefined;
      }
      return originalAnchorClick.call(this);
    };

    let timeout;
    try {
      // Call the original method for the page's sandbox link so its handler runs.
      if (sandboxLink) originalAnchorClick.call(sandboxLink);
      else downloadButton.click();
      const payload = await Promise.race([
        payloadPromise,
        new Promise((resolve) => {
          timeout = setTimeout(() => resolve({ status: "unavailable", error: "等待文件下载超时，入口可能已过期。" }), 15000);
        }),
      ]);
      return { key: artifactKey, messageId, displayName, ...payload };
    } finally {
      clearTimeout(timeout);
      anchorPrototype.click = originalAnchorClick;
      if (openedPreview) findCloseButton()?.click();
    }
  } catch (error) {
    if (openedPreview) findCloseButton()?.click();
    return {
      key: artifactKey,
      messageId,
      displayName,
      status: "unavailable",
      error: error instanceof Error ? error.message : String(error),
    };
  } finally {
    for (const { element, top, left } of scrollPositions) {
      element.scrollTo({ top, left, behavior: "instant" });
    }
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
