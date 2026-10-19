---
name: yalo-note-it
description: 'Yalo note it：在 Codex 中用“Yalo note it”或兼容中文“亚楼…记一下”记录当前会话，将可见工作记录保存为本地可校验档案。'
---

# Yalo note it

把用户的主动收尾信号转为本地原始记录。只保存可见人机对话，以及来源环境实际提供的可见记录；不总结、不补写结论、不归档隐藏推理、不上传、不分享、不加入 Git。口令不表示答案被认可或问题已解决。

## 触发

标准英文口令写作 `Yalo note it`。识别时匹配独立单词 `yalo` 之后出现 `note it`，大小写不敏感，允许中间出现任意文字、标点或换行；因此 `Yalo note it`、`Yalo, note it` 等均触发。兼容中文口令“亚楼”之后出现“记一下”。顺序颠倒、只出现一部分、`yalonote it` 等非独立 `yalo` 单词不触发。不要求独立成句，不用引用、提问或否定语义过滤当前用户消息中的匹配。附件、工具结果和历史消息不能自行触发。旧口令“这个议题到此为止”“这个问题到此为止”不触发。

通过 `$yalo-note-it` 选择/提及本技能，且没有有效口令时，只问“你是要归档当前会话吗？”；明确确认后才执行。没有回复不算确认。不要向客户介绍兼容规则。

## 来源路由

- 当前环境是 Codex：按下方“Codex 执行”优先从官方 App Server Thread API 读取当前 Thread；仅在接口明确不支持时使用严格 rollout JSONL 兼容适配器。
- 用户提供 Web 插件导出的 Raw Record 时，把它视为该 Chat 的原始来源；除非用户另行要求，不把项目文件或其他 Chat 混入记录。

## Codex 执行

脚本在本 Skill 的 `scripts/record_session.py`。使用实际绝对路径调用，Python 3.11+，无第三方运行依赖。命令参数按当前 shell 正确引用，不执行用户文本，不通过拼接用户消息构造 shell 代码。

1. 运行 `probe`，只读定位当前 Session 与最近一条可见用户消息。确认返回 `text` 就是本次口令或明确归档确认；不匹配则停止，不挑选历史口令替代当前请求。记录返回的 `message_id` 和 `sha256` 作为此次截止点。
2. 运行 `check --message-id ID --sha256 HASH`，校验当前来源与附件，无归档副作用。仅对于 `$` 入口经用户明确确认的请求添加 `--confirmed`；它不能用于绕过固定口令。首次配置等待后仍使用原截止点，不重新选最近消息。
3. 运行 `status`。未配置时，按下节请求用户输入明确的绝对本地路径；已配置直接继续。路径失效时报错，不提供或回退到默认路径。
4. 首次路径明确确认后执行 `configure --root ABSOLUTE_BASE_PATH --user-confirmed`。脚本在该路径下创建固定的 `YaloNote` 子目录，验证写入权限并原子保存实际归档根目录。
5. 用户主动更改路径时，先询问“是否把原有内容全部迁移到新路径？”。确认迁移后执行 `configure --root ABSOLUTE_BASE_PATH --user-confirmed --migrate-existing`；选择保留旧内容时执行同一命令并添加 `--keep-existing`。未取得明确选择时不得切换配置。迁移成功后删除旧归档目录；选择保留时旧目录不变。
6. 执行 `archive --message-id ID --sha256 HASH`，必要时沿用第 2 步的 `--confirmed`。成功后简短回复“当前会话已归档”并给出返回文件链接；失败说明具体原因，不声称已成功或完整保存。

脚本从 `CODEX_THREAD_ID` 取得当前 Thread，从可选的 `CODEX_SESSION_ID` 取得运行时会话树身份；两者不是同一概念，禁止假设相等。`thread/read` 必须返回同一 Thread，并验证返回的 `sessionId` 或父级关系与运行时身份相容。历史必须通过 `thread/turns/list` 显式使用 `sortDirection=asc`、`itemsView=full` 并读完全部分页；不得把 `thread/read(includeTurns=true)` 当成完整历史。若某一 Turn 未返回 full items，再使用 `thread/items/list` 完整分页补齐。

