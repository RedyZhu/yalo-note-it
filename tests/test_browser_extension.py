import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser-extension" / "yalo-note-it"


CHATGPT_DOM_HARNESS = r"""
function attributeValue(element, name) {
  return Object.prototype.hasOwnProperty.call(element.attributes, name)
    ? element.attributes[name]
    : null;
}

function matchesSimpleSelector(element, selector) {
  const trimmed = selector.trim();
  const tagMatch = trimmed.match(/^[a-z][a-z0-9-]*/iu);
  if (tagMatch && element.tagName !== tagMatch[0].toUpperCase()) return false;
  const classNames = [...trimmed.matchAll(/\.([a-zA-Z0-9_-]+)/gu)]
    .map((match) => match[1]);
  const elementClasses = String(attributeValue(element, "class") ?? "")
    .split(/\s+/u)
    .filter(Boolean);
  if (!classNames.every((className) => elementClasses.includes(className))) return false;
  const attributes = [...trimmed.matchAll(/\[([^\]=~^$*]+)(?:(\^=|\$=|=)'([^']*)')?\]/gu)];
  if (!tagMatch && !classNames.length && !attributes.length) return false;
  return attributes.every((match) => {
    const value = attributeValue(element, match[1]);
    if (!match[2]) return value !== null;
    if (value === null) return false;
    if (match[2] === "=") return value === match[3];
    if (match[2] === "^=") return value.startsWith(match[3]);
    if (match[2] === "$=") return value.endsWith(match[3]);
    return false;
  });
}

function matchesSelector(element, selector) {
  return selector.split(",").some((part) => matchesSimpleSelector(element, part));
}

class FakeElement {
  constructor(tagName, attributes = {}, text = null) {
    this.nodeType = 1;
    this.tagName = tagName.toUpperCase();
    this.attributes = { ...attributes };
    this.childNodes = [];
    this.children = [];
    this.parentElement = null;
    this.clientHeight = 0;
    this.scrollHeight = 0;
    this.scrollTop = 0;
    this.clicked = false;
    this.classList = String(attributes.class ?? "").split(/\s+/u).filter(Boolean);
    if (text !== null) this.appendText(text);
  }

  appendText(text) {
    this.childNodes.push({ nodeType: 3, textContent: text, parentElement: this });
    return this;
  }

  append(child) {
    child.parentElement = this;
    this.childNodes.push(child);
    this.children.push(child);
    return this;
  }

  getAttribute(name) {
    return attributeValue(this, name);
  }

  matches(selector) {
    return matchesSelector(this, selector);
  }

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

  querySelector(selector) {
    return this.querySelectorAll(selector)[0] ?? null;
  }

  scrollTo(options) {
    this.scrollTop = options.top;
  }

  scrollIntoView() {}

  click() {
    this.clicked = true;
  }

  get innerText() {
    return this.childNodes.map((child) =>
      child.nodeType === 3 ? child.textContent : child.innerText,
    ).join("");
  }

  get textContent() {
    return this.innerText;
  }
}

function installDocument(roots, url = "https://chatgpt.com/c/test") {
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
    title: "Fixture conversation",
    scrollingElement,
    querySelectorAll: (selector) => allElements().filter((element) => element.matches(selector)),
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; },
  };
  global.location = { href: url };
}
"""


def run_chatgpt_adapter_script(body):
    adapter = EXTENSION / "providers" / "chatgpt.js"
    script = "\n".join(
        (
            "global.self = globalThis;",
            "global.Node = { TEXT_NODE: 3, ELEMENT_NODE: 1 };",
            "global.innerHeight = 900;",
            "global.getComputedStyle = () => ({ overflowY: 'visible' });",
            CHATGPT_DOM_HARNESS,
            f"require({json.dumps(str(adapter))});",
            body,
        )
    )
    result = subprocess.run(
        ["node", "-e", script],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return json.loads(result.stdout)


class BrowserExtensionAdapterTests(unittest.TestCase):
    def test_manifest_declares_existing_icon_assets(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        expected_sizes = {"16", "32", "48", "128"}
        self.assertEqual(set(manifest["icons"]), expected_sizes)
        self.assertEqual(set(manifest["action"]["default_icon"]), {"16", "32"})
        for relative_path in manifest["icons"].values():
            self.assertTrue((EXTENSION / relative_path).is_file(), relative_path)

    def test_chatgpt_adapter_registration_and_url_contract(self):
        adapter = EXTENSION / "providers" / "chatgpt.js"
        script = f"""
global.self = globalThis;
require({json.dumps(str(adapter))});
const provider = self.YaloNoteProviders[0];
console.log(JSON.stringify({{
  id: provider.id,
  label: provider.label,
  chatgpt: provider.matchesUrl('https://chatgpt.com/c/123'),
  projectChat: provider.matchesUrl('https://chatgpt.com/g/g-p-brand/c/abc123'),
  legacy: provider.matchesUrl('https://chat.openai.com/c/123'),
  qwen: provider.matchesUrl('https://www.qianwen.com/chat/123'),
  conversationId: provider.conversationId('https://chatgpt.com/c/6a9a7f35-e334-83e8-a4cf-d938573b1910'),
  projectConversationId: provider.conversationId(
    'https://chatgpt.com/g/g-p-69284e6252ac8191a7b1182e87632bf3-man-shi/c/6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be'
  ),
}}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        contract = json.loads(result.stdout)
        self.assertEqual(contract["id"], "chatgpt")
        self.assertEqual(contract["label"], "ChatGPT")
        self.assertTrue(contract["chatgpt"])
        self.assertTrue(contract["projectChat"])
        self.assertTrue(contract["legacy"])
        self.assertFalse(contract["qwen"])
        self.assertEqual(
            contract["conversationId"],
            "6a9a7f35-e334-83e8-a4cf-d938573b1910",
        )
        self.assertEqual(
            contract["projectConversationId"],
            "6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be",
        )

    def test_chatgpt_adapter_exposes_optional_artifact_capture(self):
        adapter = EXTENSION / "providers" / "chatgpt.js"
        script = f"""
global.self = globalThis;
require({json.dumps(str(adapter))});
const provider = self.YaloNoteProviders[0];
console.log(JSON.stringify({{
  inspectPage: typeof provider.inspectPage,
  captureArtifact: typeof provider.captureArtifact,
}}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        contract = json.loads(result.stdout)
        self.assertEqual(contract["inspectPage"], "function")
        self.assertEqual(contract["captureArtifact"], "function")

    def test_chatgpt_discovers_role_messages_inside_data_turn_id_wrappers(self):
        result = run_chatgpt_adapter_script(
            r"""
const user = new FakeElement("div", {
  "data-message-author-role": "user",
  "data-message-id": "message-user",
}, "用户问题");
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "message-assistant",
}, "助手回答");
const userTurn = new FakeElement("div", { "data-turn-id": "turn-user" }).append(user);
const assistantTurn = new FakeElement("div", { "data-turn-id": "turn-assistant" }).append(assistant);
installDocument(
  [userTurn, assistantTurn],
  "https://chatgpt.com/g/g-p-brand/c/6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be",
);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(
            result["records"],
            [
                {
                    "id": "message-user",
                    "role": "user",
                    "markdown": "用户问题",
                    "attachments": [],
                },
                {
                    "id": "message-assistant",
                    "role": "assistant",
                    "markdown": "助手回答",
                    "attachments": [],
                },
            ],
        )

    def test_chatgpt_accepts_role_node_as_the_turn_owner(self):
        result = run_chatgpt_adapter_script(
            r"""
const turn = new FakeElement("article", {
  "data-turn-id": "turn-direct",
  "data-message-author-role": "assistant",
}, "直接挂在 turn 上的回答");
installDocument([turn]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["id"], "turn-direct")
        self.assertEqual(result["records"][0]["role"], "assistant")
        self.assertEqual(result["records"][0]["markdown"], "直接挂在 turn 上的回答")

    def test_chatgpt_rejects_message_without_a_stable_id(self):
        result = run_chatgpt_adapter_script(
            r"""
const message = new FakeElement("div", {
  "data-message-author-role": "user",
}, "没有稳定 ID");
installDocument([message]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertIn("缺少稳定 ID", result["error"])
        self.assertNotIn("records", result)

    def test_chatgpt_rollout_message_ids_must_resolve_to_one_id(self):
        result = run_chatgpt_adapter_script(
            r"""
const single = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "rollout:assistant",
  "data-chatgpt-search-message-ids": "message-single",
}).append(new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
}, "rollout 回答"));
const singleOwner = new FakeElement("article", { "data-turn": "assistant" }).append(single);
installDocument([singleOwner]);
const singleResult = self.YaloNoteProviders[0].inspectPage("none");

const ambiguous = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "rollout:user",
  "data-chatgpt-search-message-ids": "message-a message-b",
}, "歧义消息");
const ambiguousOwner = new FakeElement("article", { "data-turn": "user" }).append(ambiguous);
installDocument([ambiguousOwner]);
const ambiguousResult = self.YaloNoteProviders[0].inspectPage("none");
console.log(JSON.stringify({ singleResult, ambiguousResult }));
"""
        )

        self.assertEqual(result["singleResult"]["records"][0]["id"], "message-single")
        self.assertIn("消息 ID 存在歧义", result["ambiguousResult"]["error"])

    def test_chatgpt_rollout_keeps_both_roles_in_a_shared_turn_owner(self):
        result = run_chatgpt_adapter_script(
            r"""
