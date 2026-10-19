# Yalo Note It

对 AI 说“亚楼记一下”，把当前 Codex 会话原样存到本地。

这是用户主动返回的收尾信号，不代表认可所有答案或问题已经解决。保留可见人机对话、工具调用及实际收到的工具结果，不生成摘要、项目笔记或知识分类。

## 当前状态

Codex 主路径通过官方 App Server Thread API 读取当前 Thread，并保存为可校验的结构化档案。仅当 App Server 命令缺失或明确返回“不支持该方法”时，才使用严格的 rollout JSONL 兼容适配器。运行代码只依赖 Python 3.11+ 标准库。

来源身份、分页、可见性、记录类型或附件无法验证时会显式停止，不猜选会话、不缩短截止点，也不把未知数据当作可安全忽略的内部信息。rollout fallback 支持经验证的编辑历史边界；歧义分支仍会失败关闭。

## 安装

将本仓库的 `skills/yalo-note-it` 文件夹安装到个人 Codex Skill 目录。Windows PowerShell 示例：

```powershell
git clone https://github.com/RedyZhu/yalo-note-it.git
cd yalo-note-it
$skillHome = if ($env:CODEX_HOME) { $env:CODEX_HOME } else { Join-Path $env:USERPROFILE '.codex' }
$skillTarget = Join-Path $skillHome 'skills\yalo-note-it'
if (Test-Path -LiteralPath $skillTarget) { throw 'Skill 已存在，请先检查已有版本。' }
New-Item -ItemType Directory -Path (Split-Path -Parent $skillTarget) -Force | Out-Null
Copy-Item -LiteralPath '.\skills\yalo-note-it' -Destination $skillTarget -Recurse
```

安装只复制 Skill 文件，不初始化归档目录、不扫描或保存会话。需要让 Codex 加载新 Skill 后再使用；自然语言唤起依赖模型选择 Skill，并非宿主的确定性命令钩子。

### 浏览器扩展

浏览器扩展是独立产品，不随 Codex Skill 安装。Chrome 或 Edge 开启开发者模式后，使用“加载已解压的扩展程序”选择：

```text
browser-extension/yalo-note-it
```

需要生成 Chrome 安装包时，也只把这个目录作为扩展程序根目录。目录内直接包含 `manifest.json`，无需复制到 Skill。

## 使用

在 Codex 对话中发送：

```text
亚楼记一下
```

首次使用会要求输入一个明确的本地绝对路径。例如：

```text
D:\MyData
```

确认后在该路径下创建 `YaloNote` 文件夹并保存配置，因此示例的实际归档根目录是 `D:\MyData\YaloNote\`。本工具不提供默认路径。已经配置的旧名称 `yalo note` 或 `yalo-note` 继续使用，不会自动改名或移动。后续使用同一路径；失效时明确报错。更改路径时会询问是否把原有内容全部迁移过去，未明确选择时不会切换配置。单独通过 `$yalo-note-it` 提及 Skill 时，会先询问是否要归档。

归档截止于用户发出请求的那条消息；执行归档的 AI 回合和首次路径确认消息不进入该次档案。

## 输出与隐私

```text
<用户路径>/YaloNote/
  .yalo-note-location.json
  sessions/codex/<YYYY-MM>/<thread-id>/
    manifest.json
    thread.json
    assets/                    # 仅在需要复制临时附件时创建
    raw/session.jsonl          # 仅 rollout fallback 保存
```

月份取 Thread 开始时间。`thread.json` 保存可见 Thread 数据；`manifest.json` 记录来源契约、Thread/Session 身份、截止 item 及指纹、完整性、数量和文件哈希。App Server 数据不冒充 JSONL；只有 fallback 额外保留字节不改写的 `raw/session.jsonl`。同一 Thread 重复归档会原子替换整个目录，失败时保留上次成功档案。

- App Server 主路径保留可见用户、助手及工作过程 item，排除隐藏推理、hook prompt 和上下文压缩；rollout fallback 中 `session_meta` 只用于身份校验，不写入原始证据。
- 内嵌图片不重复复制，普通项目文件保留原引用，可识别的必要临时附件复制到 `assets`。
- 不上传、不分享、不自动脱敏。档案可能包含凭证、私有代码或个人信息，请选择合适的本地位置。
- 独立配置位于 `%USERPROFILE%\.ai-native-brand\archive-config.json`，升级 Skill 不覆盖配置。

## 开发与验证

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

CI 在 Windows 的 Python 3.11/3.14 与 Node.js 22 环境中运行完整测试。测试使用临时目录及合成记录，不读取个人会话，不需要凭证或归档配置。

主要文件：

- `skills/yalo-note-it/SKILL.md`：触发、首次配置和执行说明。
- `skills/yalo-note-it/scripts/record_session.py`：命令行协调器。
- `skills/yalo-note-it/scripts/app_server_client.py` 与 `thread_source.py`：App Server 生命周期、完整分页、身份与可见性校验。
- `skills/yalo-note-it/scripts/session_source.py` 与 `codex_format.py`：rollout JSONL fallback 的历史恢复和严格分类。
- `skills/yalo-note-it/scripts/archive_model.py` 与 `archive_store.py`：格式无关的归档包及原子持久化。
- `skills/yalo-note-it/scripts/session_assets.py`：本地临时附件识别及复制。
- `tests/test_session_record.py`：行为测试。
- `browser-extension/yalo-note-it/`：独立的 ChatGPT/DeepSeek 浏览器扩展，可直接加载或打包。

内部格式变化、历史分支与未知附件编码需要先验证再扩展适配，不应通过忽略未知记录让测试通过。详细兼容范围见 [格式说明](skills/yalo-note-it/references/format.md)。
