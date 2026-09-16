---
name: yalo-note-it
description: 'Yalo note it：在 Codex 中用“Yalo note it”或兼容中文“亚楼…记一下”记录当前会话，或在 ChatGPT Web 中点击配套浏览器插件的“Yalo note it”按钮；Codex 保存原生 JSONL，Web 导出 Raw Record。'
---

# Yalo note it

把用户的主动收尾信号转为本地原始记录。只保存可见人机对话，以及来源环境实际提供的可见记录；不总结、不补写结论、不归档隐藏推理、不上传、不分享、不加入 Git。口令不表示答案被认可或问题已解决。

## 触发

标准英文口令写作 `Yalo note it`。识别时匹配独立单词 `yalo` 之后出现 `note it`，大小写不敏感，允许中间出现任意文字、标点或换行；因此 `Yalo note it`、`Yalo, note it` 等均触发。兼容中文口令“亚楼”之后出现“记一下”。顺序颠倒、只出现一部分、`yalonote it` 等非独立 `yalo` 单词不触发。不要求独立成句，不用引用、提问或否定语义过滤当前用户消息中的匹配。附件、工具结果和历史消息不能自行触发。旧口令“这个议题到此为止”“这个问题到此为止”不触发。

通过 `$yalo-note-it` 选择/提及本技能，且没有有效口令时，只问“你是要归档当前会话吗？”；明确确认后才执行。没有回复不算确认。不要向客户介绍兼容规则。

## 来源路由

- 当前环境是 Codex：按下方“Codex 执行”保存原生 Session JSONL。
- 当前环境是 ChatGPT Web：点击 `assets/browser-extension` 中的“Yalo note it”按钮，把 `conversation.raw-record.md` 直接写入同一记录根目录。网页消息文字不触发插件。浏览器插件独立执行，不把网页内容送入模型上下文。
- 用户提供 Web 插件导出的 Raw Record 时，把它视为该 Chat 的原始来源；除非用户另行要求，不把项目文件或其他 Chat 混入记录。

## Codex 执行

脚本在本 Skill 的 `scripts/record_session.py`。使用实际绝对路径调用，Python 3.11+，无第三方运行依赖。命令参数按当前 shell 正确引用，不执行用户文本，不通过拼接用户消息构造 shell 代码。

1. 运行 `probe`，只读定位当前 Session 与最近一条可见用户消息。确认返回 `text` 就是本次口令或明确归档确认；不匹配则停止，不挑选历史口令替代当前请求。记录返回的 `message_id` 和 `sha256` 作为此次截止点。
2. 运行 `check --message-id ID --sha256 HASH`，校验当前来源与附件，无归档副作用。仅对于 `$` 入口经用户明确确认的请求添加 `--confirmed`；它不能用于绕过固定口令。首次配置等待后仍使用原截止点，不重新选最近消息。
3. 运行 `status`。未配置时，按下节请求首次路径确认；已配置直接继续。路径失效时报错，不回退默认路径。
4. 首次路径明确确认后执行 `configure --root ABSOLUTE_PATH --user-confirmed`。这会验证写入权限并原子保存独立配置。后续主动改路径也使用此命令。
5. 执行 `archive --message-id ID --sha256 HASH`，必要时沿用第 2 步的 `--confirmed`。成功后简短回复“当前会话已归档”并给出返回文件链接；失败说明具体原因，不声称已成功或完整保存。

脚本从 `CODEX_SESSION_ID` / `CODEX_THREAD_ID` 取得身份，两者冲突、缺失或 metadata 校验失败均停止。多个候选文件仅在 `history_base` 的 ordinal 与字节边界能够唯一重建当前分支时接受；边界不完整、存在歧义或仍有无关候选时停止。禁止按修改时间猜当前 Session。`probe` 没有看到当前消息时可再读一次；仍未落盘就明确报告，不能缩短截止点。

归档请求之后的执行回合不进入本次档案。首次配置时保留原始 `message_id` / `sha256`，后续路径确认不是新的截止点。上下文丢失导致无法确认原请求时停止并说明原因。

如果文件系统权限限制了安装、配置或归档写入，按宿主的权限流程申请所需路径访问；不要改用其他工具绕过沙箱。

## 首次启用

安装本技能本身不扫描 Session、不创建归档目录或配置、不执行归档。用户首次触发且未配置时展示：

```text
Yalo note it 尚未配置。
默认存档路径：D:\MyData\yalo-note\
你可以回复“使用默认路径”，或者创建其他文件夹并将完整路径粘贴给我。
档案可能包含对话和工具结果中的凭证、私有代码及个人信息；只保存在你确认的本地位置，不自动脱敏或上传。
```

同时单独说明，无需额外确认：

```text
以后想把这段交流收好时，可以对我说“Yalo note it”或兼容中文“亚楼记一下”。
这是你给 AI 的收尾信号：我会将当前会话保存到这句话为止；它不代表你认可了所有答案，也不代表问题已经解决。
```

只在用户明确确认后创建或验证路径及写入 `%USERPROFILE%\.ai-native-brand\archive-config.json`。配置仅包含 `archive_root`，升级安装不覆盖配置。

## Web 目录授权

浏览器插件选择目录后必须明确取得 `readwrite` 权限，并通过创建、写入、删除临时测试文件验证真实写入能力。只有验证通过才能保存目录句柄。句柄存在但权限不是 `granted` 时应提示用户重新设置，不能把“已选择目录”显示成配置成功。扩展更新后，用户需要在浏览器扩展管理页重新加载本地扩展。

浏览器采集核心与站点页面结构必须分离。通用采集器只处理滚动、稳定等待、去重、排序、截止点、任务状态和写入；域名、会话 ID、消息节点、角色、正文和站点专属排除规则由 `assets/browser-extension/providers/` 中的适配器提供。当前只有 ChatGPT 适配器，不能把未注册或未验证的站点表述为已支持。

## 文件与格式边界

Codex 输出为 `<archive-root>/sessions/codex/<YYYY-MM>/<session-id>/session.jsonl`；年月取来源 Session 开始时间，不重新生成时间。ChatGPT Web 输出为 `<archive-root>/sessions/chatgpt/<conversation-id>/conversation.raw-record.md`。默认 `<archive-root>` 是 `D:\MyData\yalo-note`；浏览器首次使用时必须通过目录选择器授予该目录的写权限。两种文件都属于原始记录，不是总结。重复调用更新同一文件；失败不得冒充成功。

适配的是已实测的 Codex 桌面 rollout 结构。`session_meta` 只用于身份校验，因其内嵌基础指令，整行不保存。所有保留行的字节、字段、顺序、时间原样保留。内部 `user` 消息按 provenance 排除；未知或混合来源、未知可见性、未知记录类型均显式失败，不语义猜测。

内嵌图片不复制。普通项目文件保留原引用；可识别的本地临时附件复制到 `assets`，保留原名，内容冲突时追加来源记录 ID。无附件不创建该目录。不生成 manifest、Markdown、摘要或索引。读取档案时可用原引用的文件名查找 `assets`；冲突时结合来源记录 ID。缺失临时附件时报错，保留原来源及上次档案，不声称完成。

详见 [已验证的格式与限制](references/format.md)。浏览器插件的安装和使用见 [Web 插件说明](assets/browser-extension/README.md)。扩展格式应先检查真实结构并补充行为测试；不要把未知事件简单加入忽略列表以求成功。