const userImage = new FakeElement("img", { alt: "用户上传图" });
const assistantImage = new FakeElement("img", { alt: "回答引用图" });
const user = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "shared-turn:user",
  "data-chatgpt-search-message-ids": "message-user",
}, "用户问题").append(userImage);
const assistant = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "shared-turn:assistant",
  "data-chatgpt-search-message-ids": "message-assistant message-assistant",
})
  .append(new FakeElement("div", {
    "data-markdown-text-style": "assistant-message",
  }, "助手回答"))
  .append(assistantImage);
const owner = new FakeElement("article", { "data-turn-key": "shared-turn" })
  .append(user)
  .append(assistant);
installDocument([owner]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(
            [(record["id"], record["role"]) for record in result["records"]],
            [
                ("message-user", "user"),
                ("message-assistant", "assistant"),
            ],
        )
        self.assertEqual(result["records"][0]["attachments"], ["用户上传图"])
        self.assertEqual(result["records"][1]["attachments"], ["回答引用图"])

    def test_chatgpt_extracts_current_assistant_body_without_screen_reader_label(self):
        result = run_chatgpt_adapter_script(
            r"""
const label = new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说：");
const strong = new FakeElement("strong", {}, "重点");
const link = new FakeElement("a", { href: "https://example.com/source" }, "来源");
const paragraph = new FakeElement("p")
  .appendText("真正回答 ")
  .append(strong)
  .appendText("，参见 ")
  .append(link)
  .appendText("，引用 ")
  .append(new FakeElement("button", {
    "data-testid": "chatgpt-library-file-citation",
  }, "品牌资料.docx"));
const body = new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
  class: "MarkdownRoot",
})
  .append(new FakeElement("h2", {}, "结论"))
  .append(paragraph);
const shell = new FakeElement("div")
  .append(label)
  .append(new FakeElement("div").append(body));
const assistant = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "fallback-turn-5:2:assistant",
  "data-chatgpt-search-message-ids": "assistant-current assistant-current",
}).append(shell);
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["id"], "assistant-current")
        self.assertEqual(
            result["records"][0]["markdown"],
            "## 结论\n\n真正回答 **重点**，参见 [来源](https://example.com/source)，引用 品牌资料.docx",
        )
        self.assertNotIn("ChatGPT 说", result["records"][0]["markdown"])

    def test_chatgpt_extracts_legacy_markdown_assistant_body(self):
        result = run_chatgpt_adapter_script(
            r"""
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "assistant-legacy",
})
  .append(new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说："))
  .append(
    new FakeElement("div", { class: "markdown prose" })
      .append(new FakeElement("p", {}, "旧版正文")),
  );
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["records"][0]["markdown"], "旧版正文")

    def test_chatgpt_prefers_semantic_body_over_outer_legacy_container(self):
        result = run_chatgpt_adapter_script(
            r"""
const body = new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
}).append(new FakeElement("p", {}, "精确正文"));
const legacy = new FakeElement("div", { class: "markdown" })
  .append(body)
  .append(new FakeElement("button", {}, "复制"));
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "assistant-priority",
}).append(legacy);
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["records"][0]["markdown"], "精确正文")

    def test_chatgpt_ignores_hidden_semantic_answer_variant(self):
        result = run_chatgpt_adapter_script(
            r"""
const oldBody = new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
}).append(new FakeElement("p", {}, "隐藏旧答案"));
const hiddenVariant = new FakeElement("div", { "aria-hidden": "true" }).append(oldBody);
const visibleBody = new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
}).append(new FakeElement("p", {}, "当前答案"));
const assistant = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "fallback-turn-6:2:assistant",
  "data-chatgpt-search-message-ids": "assistant-visible assistant-visible",
})
  .append(hiddenVariant)
  .append(visibleBody);
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["records"][0]["markdown"], "当前答案")
        self.assertNotIn("隐藏旧答案", result["records"][0]["markdown"])

    def test_chatgpt_ignores_hidden_rollout_message_with_same_id(self):
        result = run_chatgpt_adapter_script(
            r"""
function rolloutAssistant(text) {
  return new FakeElement("div", {
    "data-chatgpt-search-unit-key": "fallback-turn-6:2:assistant",
    "data-chatgpt-search-message-ids": "assistant-shared assistant-shared",
  }).append(new FakeElement("div", {
    "data-markdown-text-style": "assistant-message",
  }).append(new FakeElement("p", {}, text)));
}
const hiddenVariant = new FakeElement("div", { "aria-hidden": "true" })
  .append(rolloutAssistant("隐藏整条旧答案"));
const visibleVariant = rolloutAssistant("当前可见答案");
installDocument([hiddenVariant, visibleVariant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(len(result["records"]), 1)
        self.assertEqual(result["records"][0]["id"], "assistant-shared")
        self.assertEqual(result["records"][0]["markdown"], "当前可见答案")

    def test_chatgpt_recurses_into_nested_assistant_wrappers_without_semantic_marker(self):
        result = run_chatgpt_adapter_script(
            r"""
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "assistant-fallback",
})
  .append(new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说："))
  .append(
    new FakeElement("div")
      .append(new FakeElement("div").append(new FakeElement("p", {}, "嵌套正文"))),
  );
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertNotIn("error", result)
        self.assertEqual(result["records"][0]["markdown"], "嵌套正文")

    def test_chatgpt_rejects_assistant_message_without_extracted_body(self):
        result = run_chatgpt_adapter_script(
            r"""
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "assistant-empty",
}).append(new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说："));
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertIn("未提取到正文", result["error"])
        self.assertNotIn("records", result)

    def test_chatgpt_rejects_rollout_assistant_with_only_action_controls(self):
        result = run_chatgpt_adapter_script(
            r"""
const assistant = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "fallback-turn-7:1:assistant",
  "data-chatgpt-search-message-ids": "assistant-action assistant-action",
})
  .append(new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说："))
  .append(new FakeElement("button", {}, "复制"));
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertIn("缺少可见正文节点", result["error"])
        self.assertIn("assistant-action", result["error"])
        self.assertNotIn("records", result)

    def test_chatgpt_rejects_legacy_fallback_with_only_action_controls(self):
        result = run_chatgpt_adapter_script(
            r"""
const assistant = new FakeElement("div", {
  "data-message-author-role": "assistant",
  "data-message-id": "assistant-legacy-action",
})
  .append(new FakeElement("h4", { class: "sr-only" }, "ChatGPT 说："))
  .append(new FakeElement("button", {}, "复制"));
installDocument([assistant]);
console.log(JSON.stringify(self.YaloNoteProviders[0].inspectPage("none")));
"""
        )

        self.assertIn("未提取到正文", result["error"])
        self.assertIn("assistant-legacy-action", result["error"])
        self.assertNotIn("records", result)

    def test_chatgpt_scrolls_column_reverse_conversation_in_logical_order(self):
        result = run_chatgpt_adapter_script(
            r"""
const user = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "reverse-turn:user",
  "data-chatgpt-search-message-ids": "reverse-user",
}, "用户问题");
const assistant = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "reverse-turn:assistant",
  "data-chatgpt-search-message-ids": "reverse-assistant",
}).append(new FakeElement("div", {
  "data-markdown-text-style": "assistant-message",
}, "助手回答"));
const owner = new FakeElement("article", { "data-turn-key": "reverse-turn" })
  .append(user)
  .append(assistant);
const scroller = new FakeElement("div", { class: "thread-scroll-container" }).append(owner);
scroller.clientHeight = 800;
scroller.scrollHeight = 4000;
scroller.scrollTop = 0;
global.getComputedStyle = (element) => ({
  overflowY: element === scroller ? "auto" : "visible",
  flexDirection: element === scroller ? "column-reverse" : "column",
});
installDocument([scroller]);
const topResult = self.YaloNoteProviders[0].inspectPage("top");
const nextResult = self.YaloNoteProviders[0].inspectPage("next");
scroller.scrollTop = 0.5;
const bottomResult = self.YaloNoteProviders[0].inspectPage("none");
console.log(JSON.stringify({ topResult, nextResult, bottomResult }));
"""
        )

        self.assertEqual(result["topResult"]["scrollTop"], -3200)
        self.assertTrue(result["topResult"]["atTop"])
        self.assertFalse(result["topResult"]["atBottom"])
        self.assertEqual(result["nextResult"]["scrollTop"], -2600)
        self.assertFalse(result["nextResult"]["atTop"])
        self.assertFalse(result["nextResult"]["atBottom"])
        self.assertFalse(result["bottomResult"]["atTop"])
        self.assertTrue(result["bottomResult"]["atBottom"])

    def test_chatgpt_artifact_capture_finds_owner_by_nested_message_id(self):
        result = run_chatgpt_adapter_script(
            r"""
global.setTimeout = (callback) => { callback(); return 0; };
const icon = new FakeElement("span", { "data-testid": "library-file-icon" });
const card = new FakeElement("div", {
  role: "group",
  "aria-label": "资料.pdf",
}).append(icon);
const message = new FakeElement("div", {
  "data-message-author-role": "user",
  "data-message-id": "message-with-file",
});
const owner = new FakeElement("article", { "data-turn": "user" })
  .append(message)
  .append(card);
installDocument([owner]);
self.YaloNoteProviders[0]
  .captureArtifact("message-with-file::0::资料.pdf")
  .then((captureResult) => console.log(JSON.stringify({ captureResult, cardClicked: card.clicked })));
"""
        )

        self.assertTrue(result["cardClicked"])
        self.assertEqual(result["captureResult"]["status"], "unavailable")
        self.assertIn("没有提供下载按钮", result["captureResult"]["error"])

    def test_chatgpt_artifact_capture_normalizes_repeated_rollout_message_id(self):
        result = run_chatgpt_adapter_script(
            r"""
global.setTimeout = (callback) => { callback(); return 0; };
const icon = new FakeElement("span", { "data-testid": "library-file-icon" });
const card = new FakeElement("div", {
  role: "group",
  "aria-label": "rollout.pdf",
}).append(icon);
const message = new FakeElement("div", {
  "data-chatgpt-search-unit-key": "rollout:user",
  "data-chatgpt-search-message-ids": "rollout-message rollout-message",
});
const owner = new FakeElement("article", { "data-turn-key": "rollout-turn" })
  .append(message)
  .append(card);
installDocument([owner]);
self.YaloNoteProviders[0]
  .captureArtifact("rollout-message::0::rollout.pdf")
  .then((captureResult) => console.log(JSON.stringify({ captureResult, cardClicked: card.clicked })));
"""
        )

        self.assertTrue(result["cardClicked"])
        self.assertEqual(result["captureResult"]["status"], "unavailable")
        self.assertIn("没有提供下载按钮", result["captureResult"]["error"])

    def test_deepseek_adapter_registration_and_url_contract(self):
        adapter = EXTENSION / "providers" / "deepseek.js"
        script = f"""
global.self = globalThis;
require({json.dumps(str(adapter))});
const provider = self.YaloNoteProviders[0];
console.log(JSON.stringify({{
  id: provider.id,
  label: provider.label,
  conversation: provider.matchesUrl('https://chat.deepseek.com/a/chat/s/74fefb51-4348-45ab-8962-b0684a074ddd'),
  home: provider.matchesUrl('https://chat.deepseek.com/'),
  chatgpt: provider.matchesUrl('https://chatgpt.com/c/123'),
  conversationId: provider.conversationId('https://chat.deepseek.com/a/chat/s/74fefb51-4348-45ab-8962-b0684a074ddd'),
}}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        contract = json.loads(result.stdout)
        self.assertEqual(contract["id"], "deepseek")
        self.assertEqual(contract["label"], "DeepSeek")
        self.assertTrue(contract["conversation"])
        self.assertFalse(contract["home"])
        self.assertFalse(contract["chatgpt"])
        self.assertEqual(
            contract["conversationId"],
            "74fefb51-4348-45ab-8962-b0684a074ddd",
        )

    def test_deepseek_adapter_excludes_reasoning_and_keeps_attachment_names(self):
        adapter = EXTENSION / "providers" / "deepseek.js"
        script = f"""
global.self = globalThis;
global.Node = {{ TEXT_NODE: 3, ELEMENT_NODE: 1 }};
global.location = {{ href: 'https://chat.deepseek.com/a/chat/s/example-id' }};
function text(value) {{ return {{ nodeType: 3, textContent: value }}; }}
function content(value) {{
  return {{ nodeType: 1, tagName: 'DIV', childNodes: [text(value)], children: [], innerText: value,
    querySelector: () => null, querySelectorAll: () => [] }};
}}
const finalAnswer = content('最终回答');
const userAnswer = content('用户问题');
const card = {{ innerText: '资料.pdf\\nPDF 12KB' }};
const turns = [
  {{ getAttribute: () => '1', querySelector: (selector) => selector === '.ds-collapsible-text' ? userAnswer : null,
    querySelectorAll: (selector) => selector.includes('tabindex') ? [card] : [] }},
  {{ getAttribute: () => '2', querySelector: (selector) => selector === '.ds-assistant-message-main-content' ? finalAnswer : null,
    querySelectorAll: () => [], innerText: '已思考\\n隐藏推理\\n最终回答' }},
];
const scroller = {{ scrollTop: 0, scrollHeight: 100, clientHeight: 100, scrollTo: () => {{}} }};
global.document = {{ title: 'Test', querySelector: (selector) => selector === '.ds-virtual-list' ? scroller : null,
  querySelectorAll: (selector) => selector === '[data-virtual-list-item-key]' ? turns : [] }};
require({json.dumps(str(adapter))});
const result = self.YaloNoteProviders[0].inspectPage('none');
console.log(JSON.stringify(result.records));
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        records = json.loads(result.stdout)
        self.assertEqual(records[0]["role"], "user")
        self.assertEqual(records[0]["markdown"], "用户问题")
        self.assertEqual(records[0]["attachments"], ["资料.pdf"])
        self.assertEqual(records[1]["role"], "assistant")
        self.assertEqual(records[1]["markdown"], "最终回答")
        self.assertNotIn("隐藏推理", records[1]["markdown"])

    def test_manifest_version_matches_documented_release(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        readme = (EXTENSION / "README.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0.12.7")
        self.assertIn("当前版本：**0.12.7**", readme)

    def test_record_button_can_restore_saved_directory_permission(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertIn('handle.queryPermission({ mode: "readwrite" })', popup)
        self.assertIn('handle.requestPermission({ mode: "readwrite" })', popup)
        self.assertIn('await verifyWritable(handle)', popup)
        self.assertIn('正在确认记录目录权限与写入能力', popup)
        self.assertLess(
            popup.index('await verifyWritable(handle)', popup.index('async function control')),
            popup.index('chrome.runtime.sendMessage', popup.index('async function control')),
        )

    def test_configuration_selects_codex_initialized_root_without_browser_migration(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertIn('const ARCHIVE_FOLDER_NAME = "YaloNote"', popup)
        self.assertIn('["yalonote", "yalo note", "yalo-note"]', popup)
        self.assertIn('getDirectoryHandle(ARCHIVE_FOLDER_NAME)', popup)
        self.assertNotIn('getDirectoryHandle(ARCHIVE_FOLDER_NAME, { create: true })', popup)
        self.assertIn('RECOGNIZED_ARCHIVE_FOLDER_NAMES.has(base.name.toLocaleLowerCase())', popup)
        self.assertIn('await self.YaloArchiveAccess.readArchiveLocation(handle)', popup)
        self.assertIn('所选目录中没有 Codex 已配置的 YaloNote 文件夹', popup)
        self.assertLess(
            popup.index('await self.YaloArchiveAccess.readArchiveLocation(handle)'),
            popup.index('await self.YaloArchiveAccess.saveArchiveRoot(handle)'),
        )
        self.assertNotIn('copyDirectory', popup)
        self.assertNotIn('removeDirectoryContents', popup)
        self.assertNotIn('window.confirm', popup)
        self.assertNotIn('D:\\\\MyData\\\\yalo-note', popup)

    def test_directory_actions_open_current_archive_with_file_url(self):
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        self.assertGreater(html.index('id="status"'), html.index('id="stop"'))
        self.assertGreater(html.index('id="open-archive"'), html.index('id="status"'))
        self.assertLess(html.index('id="open-archive"'), html.index('id="configure"'))
        self.assertIn('class="directory-actions"', html)
        self.assertIn('button:disabled { cursor: not-allowed;', html)
        self.assertNotIn('button:disabled { cursor: wait;', html)
        self.assertNotIn('id="open-directory"', html)
        self.assertNotIn('openArchiveRoot', popup)
        self.assertNotIn('yalo-note-open', popup)
        self.assertNotIn('showDirectoryPicker({ startIn:', popup)
        self.assertIn('conversationDirectoryFileUrl', popup)
        self.assertIn('chrome.extension.isAllowedFileSchemeAccess()', popup)
        self.assertNotIn('file:///*', manifest["host_permissions"])
        self.assertIn('file:///*', manifest["optional_host_permissions"])
        self.assertTrue((EXTENSION / "archive-access.js").is_file())
        self.assertFalse((EXTENSION / "archive.html").exists())
        self.assertFalse((EXTENSION / "archive.js").exists())
        self.assertIn('Codex', html)

    def test_archive_access_resolves_identity_and_never_creates_entries(self):
        chatgpt = EXTENSION / "providers" / "chatgpt.js"
        deepseek = EXTENSION / "providers" / "deepseek.js"
        archive_access = EXTENSION / "archive-access.js"
        conversation_id = "6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be"
        source_url = (
            "https://chatgpt.com/g/g-69284e6252ac8191a7b1182e87632bf3-man-shi/"
            f"c/{conversation_id}"
        )
        archive_root = r"F:\My Files\石#\literal%25\%2e%2e\YaloNote"
        script = f"""
global.self = globalThis;
require({json.dumps(str(chatgpt))});
require({json.dumps(str(deepseek))});
require({json.dumps(str(archive_access))});

const accesses = [];
const notFound = () => Object.assign(new Error('missing'), {{ name: 'NotFoundError' }});
function directory(children = {{}}, files = {{}}, name = '') {{
  return {{
    name,
    async getDirectoryHandle(name, options) {{
      accesses.push({{ type: 'directory', name, create: options?.create === true }});
      if (options?.create) throw new Error('read path attempted to create a directory');
      if (!(name in children)) throw notFound();
      return children[name];
    }},
    async getFileHandle(name, options) {{
      accesses.push({{ type: 'file', name, create: options?.create === true }});
      if (options?.create) throw new Error('read path attempted to create a file');
      if (!(name in files)) throw notFound();
      const contents = files[name];
      return {{
        getFile: async () => ({{
          size: Buffer.byteLength(contents),
          text: async () => contents,
        }}),
      }};
    }},
  }};
}}

const conversationId = {json.dumps(conversation_id)};
const sourceUrl = {json.dumps(source_url)};
const manifest = JSON.stringify({{
  schemaVersion: 1,
  provider: 'chatgpt',
  conversationId,
  sourceUrl,
  exportedAt: '2026-10-08T00:00:00.000Z',
  artifacts: [],
}});
const location = JSON.stringify({{
  schemaVersion: 1,
  kind: 'yalo-note-archive-root',
  archive_root: {json.dumps(archive_root)},
}});
const conversation = directory({{}}, {{
  'manifest.json': manifest,
  'conversation.raw-record.md': '# Raw Record\\n',
}});
const root = directory({{
  sessions: directory({{
    chatgpt: directory({{ [conversationId]: conversation }}),
  }}),
}}, {{ '.yalo-note-location.json': location }}, 'YaloNote');

(async () => {{
  const context = self.YaloArchiveAccess.contextForUrl(sourceUrl);
  const archive = await self.YaloArchiveAccess.readConversationArchive(
    root,
    context.providerId,
    context.conversationId,
  );
  const archiveUrl = await self.YaloArchiveAccess.conversationDirectoryFileUrl(
    root,
    context.providerId,
    context.conversationId,
  );

  const missingRoot = directory({{
    sessions: directory({{ chatgpt: directory() }}),
  }}, {{}}, 'YaloNote');
  let missingCode = null;
  try {{
    await self.YaloArchiveAccess.readConversationArchive(
      missingRoot,
      'chatgpt',
      conversationId,
    );
  }} catch (error) {{
    missingCode = error.code;
  }}

  const mismatchedConversation = directory({{}}, {{
    'manifest.json': JSON.stringify({{
      schemaVersion: 1,
      provider: 'chatgpt',
      conversationId: 'another-conversation',
      sourceUrl,
      exportedAt: '2026-10-08T00:00:00.000Z',
      artifacts: [],
    }}),
    'conversation.raw-record.md': '# Wrong Record\\n',
  }});
  const mismatchRoot = directory({{
    sessions: directory({{
      chatgpt: directory({{ [conversationId]: mismatchedConversation }}),
    }}),
  }});
  let mismatchCode = null;
  try {{
    await self.YaloArchiveAccess.readConversationArchive(
      mismatchRoot,
      'chatgpt',
      conversationId,
    );
  }} catch (error) {{
    mismatchCode = error.code;
  }}

  let missingLocationCode = null;
  try {{
    await self.YaloArchiveAccess.readArchiveLocation(missingRoot);
  }} catch (error) {{
    missingLocationCode = error.code;
  }}

  const wrongLocationRoot = directory({{}}, {{
    '.yalo-note-location.json': JSON.stringify({{
      schemaVersion: 1,
      kind: 'yalo-note-archive-root',
      archive_root: 'F:\\\\Elsewhere\\\\DifferentRoot',
    }}),
  }}, 'YaloNote');
  let invalidLocationCode = null;
  try {{
    await self.YaloArchiveAccess.readArchiveLocation(wrongLocationRoot);
  }} catch (error) {{
    invalidLocationCode = error.code;
  }}

  async function locationCode(contents, name = 'YaloNote') {{
    try {{
      await self.YaloArchiveAccess.readArchiveLocation(directory(
        {{}},
        {{ '.yalo-note-location.json': contents }},
        name,
      ));
      return null;
    }} catch (error) {{
      return error.code;
    }}
  }}
  const invalidLocationCodes = {{
    oversized: await locationCode('x'.repeat((16 * 1024) + 1)),
    badJson: await locationCode('{{'),
    badSchema: await locationCode(JSON.stringify({{
      schemaVersion: 2,
      kind: 'yalo-note-archive-root',
      archive_root: 'F:\\\\My Files\\\\YaloNote',
    }})),
    badKind: await locationCode(JSON.stringify({{
      schemaVersion: 1,
      kind: 'something-else',
      archive_root: 'F:\\\\My Files\\\\YaloNote',
    }})),
    wrongBasename: await locationCode(JSON.stringify({{
      schemaVersion: 1,
      kind: 'yalo-note-archive-root',
      archive_root: 'F:\\\\My Files\\\\DifferentRoot',
    }})),
  }};

  const beforeInvalid = accesses.length;
  let invalidCode = null;
  try {{
    await self.YaloArchiveAccess.readConversationArchive(root, 'chatgpt', '../secret');
  }} catch (error) {{
    invalidCode = error.code;
  }}

  console.log(JSON.stringify({{
    context,
    manifest: archive.manifest,
    archiveUrl,
    missingCode,
    mismatchCode,
    missingLocationCode,
    invalidLocationCode,
    invalidLocationCodes,
    invalidCode,
    invalidAccesses: accesses.length - beforeInvalid,
    created: accesses.filter((entry) => entry.create).length,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertEqual(state["context"]["providerId"], "chatgpt")
        self.assertEqual(state["context"]["conversationId"], conversation_id)
        self.assertEqual(state["manifest"]["sourceUrl"], source_url)
        self.assertEqual(
            state["archiveUrl"],
            "file:///F:/My%20Files/%E7%9F%B3%23/literal%2525/"
            f"%252e%252e/YaloNote/sessions/chatgpt/{conversation_id}/",
        )
        self.assertEqual(state["missingCode"], "not-found")
        self.assertEqual(state["mismatchCode"], "identity-mismatch")
        self.assertEqual(state["missingLocationCode"], "location-missing")
        self.assertEqual(state["invalidLocationCode"], "location-invalid")
        self.assertEqual(
            set(state["invalidLocationCodes"].values()),
            {"location-invalid"},
        )
        self.assertEqual(state["invalidCode"], "invalid-context")
        self.assertEqual(state["invalidAccesses"], 0)
        self.assertEqual(state["created"], 0)

    def test_popup_opens_only_an_existing_archive_for_the_active_url(self):
        popup = EXTENSION / "popup.js"
        conversation_id = "6ac26ed6-d71c-83ee-bd8d-fe1a2112c8be"
        archive_url = (
            f"file:///F:/My%20Files/YaloNote/sessions/chatgpt/{conversation_id}/"
        )
        script = f"""
global.self = globalThis;
class FakeElement {{
  constructor() {{
    this.disabled = false;
    this.textContent = '';
    this.dataset = {{}};
    this.listeners = {{}};
  }}
  addEventListener(type, listener) {{ this.listeners[type] = listener; }}
  async click() {{ return await this.listeners.click(); }}
}}
const elements = {{
  '#status': new FakeElement(),
  '#record': new FakeElement(),
  '#stop': new FakeElement(),
  '#open-archive': new FakeElement(),
  '#configure': new FakeElement(),
}};
global.document = {{
  querySelector: (selector) => elements[selector],
  querySelectorAll: (selector) => selector === 'button'
    ? [elements['#record'], elements['#stop'], elements['#open-archive'], elements['#configure']]
    : [],
}};
global.window = {{ close: () => {{}}, confirm: () => false }};

const events = [];
const openedTabs = [];
const root = {{
  name: 'YaloNote',
  queryPermission: async () => 'granted',
}};
let mode = 'existing';
self.YaloArchiveAccess = {{
  contextForUrl: (url) => mode === 'unsupported' ? null : ({{
    providerId: 'chatgpt',
    conversationId: {json.dumps(conversation_id)},
    pageUrl: url,
  }}),
  readArchiveRoot: async () => root,
  ensureReadable: async () => {{ events.push('permission'); }},
  readConversationArchive: async () => {{
    events.push('lookup');
    if (mode === 'missing') throw new Error('当前会话尚无归档。');
    if (mode === 'mismatch') throw new Error('当前会话归档的身份信息与页面 URL 不匹配。');
    return {{}};
  }},
  conversationDirectoryFileUrl: async () => {{
    events.push('location');
    return {json.dumps(archive_url)};
  }},
}};
global.chrome = {{
  tabs: {{
    query: async () => [{{
      id: 17,
      url: 'https://chatgpt.com/g/project/c/{conversation_id}',
    }}],
    create: async (options) => {{ events.push('open'); openedTabs.push(options.url); }},
  }},
  extension: {{
    isAllowedFileSchemeAccess: async () => {{
      events.push('file-access');
      return mode !== 'file-denied';
    }},
  }},
  runtime: {{
    sendMessage: async () => ({{ ok: true, error: null }}),
  }},
  action: {{ getBadgeText: async () => '' }},
}};

require({json.dumps(str(popup))});
(async () => {{
  await new Promise((resolve) => setImmediate(resolve));
  await elements['#open-archive'].click();
  const existingStatus = elements['#status'].textContent;
  mode = 'missing';
  await elements['#open-archive'].click();
  const missingStatus = elements['#status'].textContent;
  mode = 'unsupported';
  await elements['#open-archive'].click();
  const unsupportedStatus = elements['#status'].textContent;
  mode = 'mismatch';
  await elements['#open-archive'].click();
  const mismatchStatus = elements['#status'].textContent;
  mode = 'file-denied';
  await elements['#open-archive'].click();
  const fileDeniedStatus = elements['#status'].textContent;
  console.log(JSON.stringify({{
    events,
    openedTabs,
    existingStatus,
    missingStatus,
    unsupportedStatus,
    mismatchStatus,
    fileDeniedStatus,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertEqual(len(state["openedTabs"]), 1)
        self.assertEqual(state["openedTabs"][0], archive_url)
        self.assertLess(state["events"].index("permission"), state["events"].index("lookup"))
        self.assertLess(state["events"].index("lookup"), state["events"].index("location"))
        self.assertLess(state["events"].index("location"), state["events"].index("file-access"))
        self.assertLess(state["events"].index("file-access"), state["events"].index("open"))
        self.assertIn("已在浏览器中打开", state["existingStatus"])
        self.assertIn("当前会话尚无归档", state["missingStatus"])
        self.assertIn("尚未配置", state["unsupportedStatus"])
        self.assertIn("身份信息", state["mismatchStatus"])
        self.assertIn("允许访问文件网址", state["fileDeniedStatus"])

    def test_popup_restores_error_details_for_the_active_tab(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")
        worker = (EXTENSION / "service-worker.js").read_text(encoding="utf-8")
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        self.assertIn("storage", manifest["permissions"])
        self.assertIn('updateRecordStatus(tab.id, "get", null, tab.url)', popup)
        self.assertIn('上次记录失败：${savedError.message}', popup)
        self.assertIn('chrome.storage.session.set', worker)
        self.assertIn('chrome.storage.session.get', worker)
        self.assertIn('await clearTabError(tab.id)', worker)

    def test_zero_records_is_a_persisted_failure(self):
        worker = (EXTENSION / "service-worker.js").read_text(encoding="utf-8")

        self.assertIn('if (!ordered.length)', worker)
        self.assertIn('没有识别到任何可归档消息', worker)
        self.assertNotIn('ordered.length ? "OK" : "ERR"', worker)
        self.assertIn('return await finishRunWithError(run, message)', worker)

    def test_worker_error_state_is_tab_scoped_and_zero_records_fail(self):
        worker = EXTENSION / "service-worker.js"
        script = f"""
const path = require('path');
const extension = {json.dumps(str(EXTENSION))};
global.self = globalThis;
global.setTimeout = (callback) => {{ callback(); return 0; }};
global.importScripts = (...files) => files.forEach((file) => require(path.join(extension, file)));
const values = new Map();
const badges = new Map();
const currentUrls = new Map([
  [11, 'https://chatgpt.com/c/abc123?view=one'],
  [21, 'https://chatgpt.com/c/test'],
]);
let messageListener;
let updatedListener;
global.chrome = {{
  storage: {{ session: {{
    get: async (key) => values.has(key) ? {{ [key]: values.get(key) }} : {{}},
    set: async (entries) => Object.entries(entries).forEach(([key, value]) => values.set(key, value)),
    remove: async (key) => values.delete(key),
  }} }},
  action: {{
    setBadgeBackgroundColor: async () => {{}},
    setBadgeText: async ({{ tabId, text }}) => badges.set(tabId, text),
  }},
  tabs: {{
    get: async (tabId) => ({{
      id: tabId,
      url: currentUrls.get(tabId) ?? 'https://chatgpt.com/c/test',
    }}),
    onRemoved: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: (listener) => {{ updatedListener = listener; }} }},
  }},
  runtime: {{ onMessage: {{ addListener: (listener) => {{ messageListener = listener; }} }} }},
  scripting: {{ executeScript: async () => [{{ result: {{
    records: [], artifacts: [], scrollHeight: 100, atTop: true, atBottom: true,
    url: 'https://chatgpt.com/c/test', title: 'Test',
  }} }}] }},
}};
require({json.dumps(str(worker))});
function send(message) {{
  return new Promise((resolve, reject) => {{
    if (!messageListener(message, {{}}, resolve)) reject(new Error('message not handled'));
  }});
}}
(async () => {{
  await send({{
    type: 'record-status', mode: 'error', tabId: 11, message: 'first failure',
    pageUrl: 'https://chatgpt.com/c/abc123?view=one',
  }});
  const tab11 = await send({{
    type: 'record-status', mode: 'get', tabId: 11,
    pageUrl: 'https://chatgpt.com/c/abc123#latest',
  }});
  const tab12 = await send({{ type: 'record-status', mode: 'get', tabId: 12 }});
  currentUrls.set(11, 'https://chatgpt.com/c/abc123?view=two');
  updatedListener(11, {{ url: 'https://chatgpt.com/c/abc123?view=two' }});
  await new Promise((resolve) => setImmediate(resolve));
  const sameConversation = await send({{
    type: 'record-status', mode: 'get', tabId: 11,
    pageUrl: 'https://chatgpt.com/c/abc123?view=two',
  }});
  currentUrls.set(11, 'https://chatgpt.com/c/def456');
  updatedListener(11, {{ url: 'https://chatgpt.com/c/def456' }});
  await new Promise((resolve) => setImmediate(resolve));
  const afterNavigation = await send({{
    type: 'record-status', mode: 'get', tabId: 11,
    pageUrl: 'https://chatgpt.com/c/def456',
  }});
  const zeroResult = await send({{ type: 'record-control', mode: 'record', tabId: 21 }});
  const zeroStatus = await send({{ type: 'record-status', mode: 'get', tabId: 21 }});
  console.log(JSON.stringify({{
    tab11, tab12, sameConversation, afterNavigation, zeroResult, zeroStatus,
    firstBadge: badges.get(11), zeroBadge: badges.get(21),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertEqual(state["tab11"]["error"]["message"], "first failure")
        self.assertIsNone(state["tab12"]["error"])
        self.assertEqual(state["sameConversation"]["error"]["message"], "first failure")
        self.assertIsNone(state["afterNavigation"]["error"])
        self.assertEqual(state["firstBadge"], "")
        self.assertFalse(state["zeroResult"]["ok"])
        self.assertIn("没有识别到任何可归档消息", state["zeroResult"]["message"])
        self.assertEqual(
            state["zeroStatus"]["error"]["message"],
            state["zeroResult"]["message"],
        )
        self.assertEqual(state["zeroBadge"], "ERR")

    def test_worker_fails_fast_when_scroll_scan_makes_no_progress(self):
        worker = EXTENSION / "service-worker.js"
        records = [
            {
                "id": f"message-{index}",
                "role": "user" if index % 2 == 0 else "assistant",
                "markdown": f"message {index}",
                "attachments": [],
            }
            for index in range(8)
        ]
        script = f"""
const path = require('path');
const extension = {json.dumps(str(EXTENSION))};
global.self = globalThis;
global.setTimeout = (callback) => {{ callback(); return 0; }};
global.importScripts = (...files) => files.forEach((file) => require(path.join(extension, file)));
const values = new Map();
const badgeEvents = [];
const moves = [];
let messageListener;
let archiveOpenCount = 0;

global.indexedDB = {{
  open: () => {{
    archiveOpenCount += 1;
    throw new Error('archive write attempted before stable bottom');
  }},
}};
global.chrome = {{
  storage: {{ session: {{
    get: async (key) => values.has(key) ? {{ [key]: values.get(key) }} : {{}},
    set: async (entries) => Object.entries(entries).forEach(([key, value]) => values.set(key, value)),
    remove: async (key) => values.delete(key),
  }} }},
  action: {{
    setBadgeBackgroundColor: async () => {{}},
    setBadgeText: async ({{ text }}) => badgeEvents.push(text),
  }},
  tabs: {{
    get: async (tabId) => ({{ id: tabId, url: 'https://chatgpt.com/c/stalled' }}),
    onRemoved: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: () => {{}} }},
  }},
  runtime: {{ onMessage: {{ addListener: (listener) => {{ messageListener = listener; }} }} }},
  scripting: {{ executeScript: async (request) => {{
    moves.push(request.args[0]);
    return [{{ result: {{
      records: {json.dumps(records, ensure_ascii=False)},
      artifacts: [],
      scrollTop: 0.5,
      scrollHeight: 4000,
      viewport: 800,
      atTop: true,
      atBottom: false,
      url: 'https://chatgpt.com/c/stalled',
      title: 'Stalled conversation',
    }} }}];
  }} }},
}};
require({json.dumps(str(worker))});

function send(message) {{
  return new Promise((resolve, reject) => {{
    if (!messageListener(message, {{}}, resolve)) reject(new Error('message not handled'));
  }});
}}

(async () => {{
  const recordResult = await send({{
    type: 'record-control', mode: 'record', tabId: 61,
  }});
  const statusResult = await send({{
    type: 'record-status', mode: 'get', tabId: 61,
    pageUrl: 'https://chatgpt.com/c/stalled',
  }});
  const stopResult = await send({{
    type: 'record-control', mode: 'stop', tabId: 61,
  }});
  console.log(JSON.stringify({{
    recordResult,
    statusResult,
    stopResult,
    nextCalls: moves.filter((move) => move === 'next').length,
    archiveOpenCount,
    badgeEvents,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertFalse(state["recordResult"]["ok"])
        self.assertIn("滚动连续多次未前进", state["recordResult"]["message"])
        self.assertEqual(
            state["statusResult"]["error"]["message"],
            state["recordResult"]["message"],
        )
        self.assertLess(state["nextCalls"], 50)
        self.assertEqual(state["archiveOpenCount"], 0)
        self.assertEqual(state["badgeEvents"][-1], "ERR")
        self.assertNotIn("OK", state["badgeEvents"])
        self.assertIn("当前没有正在执行的任务", state["stopResult"]["message"])

    def test_worker_never_writes_without_confirmed_top_and_bottom(self):
        worker = EXTENSION / "service-worker.js"
        script = f"""
const path = require('path');
const extension = {json.dumps(str(EXTENSION))};
global.self = globalThis;
global.setTimeout = (callback) => {{ callback(); return 0; }};
global.importScripts = (...files) => files.forEach((file) => require(path.join(extension, file)));
const values = new Map();
let messageListener;
let scenario = 'top-ignored';
let archiveOpenCount = 0;
let okBadgeCount = 0;
let limitNoneCalls = 0;
let limitNextCalls = 0;

const records = [{{
  id: 'message-1', role: 'user', markdown: 'partial', attachments: [],
}}];
global.indexedDB = {{
  open: () => {{
    archiveOpenCount += 1;
    throw new Error('partial archive write attempted');
  }},
}};
global.chrome = {{
  storage: {{ session: {{
    get: async (key) => values.has(key) ? {{ [key]: values.get(key) }} : {{}},
    set: async (entries) => Object.entries(entries).forEach(([key, value]) => values.set(key, value)),
    remove: async (key) => values.delete(key),
  }} }},
  action: {{
    setBadgeBackgroundColor: async () => {{}},
    setBadgeText: async ({{ text }}) => {{ if (text === 'OK') okBadgeCount += 1; }},
  }},
  tabs: {{
    get: async (tabId) => ({{ id: tabId, url: `https://chatgpt.com/c/${{tabId}}` }}),
    onRemoved: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: () => {{}} }},
  }},
  runtime: {{ onMessage: {{ addListener: (listener) => {{ messageListener = listener; }} }} }},
  scripting: {{ executeScript: async (request) => {{
    const move = request.args[0];
    if (scenario === 'top-ignored') {{
      return [{{ result: {{
        records, artifacts: [], scrollTop: 0, scrollHeight: 4000, viewport: 800,
        atTop: false, atBottom: true, url: 'https://chatgpt.com/c/71', title: 'Top ignored',
      }} }}];
    }}
    if (move === 'next') limitNextCalls += 1;
    if (move === 'none') limitNoneCalls += 1;
    const initialNone = move === 'none' && limitNoneCalls === 1;
    return [{{ result: {{
      records, artifacts: [],
      scrollTop: move === 'none' ? limitNoneCalls : 0,
      scrollHeight: 4000,
      viewport: 800,
      atTop: move === 'top' || initialNone,
      atBottom: false,
      url: 'https://chatgpt.com/c/72',
      title: 'Bottom never reached',
    }} }}];
  }} }},
}};
require({json.dumps(str(worker))});

function send(message) {{
  return new Promise((resolve, reject) => {{
    if (!messageListener(message, {{}}, resolve)) reject(new Error('message not handled'));
  }});
}}

(async () => {{
  const topResult = await send({{ type: 'record-control', mode: 'record', tabId: 71 }});
  scenario = 'limit';
  const limitResult = await send({{ type: 'record-control', mode: 'record', tabId: 72 }});
  console.log(JSON.stringify({{
    topResult,
    limitResult,
    archiveOpenCount,
    okBadgeCount,
    limitNextCalls,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertFalse(state["topResult"]["ok"])
        self.assertIn("未确认稳定的对话顶部", state["topResult"]["message"])
        self.assertFalse(state["limitResult"]["ok"])
        self.assertIn("扫描步数上限", state["limitResult"]["message"])
        self.assertEqual(state["limitNextCalls"], 2000)
        self.assertEqual(state["archiveOpenCount"], 0)
        self.assertEqual(state["okBadgeCount"], 0)

    def test_running_export_is_invalidated_by_navigation_and_user_stop(self):
        worker = EXTENSION / "service-worker.js"
        script = f"""
const path = require('path');
const extension = {json.dumps(str(EXTENSION))};
global.self = globalThis;
global.setTimeout = (callback) => {{ callback(); return 0; }};
global.importScripts = (...files) => files.forEach((file) => require(path.join(extension, file)));
const values = new Map();
const badgeEvents = [];
const currentUrls = new Map([
  [31, 'https://chatgpt.com/c/original'],
  [41, 'https://chatgpt.com/c/stop-test'],
]);
let messageListener;
let updatedListener;
let scenario = 'navigation';
let inspectionCount = 0;
let releaseInspection = null;
let archiveOpenCount = 0;

function stateFor(url, withRecord) {{
  return {{
    records: withRecord ? [{{
      id: 'message-1', role: 'user', markdown: 'partial record', attachments: [],
    }}] : [],
    artifacts: [],
    scrollHeight: 100,
    atBottom: false,
    url,
    title: 'Test',
  }};
}}

global.indexedDB = {{
  open: () => {{
    archiveOpenCount += 1;
    throw new Error('archive write attempted after cancellation');
  }},
}};
global.chrome = {{
  storage: {{ session: {{
    get: async (key) => values.has(key) ? {{ [key]: values.get(key) }} : {{}},
    set: async (entries) => Object.entries(entries).forEach(([key, value]) => values.set(key, value)),
    remove: async (key) => values.delete(key),
  }} }},
  action: {{
    setBadgeBackgroundColor: async () => {{}},
    setBadgeText: async ({{ tabId, text }}) => badgeEvents.push({{ tabId, text }}),
  }},
  tabs: {{
    get: async (tabId) => ({{ id: tabId, url: currentUrls.get(tabId) }}),
    onRemoved: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: (listener) => {{ updatedListener = listener; }} }},
  }},
  runtime: {{ onMessage: {{ addListener: (listener) => {{ messageListener = listener; }} }} }},
  scripting: {{ executeScript: async () => {{
    inspectionCount += 1;
    const tabId = scenario === 'navigation' ? 31 : 41;
    const url = scenario === 'navigation'
      ? 'https://chatgpt.com/c/original'
      : 'https://chatgpt.com/c/stop-test';
    const result = stateFor(url, scenario === 'stop');
    const shouldPause = (scenario === 'navigation' && inspectionCount === 1)
      || (scenario === 'stop' && inspectionCount === 6);
    if (!shouldPause) return [{{ result }}];
    return new Promise((resolve) => {{
      releaseInspection = () => resolve([{{ result }}]);
    }});
  }} }},
}};
require({json.dumps(str(worker))});

function send(message) {{
  return new Promise((resolve, reject) => {{
    if (!messageListener(message, {{}}, resolve)) reject(new Error('message not handled'));
  }});
}}

async function waitForPausedInspection() {{
  for (let attempt = 0; attempt < 100 && !releaseInspection; attempt += 1) {{
    await Promise.resolve();
  }}
  if (!releaseInspection) throw new Error('inspection did not pause');
}}

(async () => {{
  const navigationRun = send({{
    type: 'record-control', mode: 'record', tabId: 31,
    pageUrl: 'https://chatgpt.com/c/original',
  }});
  await waitForPausedInspection();
  currentUrls.set(31, 'https://chatgpt.com/c/next');
  updatedListener(
    31,
    {{ url: 'https://chatgpt.com/c/next' }},
    {{ id: 31, url: 'https://chatgpt.com/c/next' }},
  );
  await new Promise((resolve) => setImmediate(resolve));
  const navigationResetIndex = badgeEvents.length;
  releaseInspection();
  releaseInspection = null;
  const navigationResult = await navigationRun;
  await Promise.resolve();
  const navigationStatus = await send({{
    type: 'record-status', mode: 'get', tabId: 31,
    pageUrl: 'https://chatgpt.com/c/next',
  }});

  scenario = 'stop';
  inspectionCount = 0;
  const stopRun = send({{
    type: 'record-control', mode: 'record', tabId: 41,
    pageUrl: 'https://chatgpt.com/c/stop-test',
  }});
  await waitForPausedInspection();
  const stopResponse = await send({{ type: 'record-control', mode: 'stop', tabId: 41 }});
  releaseInspection();
  releaseInspection = null;
  const stopResult = await stopRun;
  const stopStatus = await send({{
    type: 'record-status', mode: 'get', tabId: 41,
    pageUrl: 'https://chatgpt.com/c/stop-test',
  }});

  console.log(JSON.stringify({{
    navigationResult,
    navigationStatus,
    navigationBadges: badgeEvents.filter((entry) => entry.tabId === 31).map((entry) => entry.text),
    navigationBadgesAfterReset: badgeEvents
      .slice(navigationResetIndex)
      .filter((entry) => entry.tabId === 31)
      .map((entry) => entry.text),
    stopResponse,
    stopResult,
    stopStatus,
    stopBadges: badgeEvents.filter((entry) => entry.tabId === 41).map((entry) => entry.text),
    archiveOpenCount,
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertTrue(state["navigationResult"].get("cancelled"))
        self.assertIsNone(state["navigationStatus"]["error"])
        self.assertEqual(state["navigationBadges"][-1], "")
        self.assertNotIn("ERR", state["navigationBadgesAfterReset"])
        self.assertNotIn("OK", state["navigationBadgesAfterReset"])
        self.assertTrue(state["stopResponse"]["ok"])
        self.assertTrue(state["stopResult"].get("cancelled"))
        self.assertIsNone(state["stopStatus"]["error"])
        self.assertNotIn("ERR", state["stopBadges"])
        self.assertNotIn("OK", state["stopBadges"])
        self.assertEqual(state["archiveOpenCount"], 0)

    def test_busy_run_stays_reserved_while_error_persistence_is_pending(self):
        worker = EXTENSION / "service-worker.js"
        script = f"""
const path = require('path');
const extension = {json.dumps(str(EXTENSION))};
global.self = globalThis;
global.setTimeout = (callback) => {{ callback(); return 0; }};
global.importScripts = (...files) => files.forEach((file) => require(path.join(extension, file)));
const values = new Map();
const badgeEvents = [];
const inspectionTabs = [];
const currentUrls = new Map([
  [51, 'https://chatgpt.com/c/first'],
  [52, 'https://chatgpt.com/c/second'],
]);
let messageListener;
let updatedListener;
let pendingSet = null;

global.chrome = {{
  storage: {{ session: {{
    get: async (key) => values.has(key) ? {{ [key]: values.get(key) }} : {{}},
    set: async (entries) => new Promise((resolve) => {{
      pendingSet = () => {{
        for (const [key, value] of Object.entries(entries)) values.set(key, value);
        resolve();
      }};
    }}),
    remove: async (key) => values.delete(key),
  }} }},
  action: {{
    setBadgeBackgroundColor: async () => {{}},
    setBadgeText: async ({{ tabId, text }}) => badgeEvents.push({{ tabId, text }}),
  }},
  tabs: {{
    get: async (tabId) => ({{ id: tabId, url: currentUrls.get(tabId) }}),
    onRemoved: {{ addListener: () => {{}} }},
    onUpdated: {{ addListener: (listener) => {{ updatedListener = listener; }} }},
  }},
  runtime: {{ onMessage: {{ addListener: (listener) => {{ messageListener = listener; }} }} }},
  scripting: {{ executeScript: async (request) => {{
    inspectionTabs.push(request.target.tabId);
    return [{{ result: {{
      records: [], artifacts: [], scrollHeight: 100, atBottom: true,
      url: currentUrls.get(request.target.tabId), title: 'Empty conversation',
    }} }}];
  }} }},
}};
require({json.dumps(str(worker))});

function send(message) {{
  return new Promise((resolve, reject) => {{
    if (!messageListener(message, {{}}, resolve)) reject(new Error('message not handled'));
  }});
}}

async function waitForPendingSet() {{
  for (let attempt = 0; attempt < 200 && !pendingSet; attempt += 1) {{
    await Promise.resolve();
  }}
  if (!pendingSet) throw new Error('error persistence did not pause');
}}

(async () => {{
  const firstRun = send({{
    type: 'record-control', mode: 'record', tabId: 51,
    pageUrl: 'https://chatgpt.com/c/first',
  }});
  await waitForPendingSet();
  const inspectionsBeforeBusy = inspectionTabs.length;
  const busyResult = await send({{
    type: 'record-control', mode: 'record', tabId: 52,
    pageUrl: 'https://chatgpt.com/c/second',
  }});
  const inspectionsAfterBusy = inspectionTabs.length;
  const busyStatus = await send({{
    type: 'record-status', mode: 'get', tabId: 52,
    pageUrl: 'https://chatgpt.com/c/second',
  }});

  currentUrls.set(51, 'https://chatgpt.com/c/new-page');
  updatedListener(
    51,
    {{ url: 'https://chatgpt.com/c/new-page' }},
    {{ id: 51, url: 'https://chatgpt.com/c/new-page' }},
  );
  await new Promise((resolve) => setImmediate(resolve));
  const navigationResetIndex = badgeEvents.length;
  pendingSet();
  pendingSet = null;
  const firstResult = await firstRun;
  const firstStatus = await send({{
    type: 'record-status', mode: 'get', tabId: 51,
    pageUrl: 'https://chatgpt.com/c/new-page',
  }});

  console.log(JSON.stringify({{
    busyResult,
    busyStatus,
    inspectionsBeforeBusy,
    inspectionsAfterBusy,
    inspectedSecondTab: inspectionTabs.includes(52),
    firstResult,
    firstStatus,
    badgesAfterNavigation: badgeEvents
      .slice(navigationResetIndex)
      .filter((entry) => entry.tabId === 51)
      .map((entry) => entry.text),
    finalFirstBadge: badgeEvents
      .filter((entry) => entry.tabId === 51)
      .map((entry) => entry.text)
      .at(-1),
  }}));
}})().catch((error) => {{ console.error(error); process.exitCode = 1; }});
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        state = json.loads(result.stdout)

        self.assertFalse(state["busyResult"]["ok"])
        self.assertTrue(state["busyResult"]["busy"])
        self.assertIsNone(state["busyStatus"]["error"])
        self.assertEqual(
            state["inspectionsBeforeBusy"],
            state["inspectionsAfterBusy"],
        )
        self.assertFalse(state["inspectedSecondTab"])
        self.assertTrue(
            state["firstResult"].get("cancelled")
            or state["firstResult"].get("detached")
        )
        self.assertIsNone(state["firstStatus"]["error"])
        self.assertNotIn("ERR", state["badgesAfterNavigation"])
        self.assertNotIn("OK", state["badgesAfterNavigation"])
        self.assertEqual(state["finalFirstBadge"], "")

    def test_popup_initialization_and_record_failures_have_safe_status_contract(self):
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        controls_start_disabled_in_html = all(
            f'id="{control}" disabled' in html
            or f'id="{control}" class=' in html and "disabled" in html.split(f'id="{control}"', 1)[1].split(">", 1)[0]
            for control in ("record", "stop", "open-archive", "configure")
        )
        controls_disabled_synchronously_in_script = (
            "setActionButtonsDisabled(true)" in popup
            and popup.index("setActionButtonsDisabled(true)") < popup.index("initializeStatus();")
        )
        self.assertTrue(
            controls_start_disabled_in_html or controls_disabled_synchronously_in_script,
            "popup actions must be disabled before asynchronous initialization begins",
        )
        self.assertIn('operationState = "idle";', popup)
        self.assertIn("syncOperationControls();", popup)
        self.assertIn('mode === "record" && !response?.ok', popup)
        failure_branch = popup.index('mode === "record" && !response?.ok')
        self.assertIn(
            "persistRecordError(tab, message)",
            popup[failure_branch:],
        )
        self.assertIn('updateRecordStatus(tab.id, "error", message, tab.url)', popup)

    def test_popup_serializes_record_and_configuration_without_persisting_transient_results(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertIn('if (operationState !== "idle" || stopPending) return;', popup)
        self.assertIn('setOperationState("preparing")', popup)
        self.assertIn('if (operationState !== "idle") return;', popup)
        self.assertIn('setOperationState("configuring")', popup)
        self.assertIn('recordButton.disabled = stopPending || operationState !== "idle"', popup)
        self.assertIn('configureButton.disabled = stopPending || operationState !== "idle"', popup)

        persistence_guard = (
            'if (mode === "record" && !response?.ok\n'
            '        && !response?.cancelled && !response?.detached && !response?.busy)'
        )
        self.assertIn(persistence_guard, popup)
        guard_start = popup.index(persistence_guard)
        guard_end = popup.index("}", guard_start)
        self.assertIn("persistRecordError(tab, message)", popup[guard_start:guard_end])
        self.assertIn(
            'response?.cancelled || response?.detached || response?.busy',
            popup,
        )

    def test_manifest_sets_storage_api_browser_floor(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["minimum_chrome_version"], "102")


if __name__ == "__main__":
    unittest.main()
