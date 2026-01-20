<!--
Used by:
- `src/group_chat_telegram_ai/daily_report.py` → `DAILY_REPORT_UPDATES_CONTEXT_PROMPT_PATH`
- `src/group_chat_telegram_ai/update_engine.py` → `UPDATES_CONTEXT_PROMPT_PATH`
Purpose (meta): Shared guidance for how the LLM should use `updates_context` (tracked files + structures) when planning/updating.
-->

You will receive an `updates_context` object that describes:
- tracked app files (markdown + json)
- prompts available in this repo
- prompt mapping keys (which prompt to use for which task)
- per-file structure summaries (headings for md, keys/shape for json)

Use `updates_context` to:
- choose the correct file(s) to update
- keep updates minimal and consistent with each file's structure
- avoid proposing changes to non-tracked files

Rules:
- Do not invent new files.
- Do not invent new sections/keys unless the day messages clearly require it.
- When referencing structure, prefer existing headings/keys from `updates_context.file_structures`.
- All changes are Not Approved until explicitly accepted; treat updates as proposals.