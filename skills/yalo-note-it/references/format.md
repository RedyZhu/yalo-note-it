# Verified format and operational limits

## Source formats

Codex Desktop preserves verified visible rollout records byte-for-byte as `session.jsonl`.

## Codex Desktop

Observed locally on 2026-09-10, Codex Desktop rollout `cli_version=0.153.4`.
This is a strict adapter for an internal format, not a claim of a stable Codex API.

- Current shell exposes `CODEX_SESSION_ID` and `CODEX_THREAD_ID`. The source first row has matching `payload.session_id` and `payload.id`.
- Source names include the session UUID under `CODEX_HOME/sessions`; `archived_sessions` is also searched for the exact UUID. Never select by recency.
- Current user input was observed on disk before executing tools. Each invocation verifies its own complete, newline-terminated cutoff row; the observation is not a timing guarantee.
- User messages carry `internal_chat_message_metadata_passthrough.content_item_kinds`. Verified visible kinds: `user.text`, `user.image`. Verified injected kinds: `plugins.recommendations`, `agents_md.instructions`, `environments.environment_context`. Mixed/unknown provenance is rejected rather than editing the row.
- Assistant visible phases observed: `commentary`, `final_answer`. `final` is supported as the equivalent declared channel. Analysis/reasoning is excluded.
- Tool calls use `custom_tool_call`; results use `custom_tool_call_output` with a list of `input_text` blocks. Image content uses `input_image` with `image_url` and optional `detail`. Embedded image URLs are retained in place.
- Additional observed tool forms: `function_call` with `id`, `call_id`, `name`, string `arguments`; `function_call_output` with `id`, `call_id`, and either string `output` or a non-empty list of `input_text` blocks. These are preserved too; no synthetic execution status is added when the source omits it.
- Known excluded top-level records: `world_state`, `turn_context`, `token_usage_record`. Known event messages: task start/complete, thread settings, token count and `item_completed` (UI mirrors of response records). `reasoning` response items are excluded, without reading or extracting their content.
- Metadata includes `base_instructions`, so the user approved excluding the entire metadata row. It is still required to identify the source and choose the session year/month.
- Other formats (including legacy messages without provenance, alternate tool representations, compaction records and new attachment types) fail explicitly pending verification. No silent compatibility fallback.

## Attachments

Scan retained visible messages and tool outputs, not tool input code, for explicit absolute local references: Markdown links, typed image blocks, and whole path strings (including JSON values). Paths merely mentioned inside source code, tracebacks and diagnostic prose are not attachment declarations. Recognize OS temp environment directories plus Codex `tmp`, `clipboard`, `generated_images`, and `visualizations`. Preserve ordinary directory references; do not recursively copy directories. Non-embedded image references that are not absolute local files fail because their durability is not established.

Original records are never edited. An asset can be found from its original basename; a differing-content basename collision uses `stem-record_id.ext`. If that also collides, fail. Identical content at a name reuses the existing copy. Changed/missing source assets fail before replacing the archive. A failed run can leave an empty Session directory but cannot replace the prior successful JSONL; newly created asset files are removed on handled failures. A process kill can leave an explicit lock and partial ancillary files, which require inspection before retrying.

This is reference recognition, not semantic recovery: unrecognized attachment encodings require adapter support. No network retrieval or tool replay. No source mutation, redaction, summaries, additional metadata or archive timestamps.

## Invocation

Implicit skill selection is enabled and the description names both Codex triggers: Chinese `亚楼 … 记一下` and case-insensitive English `yalo … note it`. The English trigger requires `yalo` and `note` to be separate words and whitespace between `note` and `it`. Skill selection is model-mediated, not a host-level literal command hook. It requires a real user invocation after installation to validate discovery in the user's app; do not claim that metadata validation alone proves routing reliability.

Official skill documentation: https://learn.chatgpt.com/docs/build-skills

## Edited-message history branches

A child history file declares `history_base.thread_id`, `end_ordinal_exclusive`, and `end_byte_offset`. The adapter resolves each parent only when exactly one candidate has a complete newline-terminated prefix at that byte offset, contains exactly the declared number of records, and ends at ordinal `end_ordinal_exclusive - 1`. It then reads the verified parent prefix followed by the child file. Abandoned parent records after the boundary are not retained.

Filename matching, session ID, and modification time are never sufficient on their own. Cycles, ambiguous parents, invalid boundaries, multiple active leaves, and unrelated candidates (including guardian-review files sharing the session ID) still fail explicitly rather than being guessed or concatenated.

An additional narrow recovery handles an offline submission replay: when candidates have no `history_base`, a stale candidate may be discarded only if all of its retained records are unanswered user messages and they equal the opening records of one unique, strictly longer candidate after normalizing only CRLF and trailing line breaks. Equal complete copies, differing messages, assistant/tool records in the stale candidate, or more than one possible complete candidate remain ambiguous and fail. File time, size, suffix, and `multi_agent_version` are not selection signals.
