# Page adapters

浏览器扩展的采集循环与站点 DOM 分离。`service-worker.js` 负责通用滚动、稳定检测、停止、状态和归档写入；每个站点适配器向 `self.YaloNoteProviders` 注册页面能力。

当前已注册并实测 `chatgpt.js` 与 `deepseek.js`。其他站点不会被当成已支持站点。

## 注册契约

每个适配器注册：

- `id`：稳定的小写存储标识，用作 `sessions/<id>/` 目录名；
- `label`：Raw Record 标题中的产品名称；
- `matchesUrl(url)`：仅对适配器已验证的会话 URL 返回 `true`；
- `conversationId(url)`：返回 URL 中的稳定会话 ID，无法取得时返回 `null`；
- `inspectPage(targetPosition)`：在目标页面中执行，必须返回标准化页面状态；
- `captureArtifact(key)`（可选）：通过 `inspectPage` 返回的 artifact key 获取当前页面仍可用的文件。

`inspectPage` 会被 Chrome 序列化后注入页面，不得依赖 Service Worker 闭包。`targetPosition` 为 `"top"` 或 `"next"`；适配器必须根据真实滚动语义处理正序、`column-reverse` 或站点专属虚拟列表。

成功返回结构：

```js
{
  records: [{ id, role, markdown, attachments }],
  artifacts: [{ key, messageId, displayName, kind }],
  scrollTop,
  scrollHeight,
  viewport,
  atTop,
  atBottom,
  generating,
  title,
  url,
}
```

- `atTop` 和 `atBottom` 表示逻辑会话顶部/底部，不能假设 DOM 滚动值总是正数；
- `generating` 必须是布尔值，表示页面是否仍在生成；不得在明知页面仍生成时返回 `false`；
- 不能验证页面时返回 `{ error: "具体原因" }`，不返回猜测的部分记录。

## 消息身份与可见内容

`records[].id` 必须来自站点提供的稳定消息/turn 身份，在虚拟列表重新渲染和重复扫描时保持不变。禁止用可见文字、当前索引或随机值合成身份。缺少、歧义或重复 ID 必须显式报错，避免静默合并不同消息。

适配器只提取消息边界内真正可见的语义正文和媒体：

- 排除导航、操作按钮、辅助标签、隐藏元素和进度/推理界面；
- 保留标题、段落、列表、链接、引用、表格、代码和可见附件名称；
- 纯图像或纯文件的助手消息可以没有文本正文，但必须存在可见媒体；
- 既无可验证正文也无可见媒体的助手消息必须失败，不以 UI 标签填充档案。

ChatGPT 适配器优先使用新版语义正文容器，并为已验证的旧版 `.markdown` 结构保留窄兼容路径。DeepSeek 只读取 `.ds-assistant-message-main-content` 中的最终回答，明确排除同一助手消息节点里的“已思考”内容。两个当前适配器都返回 `generating`，通用采集器在生成结束前不提交底部归档。

## 附件契约

`artifacts[].key` 在单次采集中必须唯一，并能在重新查找 DOM 时定位同一个可见文件。同名文件在同一呈现机制中多次出现时仍是不同 artifact；只有适配器识别到的跨机制重复表示（如文件卡与 sandbox 链接）才可合并。

`captureArtifact(key)` 返回：

```js
{
  key,
  messageId,
  displayName,
  status: "saved" | "unavailable" | "too_large",
  base64,
  mimeType,
  size,
  sourceUrl,
  error,
}
```

`base64` 仅在 `saved` 时返回。适配器负责通过当前页面提供的入口取得文件，不得拦截与目标 artifact 无关的用户下载。通用采集器负责安全命名、大小上限、SHA-256 和 manifest。单个 artifact 失败不得被伪造为已保存。

ChatGPT 的 `kind` 为 `uploaded` 或 `generated`；生成文件可来自可见文件卡、下载按钮或已验证的 sandbox 链接。DeepSeek 当前只记录附件名称，不注册 `captureArtifact`。
