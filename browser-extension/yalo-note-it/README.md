# Yalo note it — Browser Extension

这是 Yalo note it 的 AI Web 入口，当前支持 ChatGPT Web 与 DeepSeek Web。只有点击扩展图标并在独立任务窗口中启动记录时才会读取页面。

当前版本：**0.14.0**。

## 安装

1. 在 Chrome 打开 `chrome://extensions`；Edge 打开 `edge://extensions`。
2. 开启“开发者模式”。
3. 点击“加载已解压的扩展程序”，选择 `browser-extension/yalo-note-it`。
4. 建议把扩展固定在工具栏。

代码更新后，在扩展管理页点击“重新加载”，并确认版本号为 **0.14.0**。

## 目录前置

浏览器扩展现在只使用 **Codex Skill 已初始化**的归档根目录。先在 Codex 中配置 Yalo note it，Skill 会创建：

```text
<用户路径>/YaloNote/
  .yalo-note-location.json
  sessions/                    # 首次归档时创建
```

`.yalo-note-location.json` 记录该归档根目录的绝对路径，用于生成当前 Web 归档的 `file:///` 地址。

点击“设置本地目录”时，可选择实际的 `YaloNote` 目录，或包含 `YaloNote`、`yalo note` 或 `yalo-note` 的父目录。扩展会：

- 请求该目录的读写权限；
- 通过临时文件验证真实写入；
- 校验 `.yalo-note-location.json` 与所选目录一致；
- 仅在全部通过后，把目录句柄保存到扩展的 IndexedDB。

扩展不创建 `YaloNote`、不重写绝对路径、不迁移旧归档。更换路径和迁移数据由 Codex Skill 完成，避免浏览器只持有不透明目录句柄时猜测本地路径。

## 记录当前会话

1. 打开目标 ChatGPT 或 DeepSeek 会话。
2. 点击扩展图标。扩展打开一个独立任务窗口，并把它永久绑定到点击时的原会话标签页；切换标签页或从另一段会话再次点击扩展都不会改变这个目标。要处理另一段会话，先关闭当前任务窗口，再从新会话页点击扩展图标。
3. 点击“Yalo note it”。扩展先重新确认目录权限和路径配置，再从顶部向下读取虚拟化消息列表。
4. 任务窗口会显示扫描、写入、成功或具体错误。请保留原会话页和任务窗口，直到任务结束。

只有扫描阶段可以安全停止；开始写入后停止按钮会禁用。任务状态按目标标签页和会话持久化，重新打开任务窗口会显示上次成功或失败信息；后台中断的未完成任务会被标记为中断，不冒充成功。

### Web 归档格式（schemaVersion 2）

```text
<archive-root>/sessions/<chatgpt|deepseek>/<conversation-id>/
  manifest.json
  conversation.raw-record.md
  records/<sha256>.md
  assets/
```

`records/<sha256>.md` 是由 `manifest.json` 指向并校验的正文版本；manifest 在不可变正文和附件之后写入，作为这次归档的提交点。`conversation.raw-record.md` 是便于人直接打开的副本，其更新失败时会给出警告，不否定已校验的 manifest 归档。重名附件不会静默覆盖。

ChatGPT 会尝试保存当前页面仍可下载的用户上传文件和助手生成文件。入口过期、页面不再提供下载或文件超过 25 MB 时，对话仍会保存，该文件在 manifest 中记为 `unavailable` 或 `too_large`。DeepSeek 当前只记录附件名称，不下载二进制内容。

## 打开当前归档

“打开当前归档”根据原会话 URL 的 provider 和稳定 conversation ID 定位目录。打开前会校验 manifest、正文文件和来源 URL；当前会话未归档或归档不完整时明确报错。

首次使用前，在 Chrome/Edge 的扩展详情页开启“允许访问文件网址”。该按钮会在原会话所在浏览器窗口新建 `file:///.../sessions/<provider>/<conversation-id>/` 标签页；它不启动 Windows 文件资源管理器。

## 常见错误

- **记录目录权限已失效**：目录句柄还在，但浏览器未授予当前读写权限。再次点击“Yalo note it”并完成授权；只有句柄失效时才需要重新设置。
- **缺少 Codex 路径配置**：所选目录不是 Skill 初始化的归档根目录，或 `.yalo-note-location.json` 无效。先在 Codex 中配置或修复路径。
- **浏览器拒绝系统文件夹**：不要选择磁盘根目录、用户主目录或其他受限位置；应选择已配置的普通 `YaloNote` 目录。
- **原会话标签页已关闭**：独立任务窗口不会改抓取其他活动标签页。关闭该任务窗口，重新打开目标会话，再从会话页点击扩展图标。
- **未允许访问文件网址**：在扩展详情页开启该选项后，再次打开当前归档。

## 页面适配器架构

- `service-worker.js`：通用采集、停止、状态和安全写入机制；
- `providers/*.js`：站点 URL、滚动容器、消息身份、可见正文及附件提取；
- `archive-settings.js`：目录句柄持久化；
- `archive-access.js`：路径定位、归档身份与文件哈希校验；
- `task-status-store.js`：独立任务窗口的状态持久化。

适配器契约见 [`providers/README.md`](providers/README.md)。

## 0.14.0 要点

- 目录设置改为验证 Codex 已初始化的归档根，不再由浏览器创建或迁移目录；
- 增加“打开当前归档”，通过经验证的 `file:///` 地址打开与当前 URL 匹配的归档；
- 独立任务窗口在关闭前永不改绑其原标签页，任务与错误状态可持久恢复；
- ChatGPT 适配器按稳定消息 ID 采集可见语义正文和媒体，未知、重复或正在生成的状态显式失败或等待；
- Web 归档采用 manifest 提交点、内容寻址正文和不覆盖的附件写入。

## 限制

- ChatGPT 或 DeepSeek 页面结构改变后，需要更新对应适配器；未知结构不会被当作完整归档。
- 单次任务最多扫描 2000 个滚动步；页面无进展、长时间生成、未到稳定底部或没有识别到记录都会失败。
- ChatGPT 文件必须在记录时仍能通过页面下载；不恢复已过期文件、Canvas 内容或其他会话中的文件。
