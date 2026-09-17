# Yalo Note It

对 AI 说“亚楼记一下”，把当前 Codex 会话原样存到本地。

这是用户主动返回的收尾信号，不代表认可所有答案或问题已经解决。保留可见人机对话、工具调用及实际收到的工具结果，不生成摘要、项目笔记或知识分类。

## 当前状态

早期版本，已在 Windows、Python 3.14 和 Codex Desktop rollout `0.153.4` 上验证单文件会话。运行代码只依赖 Python 3.11+ 标准库。

**编辑历史消息后产生的多文件分支，暂不支持归档。** 真实会话可能通过 `history_base` 继承其他文件的部分历史。目前遇到多个候选来源会停止，不猜选最新文件，也不拼接未经验证的内容。这是下一步需要实现的能力。

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

## 使用

在 Codex 对话中发送：

```text
亚楼记一下
```

首次使用会要求输入一个明确的本地绝对路径。例如：

```text
D:\MyData
```

确认后在该路径下创建 `yalo note` 文件夹并保存配置，因此示例的实际归档根目录是 `D:\MyData\yalo note\`。本工具不提供默认路径。后续使用同一路径；失效时明确报错。更改路径时会询问是否把原有内容全部迁移过去，未明确选择时不会切换配置。单独通过 `$yalo-note-it` 提及 Skill 时，会先询问是否要归档。

归档截止于用户发出请求的那条消息；执行归档的 AI 回合和首次路径确认消息不进入该次档案。

## 输出与隐私

```text
<用户路径>/yalo note/sessions/codex/<YYYY-MM>/<session-id>/
  session.jsonl
  assets/          # 仅在需要复制临时附件时创建
```

月份取来源会话的开始时间。同一会话重复归档，原子更新同一份 `session.jsonl`；写入失败保留上次成功档案。所有保留行的原始字节、时间与顺序不改写。

- 排除隐藏推理、系统/开发者指令、遥测和内部上下文；`session_meta` 因内嵌基础指令整行排除，仅用于来源校验。
- 内嵌图片不重复复制，普通项目文件保留原引用，可识别的必要临时附件复制到 `assets`。
- 不上传、不分享、不自动脱敏。档案可能包含凭证、私有代码或个人信息，请选择合适的本地位置。
- 独立配置位于 `%USERPROFILE%\.ai-native-brand\archive-config.json`，升级 Skill 不覆盖配置。

## 开发与验证

```powershell
python -B -X utf8 -m unittest discover -s tests -v
```

CI 配置为在 Windows 的 Python 3.11 和 3.14 上运行同一套测试。测试使用临时目录及合成记录，不读取个人会话，不需要凭证或归档配置。

主要文件：

- `skills/yalo-note-it/SKILL.md`：触发、首次配置和执行说明。
- `skills/yalo-note-it/scripts/archive_session.py`：身份定位、截止点、配置及安全写入。
- `skills/yalo-note-it/scripts/codex_format.py`：已验证格式的记录筛选。
- `skills/yalo-note-it/scripts/session_assets.py`：本地临时附件识别及复制。
- `tests/test_session_archive.py`：行为测试。

内部格式变化、历史分支与未知附件编码需要先验证再扩展适配，不应通过忽略未知记录让测试通过。详细兼容范围见 [格式说明](skills/yalo-note-it/references/format.md)。
