# Verified formats and operational limits

## Source formats

Codex Desktop is read through the official App Server Thread API first. A strict,
byte-preserving rollout adapter remains only for installations that explicitly do
not support the required methods.

## Codex App Server

- `thread/read` supplies identity and metadata only. `includeTurns=true` is not a
  completeness mechanism.
- History uses `thread/turns/list` with `sortDirection=asc` and
  `itemsView=full`, following `nextCursor` until it is null. A non-full turn is
  hydrated through fully paginated `thread/items/list`.
- Duplicate cursors, duplicate Turn/item IDs, non-ascending history, mismatched
  identity and inconsistent item pages fail explicitly.
- `CODEX_THREAD_ID` identifies the current Thread. `CODEX_SESSION_ID` can identify
  its runtime session tree and need not equal either `thread.id` or
  `thread.sessionId`; the API identity and declared parent/fork relationship are
  validated without conflating these fields.
- Visible records are `userMessage`, `agentMessage`, `plan`, command/file/tool,
  collaboration, web/image and review-mode work items. `reasoning`, `hookPrompt`
  and `contextCompaction` are validated as internal and excluded. Every unknown
  item or user-content type fails closed.
- The cutoff fingerprint is SHA-256 over canonical JSON containing both `turnId`
  and the complete user item. A requested cutoff that is not visible after a
  bounded retry fails; it never falls back to an earlier user item.
- Only an unavailable App Server command or explicit JSON-RPC method-not-supported
  response enables the rollout adapter. Permission, identity, protocol,
  pagination and content failures never do.

The archive directory contains `thread.json` and a commit manifest with source
contract, Thread/Session IDs, CLI/user-agent data, cutoff, completeness, counts
and SHA-256 hashes. API history is never written under a `.jsonl` name.

## Codex Desktop rollout fallback

Observed locally on Codex Desktop rollouts from `cli_version=0.115` through
`0.155.0-alpha.16.4`, including `0.142.0-alpha.6` history branches.
This is a strict adapter for an internal format, not a claim of a stable Codex API.

- The source first row has `payload.id` matching `CODEX_THREAD_ID` and
  `payload.session_id` matching the optional `CODEX_SESSION_ID`.
- Source names include the Thread UUID and edited-history names can also include the
  session-tree UUID under `CODEX_HOME/sessions`; `archived_sessions` is searched by
  the same verified identities. Never select by recency.
- Current user input was observed on disk before executing tools. Each invocation verifies its own complete, newline-terminated cutoff row; the observation is not a timing guarantee.
- Metadata includes `base_instructions`, so the user approved excluding the entire metadata row. It is still required to identify the source and choose the session year/month.

### Compatibility matrix

| Record family | Preserved | Excluded after validation | Rejected |
|---|---|---|---|
| User messages | `user.text`, `user.image`; verified pre-provenance id-less ordinary text/images, either without metadata or with only a non-empty `turn_id` (including inline markup, file attachments, and both verified ambient-browser request envelopes) | `plugins.recommendations`, `agents_md.instructions`, `environments.environment_context`, `skills.selected_skill_instructions`, `additional_content.codex_apps_writing_block_edits`; verified legacy plugin, AGENTS, environment, startup, ambient-browser-only, and exact host `turn_aborted` envelopes | Current id-bearing messages without provenance; unknown or mixed provenance outside the verified legacy browser-request shapes; complete unknown legacy wrappers; internal legacy envelopes mixed with images |
| Assistant messages | `commentary`, `final`, `final_answer`, including verified pre-ID records | `analysis`, `reasoning`; verified realtime transport prefixes | Missing or unknown visibility phase |
| Tools | `custom_tool_call`, `function_call`; legacy outputs keyed only by `call_id`; outputs represented as a string or `input_text`/`input_image` blocks; verified `web_search_call` search records and `image_generation_call` records containing embedded PNG base64 | — | Unknown call/output shapes, statuses, actions, result encodings, or content blocks |
| Agent transport | — | Validated `agent_message` text/encrypted transport and `inter_agent_communication_metadata` with boolean `trigger_turn` | Unknown routing, content, or metadata shapes |
| Realtime | Validated user/assistant `transcript_segment` | Session start/close transport and realtime delegation wrappers | Unknown realtime event or transcript shape |
| Session/UI internals | — | `session_meta`, `world_state`, `turn_context`, `token_usage_record`, `compacted`, `reasoning`; known events `task_started`, `task_complete`, `thread_settings_applied`, `token_count`, `item_completed`, `turn_aborted` | Every other top-level, response-item, or event kind |

