# Page adapters

浏览器扩展的采集循环与站点页面结构分离。`service-worker.js` 负责通用流程；每个支持的站点在本目录注册一个页面适配器。

当前已注册并实测 `chatgpt.js` 与 `deepseek.js`。其他站点尚未注册，不会被当成已支持站点。

## 适配器契约

每个适配器向 `self.YaloNoteProviders` 注册以下字段：

- `id`：稳定的小写存储标识，用作 `sessions/<id>/` 目录名；
- `label`：Raw Record 标题中的产品名称；
- `matchesUrl(url)`：判断适配器是否支持当前页面；
- `conversationId(url)`：从会话 URL 取得稳定 ID，无法取得时返回 `null`；
- `inspectPage(targetPosition)`：在目标网页中执行，返回标准化页面状态。
- `captureArtifact(key)`（可选）：取得 `inspectPage` 发现的用户附件，返回文件状态和内容。

`inspectPage` 不得依赖 Service Worker 闭包，因为 Chrome 会将函数序列化后注入目标页面。返回结构为：

```js
{
  records: [{ id, role, markdown, attachments }],
  artifacts: [{ key, messageId, displayName }],
  scrollTop,
  scrollHeight,
  viewport,
  atBottom,
  title,
  url,
  error,
}
```

附件捕获结果为：

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

`base64` 只在 `saved` 时返回。站点适配器负责通过该站点当前页面提供的入口取得文件；通用采集器负责安全命名、写入 `assets/`、计算 SHA-256 和生成 `manifest.json`。

站点适配器负责消息节点、角色、正文、附件、滚动区域和站点专属排除规则。通用采集器负责顶部稳定检测、向下扫描、去重、排序、截止点、进度、停止和本地写入。

DeepSeek 适配器只读取 `.ds-assistant-message-main-content` 中的最终回答，明确排除同一助手消息节点里的“已思考”内容。实测附件卡没有稳定下载链接，当前仅记录文件名，不注册 `captureArtifact`。
