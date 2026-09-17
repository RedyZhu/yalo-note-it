function inspectChatGptPage(targetPosition) {
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

  function inlineMarkdown(node) {
    if (node.nodeType === Node.TEXT_NODE) return node.textContent ?? "";
    if (node.nodeType !== Node.ELEMENT_NODE) return "";
    const element = node;
    const tag = element.tagName.toLowerCase();
    const content = [...element.childNodes].map(inlineMarkdown).join("");
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

  function blockMarkdown(root) {
    const blocks = [];
    const visit = (element, depth = 0) => {
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
        blocks.push(`${"#".repeat(Number(tag[1]))} ${cleanText(inlineMarkdown(element))}`);
        return;
      }
      if (tag === "blockquote") {
        blocks.push(cleanText(element.innerText).split("\n").map((line) => `> ${line}`).join("\n"));
        return;
      }
      if (tag === "table") {
        const rows = [...element.querySelectorAll("tr")].map((row) =>
          [...row.querySelectorAll(":scope > th, :scope > td")].map((cell) => cleanText(inlineMarkdown(cell))),
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
          .map((item, index) => `${"  ".repeat(depth)}${ordered ? `${index + 1}.` : "-"} ${cleanText(inlineMarkdown(item))}`);
        if (lines.length) blocks.push(lines.join("\n"));
        return;
      }
      if (tag === "p") {
        const text = cleanText(inlineMarkdown(element));
        if (text) blocks.push(text);
        return;
      }
      const blockChildren = [...element.children].filter((child) =>
        /^(P|PRE|H[1-6]|UL|OL|BLOCKQUOTE|TABLE)$/u.test(child.tagName),
      );
      if (blockChildren.length) {
        for (const child of blockChildren) visit(child, depth + 1);
      } else {
        const text = cleanText(inlineMarkdown(element));
        if (text) blocks.push(text);
      }
    };
    visit(root);
    return blocks.join("\n\n").replace(/\n{3,}/gu, "\n\n").trim();
  }

  const turns = [...document.querySelectorAll("main [data-testid^='conversation-turn-']")];
  const scroller = findScroller(turns);
  if (!scroller) return { error: "找不到 ChatGPT 对话滚动区域。" };
  if (targetPosition === "top") scroller.scrollTo({ top: 0, behavior: "instant" });
  if (targetPosition === "next") {
    scroller.scrollTo({
      top: Math.min(
        scroller.scrollHeight - scroller.clientHeight,
        scroller.scrollTop + Math.floor(scroller.clientHeight * 0.75),
      ),
      behavior: "instant",
    });
  }
  const records = turns.map((turn) => {
    const content = turn.querySelector("[data-message-author-role]") ?? turn;
    const attachments = [...turn.querySelectorAll("img")]
      .map((image) => image.getAttribute("alt"))
      .filter(Boolean);
    return {
      id: turn.getAttribute("data-testid") ?? "",
      role: content.getAttribute("data-message-author-role") ?? "unknown",
      markdown: blockMarkdown(content),
      attachments: [...new Set(attachments)],
    };
  });
  const artifacts = [];
  for (const turn of turns) {
    const content = turn.querySelector("[data-message-author-role]") ?? turn;
    if (content.getAttribute("data-message-author-role") !== "user") continue;
    const messageId = turn.getAttribute("data-testid") ?? "";
    const cards = [...turn.querySelectorAll("[role='group'][aria-label]")]
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
    atBottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 3,
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
  const turn = [...document.querySelectorAll("main [data-testid^='conversation-turn-']")]
    .find((candidate) => candidate.getAttribute("data-testid") === messageId);
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