Image content uses `input_image` with `image_url` and optional `detail`; embedded data URLs and verified image-generation results stay in the source record. Tool result strings are normalized only for attachment inspection—the archived bytes are never rewritten. Missing legacy message/output IDs are never added: attachment naming uses the source ordinal for messages and the existing `call_id` for outputs. A trigger selected as the archive cutoff must still have its real, non-empty message ID. No synthetic execution status is added when the source omits it.

Other formats fail explicitly pending verification. No silent compatibility fallback.

## Maintenance boundaries

- `archive_model.py` owns the format-neutral `ArchivePackage`, references and asset-copy plan.
- `app_server_client.py` owns App Server process/JSON-RPC lifecycle and complete pagination.
- `thread_source.py` owns API identity, visibility, cutoff, completeness and mapping into `ArchivePackage`.
- `codex_format.py` owns fallback rollout classification and conversion to narrow `ReferenceSource` values.
- `session_source.py` owns fallback source identity, history reconstruction, JSONL reading, cutoff verification, and package conversion.
- `session_assets.py` owns format-neutral reference discovery and attachment copying; it does not inspect Codex payload kinds.
- `archive_store.py` owns configuration, migration, atomic replacement, locking, and archive persistence.
- `runtime_context.py` owns distinct Thread/Session runtime identity and home-directory resolution.
- `archive_trigger.py` owns the explicit archive-authorization phrase matcher.
- `record_session.py` is only the command-line application coordinator. `archive_errors.py` defines the shared user-reportable error contract.

To support a new observed format, capture its exact structural evidence, classify it as preserved or internal, change only the owning adapter, add positive and fail-closed boundary tests, update the matrix, and run both the full suite and a real read-only `check`. Do not add a broad ignore rule or a Session-specific condition.

## Attachments

Scan retained visible messages and tool outputs, not tool input code, for explicit absolute local references: Markdown links, typed image blocks, and whole path strings (including JSON values). Paths merely mentioned inside source code, tracebacks and diagnostic prose are not attachment declarations. Recognize OS temp environment directories plus Codex `tmp`, `clipboard`, `generated_images`, and `visualizations`. Preserve ordinary directory references; do not recursively copy directories. Non-embedded image references that are not absolute local files fail because their durability is not established.

Original records are never edited. An asset can be found from its original basename; a differing-content basename collision uses `stem-record_id.ext`. If that also collides, fail. Identical content at a name reuses the existing copy. Changed/missing source assets fail before replacing the archive. Each replacement is built in a sibling staging directory and activated only after its files and manifest are complete. A process kill can leave an explicit lock, staging directory or backup directory, which requires inspection before retrying.

This is reference recognition, not semantic recovery: unrecognized attachment encodings require adapter support. No network retrieval or tool replay. No source mutation, redaction, summaries or generated archive timestamps.

## Invocation

Implicit skill selection is enabled and the description names both Codex triggers: Chinese `亚楼 … 记一下` and case-insensitive English `yalo … note it`. The English trigger requires `yalo` and `note` to be separate words and whitespace between `note` and `it`. Skill selection is model-mediated, not a host-level literal command hook. It requires a real user invocation after installation to validate discovery in the user's app; do not claim that metadata validation alone proves routing reliability.

Official skill documentation: https://learn.chatgpt.com/docs/build-skills

## Edited-message history branches

A child history file declares `history_base.thread_id`, `end_ordinal_exclusive`, and `end_byte_offset`. The adapter normally resolves each parent only when exactly one candidate has a complete newline-terminated prefix at that byte offset, contains exactly the declared number of records, and ends at ordinal `end_ordinal_exclusive - 1`. Verified legacy `paginated` histories can contain stale byte offsets; for those only, the adapter derives the boundary by reading the uniquely identified parent from its first ordinal through `end_ordinal_exclusive - 1` and requiring a complete, strictly contiguous JSONL sequence. It then reads that verified parent prefix followed by the child file. Abandoned parent records after the boundary are not retained.

Filename matching, session ID, and modification time are never sufficient on their own. Cycles, ambiguous parents, invalid boundaries, multiple active leaves, and unrelated candidates (including guardian-review files sharing the session ID) still fail explicitly rather than being guessed or concatenated.

An additional narrow recovery handles an offline submission replay: when candidates have no `history_base`, a stale candidate may be discarded only if all of its retained records are unanswered user messages and they equal the opening records of one unique, strictly longer candidate after normalizing only CRLF and trailing line breaks. Equal complete copies, differing messages, assistant/tool records in the stale candidate, or more than one possible complete candidate remain ambiguous and fail. File time, size, suffix, and `multi_agent_version` are not selection signals.
