const LOAD_SETTLE_MS = 4000;
const MAX_RECORD_STEPS = 2000;

let activeRun = null;

const sleep = (milliseconds) =>
  new Promise((resolve) => setTimeout(resolve, milliseconds));

function timestampForPath() {
  return new Date().toISOString().replace(/[:.]/g, "-");
}

async function setBadge(tabId, text, color = "#2563eb") {
  await chrome.action.setBadgeBackgroundColor({ tabId, color });
  await chrome.action.setBadgeText({ tabId, text });
}
async function inspectRecordPage(tabId, moveTo) {
  const [{ result }] = await chrome.scripting.executeScript({
    target: { tabId },
    args: [moveTo],
    func: (targetPosition) => {
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
          top: Math.min(scroller.scrollHeight - scroller.clientHeight, scroller.scrollTop + Math.floor(scroller.clientHeight * 0.75)),
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
      return {
        records,
        scrollTop: scroller.scrollTop,
        scrollHeight: scroller.scrollHeight,
        viewport: scroller.clientHeight,
        atBottom: scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 3,
        title: document.title,
        url: location.href,
      };
    },
  });
  return result;
}

async function loadArchiveRoot() {
  const database = await new Promise((resolve, reject) => {
    const request = indexedDB.open("yalo-note-it", 1);
    request.onupgradeneeded = () => request.result.createObjectStore("settings");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  try {
    return await new Promise((resolve, reject) => {
      const transaction = database.transaction("settings", "readonly");
      const request = transaction.objectStore("settings").get("archive-root");
      request.onsuccess = () => resolve(request.result ?? null);
      request.onerror = () => reject(request.error);
    });
  } finally {
    database.close();
  }
}

async function writeWebRecord(conversationId, body) {
  const root = await loadArchiveRoot();
  if (!root) throw new Error("请先在插件中把记录目录设置为 D:\\MyData\\yalo-note。" );
  if ((await root.queryPermission({ mode: "readwrite" })) !== "granted") {
    throw new Error("记录目录权限已失效，请在插件中重新设置目录。" );
  }
  let directory = root;
  for (const name of ["sessions", "chatgpt", conversationId]) {
    directory = await directory.getDirectoryHandle(name, { create: true });
  }
  const file = await directory.getFileHandle("conversation.raw-record.md", { create: true });
  const writable = await file.createWritable();
  try {
    await writable.write(body);
  } finally {
    await writable.close();
  }
}

async function runRecordExport(tab, cutoffId = null) {
  const run = { tabId: tab.id, stopped: false, startedAt: new Date().toISOString() };
  activeRun = run;
  const records = new Map();
  try {
    await setBadge(tab.id, "REC", "#7c3aed");
    let state = await inspectRecordPage(tab.id, "top");
    if (state?.error) throw new Error(state.error);
    let stableTop = 0;
    let topSignature = "";
    for (let step = 0; step < 20 && !run.stopped; step += 1) {
      await sleep(LOAD_SETTLE_MS);
      state = await inspectRecordPage(tab.id, "top");
      if (state?.error) throw new Error(state.error);
      const signature = `${state.scrollHeight}|${state.records[0]?.id ?? ""}`;
      stableTop = signature === topSignature ? stableTop + 1 : 0;
      topSignature = signature;
      if (stableTop >= 2) break;
    }
    if (stableTop < 2) throw new Error("达到等待上限前仍未确认稳定的对话顶部。");
    state = await inspectRecordPage(tab.id, "none");
    let stableBottom = 0;
    for (let step = 0; step < MAX_RECORD_STEPS && !run.stopped; step += 1) {
      for (const record of state.records) records.set(record.id, record);
      stableBottom = state.atBottom ? stableBottom + 1 : 0;
      if (stableBottom >= 3) break;
      state = await inspectRecordPage(tab.id, "next");
      if (state?.error) throw new Error(state.error);
      await sleep(120);
      state = await inspectRecordPage(tab.id, "none");
      await setBadge(tab.id, String(records.size), "#7c3aed");
    }
    let ordered = [...records.values()].sort((left, right) => {
      const leftNumber = Number(left.id.match(/(\d+)$/u)?.[1] ?? 0);
      const rightNumber = Number(right.id.match(/(\d+)$/u)?.[1] ?? 0);
      return leftNumber - rightNumber;
    });
    if (cutoffId) {
      const cutoffIndex = ordered.findIndex((record) => record.id === cutoffId);
      if (cutoffIndex < 0) throw new Error("没有在完整扫描中找到触发消息，未生成不完整记录。");
      ordered = ordered.slice(0, cutoffIndex + 1);
    }
    const conversationId = state.url.match(/\/c\/([0-9a-f-]+)/iu)?.[1] ?? timestampForPath();
    const lines = [
      "# ChatGPT Conversation Raw Record",
      "",
      `> Source: ${state.url}`,
      `> Page title: ${state.title}`,
      `> Exported at: ${new Date().toISOString()}`,
      `> Message count: ${ordered.length}`,
      `> Cutoff: ${cutoffId ?? "manual-current-page"}`,
      "> This file preserves the conversation order and rendered message content. It is not a summary.",
      "",
    ];
    for (const [index, record] of ordered.entries()) {
      const role = record.role === "user" ? "User" : record.role === "assistant" ? "Assistant" : record.role;
      lines.push(`## ${String(index + 1).padStart(3, "0")} · ${role}`, "", `<!-- ${record.id} -->`);
      for (const attachment of record.attachments) lines.push(`- [附件：${attachment}]`);
      if (record.attachments.length) lines.push("");
      lines.push(record.markdown || "[无法辨认]", "");
    }
    const body = lines.join("\n");
    await writeWebRecord(conversationId, body);
    await setBadge(tab.id, ordered.length ? "OK" : "ERR", ordered.length ? "#16a34a" : "#dc2626");
    return { ok: true, message: `已记录 ${ordered.length} 条消息。` };
  } catch (error) {
    console.error("Raw Record export failed", error);
    await setBadge(tab.id, "ERR", "#dc2626");
    return { ok: false, message: error instanceof Error ? error.message : String(error) };
  } finally {
    if (activeRun === run) activeRun = null;
  }
}

async function handleRecordControl(message) {
  if (message.mode === "stop") {
    if (!activeRun) return { ok: false, message: "当前没有正在执行的任务。" };
    activeRun.stopped = true;
    await setBadge(activeRun.tabId, "…", "#ca8a04");
    return { ok: true, message: "正在停止。" };
  }

  if (message.mode !== "record") return { ok: false, message: "不支持的操作。" };
  if (activeRun) return { ok: false, message: "已有记录任务正在执行。" };

  const tab = await chrome.tabs.get(message.tabId);
  if (!/^https:\/\/(chatgpt\.com|chat\.openai\.com)\//u.test(tab.url || "")) {
    await setBadge(tab.id, "NO", "#dc2626");
    return { ok: false, message: "请先打开目标 ChatGPT 对话。" };
  }

  return runRecordExport(tab);
}

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  if (message?.type !== "record-control") return false;
  handleRecordControl(message)
    .then(sendResponse)
    .catch((error) => sendResponse({
      ok: false,
      message: error instanceof Error ? error.message : String(error),
    }));
  return true;
});
