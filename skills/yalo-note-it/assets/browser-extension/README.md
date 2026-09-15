# Yalo note it — Browser Extension

这是 Yalo note it 的 ChatGPT Web 入口。只有点击插件中的“Yalo note it”按钮才会运行；平时不监听网页文字，也不执行后台轮询。

## 安装

1. 在 Chrome 打开 `chrome://extensions`；Edge 打开 `edge://extensions`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”。
4. 选择本目录：`skills/yalo-note-it/assets/browser-extension`。
5. 建议将扩展固定到浏览器工具栏。

## 使用

### 首次设置

点击插件中的“设置本地记录目录”，选择 `D:\MyData\yalo-note` 文件夹本身并授予写权限。不要选择 `D:\`、`D:\MyData` 或文件选择器侧栏中的系统位置。插件会创建、写入并删除一个临时测试文件；只有测试通过后才保存目录设置。浏览器安全模型不允许扩展在未经选择授权的情况下直接写任意绝对路径；此步骤通常只需一次。

### 导出 Raw Record

1. 打开目标 ChatGPT 对话。
2. 点击扩展图标，再点击“Yalo note it”。ChatGPT 页面里的消息文字不会触发插件。
3. 扩展会从顶部向下读取虚拟化加载的消息，按消息 ID 去重，并保留角色、标题、段落、列表、链接、表格、代码块和附件名称。
4. 角标显示 `OK` 后，文件位于 `D:\MyData\yalo-note\sessions\chatgpt\<conversation-id>\conversation.raw-record.md`。
5. Raw Record 是原始记录，不做总结、润色或观点合并。

点击按钮后保持插件窗口打开。插件从稳定顶部开始加载和读取消息，并在窗口中显示最终成功信息或具体错误。写入完成或失败后，任务状态会在 `finally` 阶段释放；失败时可按错误提示处理后再次点击。

## 限制

- ChatGPT 页面结构变化后，滚动区域或“展开”按钮识别规则可能需要更新。
- 单次任务最多扫描 2000 个滚动步，以防页面异常时无限运行。
