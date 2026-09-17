import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
EXTENSION = ROOT / "skills" / "yalo-note-it" / "assets" / "browser-extension"


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

    def test_manifest_version_matches_documented_release(self):
        manifest = json.loads((EXTENSION / "manifest.json").read_text(encoding="utf-8"))
        readme = (EXTENSION / "README.md").read_text(encoding="utf-8")

        self.assertEqual(manifest["version"], "0.10.1")
        self.assertIn("当前版本：**0.10.1**", readme)

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

    def test_configuration_creates_named_child_and_prompts_before_migration(self):
        popup = (EXTENSION / "popup.js").read_text(encoding="utf-8")

        self.assertIn('const ARCHIVE_FOLDER_NAME = "yalo note"', popup)
        self.assertIn('getDirectoryHandle(ARCHIVE_FOLDER_NAME, { create: true })', popup)
        self.assertIn('是否把原有内容全部迁移到新路径', popup)
        self.assertIn('await copyDirectory(previous, handle)', popup)
        self.assertIn('await removeDirectoryContents(previous)', popup)
        self.assertNotIn('D:\\\\MyData\\\\yalo-note', popup)


if __name__ == "__main__":
    unittest.main()
