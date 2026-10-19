import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "browser-extension" / "yalo-note-it"


class BrowserExtensionAdapterTests(unittest.TestCase):
    def run_node_regression(self, script_name):
        subprocess.run(["node", str(ROOT / "tests" / script_name)], check=True)

    def test_persistent_task_window_and_error_reporting(self):
        self.run_node_regression("extension_task.cjs")

    def test_generated_chatgpt_artifacts(self):
        self.run_node_regression("chatgpt_artifacts.cjs")

    def test_chatgpt_dom_discovery(self):
        self.run_node_regression("chatgpt_dom.cjs")

    def test_archive_access_and_task_window_ui(self):
        self.run_node_regression("archive_ui.cjs")

    def test_service_worker_context_and_lifecycle(self):
        self.run_node_regression("service_worker_state.cjs")

    def test_web_archive_writer_transaction_contract(self):
        self.run_node_regression("web_archive_writer.cjs")

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
  legacy: provider.matchesUrl('https://chat.openai.com/c/123'),
  qwen: provider.matchesUrl('https://www.qianwen.com/chat/123'),
  conversationId: provider.conversationId('https://chatgpt.com/c/6a9a7f35-e334-83e8-a4cf-d938573b1910'),
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
        self.assertTrue(contract["legacy"])
        self.assertFalse(contract["qwen"])
        self.assertEqual(
            contract["conversationId"],
            "6a9a7f35-e334-83e8-a4cf-d938573b1910",
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
const stopControl = {{ textContent: '停止生成', getAttribute: (name) => name === 'aria-label' ? '停止生成' : null,
  hasAttribute: () => false }};
global.getComputedStyle = () => ({{}});
global.document = {{ title: 'Test', querySelector: (selector) => selector === '.ds-virtual-list' ? scroller : null,
  querySelectorAll: (selector) => selector === '[data-virtual-list-item-key]' ? turns
    : selector === "button, [role='button']" ? [stopControl] : [] }};
require({json.dumps(str(adapter))});
const initial = self.YaloNoteProviders[0].inspectPage('none');
turns[1].getAttribute = () => '1';
const duplicate = self.YaloNoteProviders[0].inspectPage('none');
console.log(JSON.stringify({{ initial, duplicate }}));
"""
        result = subprocess.run(
            ["node", "-e", script],
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        records = payload["initial"]["records"]
        self.assertTrue(payload["initial"]["generating"])
        self.assertIn("重复消息 ID", payload["duplicate"]["error"])
        self.assertEqual(records[0]["role"], "user")
        self.assertEqual(records[0]["markdown"], "用户问题")
        self.assertEqual(records[0]["attachments"], ["资料.pdf"])
        self.assertEqual(records[1]["role"], "assistant")
        self.assertEqual(records[1]["markdown"], "最终回答")
        self.assertNotIn("隐藏推理", records[1]["markdown"])

    def test_manifest_version_matches_documented_release(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        readme = (EXTENSION / "README.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0.14.0")
        self.assertEqual(manifest["optional_host_permissions"], ["file:///*"])
        self.assertIn("当前版本：**0.14.0**", readme)

    def test_record_button_can_restore_saved_directory_permission(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertIn('handle.queryPermission({ mode: "readwrite" })', popup)
        self.assertIn('handle.requestPermission({ mode: "readwrite" })', popup)
        self.assertIn('await verifyWritable(handle)', popup)
        self.assertIn('正在确认记录目录', popup)
        self.assertIn('await self.YaloArchiveAccess.readArchiveLocation(handle)', popup)
        self.assertLess(
            popup.index('await verifyWritable(handle)', popup.index('async function control')),
            popup.index('chrome.runtime.sendMessage', popup.index('async function control')),
        )

    def test_configuration_reuses_codex_archive_root_without_migration(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")
        configured_start = popup.index("async function configuredArchiveRoot")
        configured_end = popup.index("async function control", configured_start)
        configured = popup[configured_start:configured_end]
        configure_start = popup.index("async function configureRoot")
        configure_end = popup.index("async function ensureFileUrlAccess", configure_start)
        configure = popup[configure_start:configure_end]

        self.assertIn('const ARCHIVE_FOLDER_NAMES = ["YaloNote", "yalo note", "yalo-note"]', popup)
        self.assertIn('RECOGNIZED_ARCHIVE_FOLDER_NAMES.has(base.name.toLocaleLowerCase())', configured)
        self.assertIn('return await base.getDirectoryHandle(name)', configured)
        self.assertNotIn('{ create: true }', configured)
        self.assertIn('所选目录中没有 Codex 已配置的 YaloNote 文件夹', configured)
        self.assertIn('const handle = await configuredArchiveRoot(base)', configure)
        self.assertIn('await self.YaloArchiveAccess.readArchiveLocation(handle)', configure)
        self.assertIn('await self.YaloArchiveSettings.saveArchiveRoot(handle)', configure)
        self.assertLess(
            configure.index('await verifyWritable(handle)'),
            configure.index('await self.YaloArchiveSettings.saveArchiveRoot(handle)'),
        )
        self.assertNotIn('copyDirectory', popup)
        self.assertNotIn('removeDirectoryContents', popup)
        self.assertNotIn('window.confirm', popup)
        self.assertNotIn('D:\\\\MyData\\\\yalo-note', popup)

    def test_archive_action_is_compact_and_opens_verified_local_archive(self):
        html = (EXTENSION / "popup.html").read_text(encoding="utf-8")
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertGreater(html.index('id="status"'), html.index('id="stop"'))
        self.assertGreater(html.index('id="open-archive"'), html.index('id="status"'))
        self.assertGreater(html.index('id="configure"'), html.index('id="status"'))
        self.assertIn('class="directory-actions"', html)
        self.assertIn('id="open-archive" disabled', html)
        self.assertNotIn('id="open-directory"', html)
        self.assertNotIn('openArchiveRoot', popup)
        self.assertNotIn('yalo-note-open', popup)
        self.assertIn('路径更改与归档迁移请在 Codex 中完成', html)
        self.assertIn('async function openCurrentArchive()', popup)
        self.assertIn('await self.YaloArchiveAccess.readConversationArchive(', popup)
        self.assertIn('await self.YaloArchiveAccess.conversationDirectoryFileUrl(', popup)
        self.assertIn('await ensureFileUrlAccess()', popup)
        self.assertIn('await chrome.tabs.create({ url: archiveUrl', popup)


if __name__ == "__main__":
    unittest.main()