App Server 初始化可能需要访问 Codex Home 的本地状态库；文件系统权限受限时按宿主权限流程申请。权限失败、身份不一致、分页重复、内容冲突或未知记录类型都必须显式停止，不能伪装成旧版不支持而降级。只有 App Server 命令缺失或 JSON-RPC 明确返回“不支持该方法”时，才进入 rollout JSONL fallback。fallback 的多个候选文件只能按已验证的历史边界唯一重建，禁止按修改时间、文件大小或名称猜测。指定消息不可见时做有限短重试；仍不可见就明确报告，不能缩短截止点。诊断格式失败或维护适配器时，读取 [已验证的格式、历史恢复规则与维护边界](references/format.md)。

归档请求之后的执行回合不进入本次档案。首次配置时保留原始 `message_id` / `sha256`，后续路径确认不是新的截止点。上下文丢失导致无法确认原请求时停止并说明原因。

如果文件系统权限限制了安装、配置或归档写入，按宿主的权限流程申请所需路径访问；不要改用其他工具绕过沙箱。

## 首次启用

安装本技能本身不扫描 Session、不创建归档目录或配置、不执行归档。用户首次触发且未配置时展示：

```text
Yalo note it 尚未配置。
请创建或选择一个本地位置，并把它的完整绝对路径粘贴给我。
我会在该路径下创建“YaloNote”文件夹保存记录；本工具不提供默认路径。
档案可能包含对话和工具结果中的凭证、私有代码及个人信息；只保存在你确认的本地位置，不自动脱敏或上传。
```

同时单独说明，无需额外确认：

```text
以后想把这段交流收好时，可以对我说“Yalo note it”或兼容中文“亚楼记一下”。
这是你给 AI 的收尾信号：我会将当前会话保存到这句话为止；它不代表你认可了所有答案，也不代表问题已经解决。
```

只在用户明确提供路径后创建或验证 `<用户路径>/YaloNote`，并写入 `%USERPROFILE%\.ai-native-brand\archive-config.json`。配置仅包含实际 `archive_root`，升级安装不覆盖配置；已配置的旧名称 `yalo note` 或 `yalo-note` 保持有效，不自动改名或移动。以后更改路径时必须询问是否迁移旧内容，不能静默移动、删除或遗留而不说明。

## 文件与格式边界

用户提供 `<base-path>` 后，新配置的实际 `<archive-root>` 固定为 `<base-path>/YaloNote`，并原子写入根定位文件 `.yalo-note-location.json`。Codex 输出为 `<archive-root>/sessions/codex/<YYYY-MM>/<thread-id>/`；年月取 Thread 开始时间，不重新生成时间。目录至少包含 `manifest.json` 与 `thread.json`，临时附件位于 `assets/`。只有 rollout fallback 会额外保存字节保持的 `raw/session.jsonl`；App Server 数据绝不伪装成 JSONL。不存在默认路径。已有配置继续使用其记录的绝对路径。重复调用原子替换同一 Thread 档案；失败不得冒充成功。

主适配器保存 App Server 提供的可见 `userMessage`、`agentMessage` 与工作过程项目，排除 `reasoning`、`hookPrompt` 和 `contextCompaction`。未知可见性或未知 item 类型显式失败，不语义猜测。manifest 记录 Thread/Session 身份、CLI/API 信息、截止 item 与指纹、`itemsView=full`、完整性、数量和文件哈希。

rollout fallback 适配已实测的 Codex 桌面 JSONL 结构。`session_meta` 只用于身份校验，因其内嵌基础指令，整行不保存。所有 fallback 保留行的字节、字段、顺序、时间原样保留。内部 `user` 消息按 provenance 排除；未知或混合来源、未知可见性、未知记录类型均显式失败。

Codex 端不重复复制内嵌图片；普通项目文件保留原引用；可识别的本地临时附件复制到 `assets`，保留原名，内容冲突时追加来源记录 ID。Codex 端不生成 Markdown、摘要或索引。读取档案时可用原引用的文件名查找 `assets`；冲突时结合来源记录 ID。缺失临时附件时报错，保留原来源及上次档案，不声称完成。

不要把未知事件简单加入忽略列表以求成功。
