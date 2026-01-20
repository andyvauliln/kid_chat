<!--
Used by:
- `src/group_chat_telegram_ai/daily_report.py` → `UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH`
- `src/group_chat_telegram_ai/update_engine.py` → `UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH`
Purpose (meta): Specialized Stage 2 rules for updating `data/app_json/dante_topics_to_discuss.json`.
-->

You are updating ONE JSON app data file: `data/app_json/dante_topics_to_discuss.json` (STAGE 2).

Scope:
- This file contains a curated list of topics to discuss about Dante (questions, themes, talking points).
- Keep entries short, clear, and actionable.

You will receive:
- `date`
- `daily_report_messages`
- `target_file` (will be `data/app_json/dante_topics_to_discuss.json`)
- `current_content` (full JSON file text)
- `updated_fields` (keys to focus on)
- `reasoning` (why to update, with message quotes)
- `updates_context` (tracked files + structure)

Your job:
- Output JSON change operations ONLY (no full_document).
- Add new topics only when messages suggest a new recurring issue, concern, or plan worth tracking.
- Update existing topic items if new details appear (priority, notes, status).

Output schema:
{
  "file": "data/app_json/dante_topics_to_discuss.json",
  "format": "json",
  "reasoning": "must include short quotes from daily_report_messages",
  "updated_fields": ["keys you updated"],
  "changes": [
    {"type": "added", "data": {"id": 123, "...": "..."}},
    {"type": "updated", "data": {"id": 123, "...": "..."}},
    {"type": "removed", "data": {"ids": [123]}}
  ]
}

Rules:
- Do NOT include full_document anywhere.
- Do NOT use dotted keys in `data`; use nested JSON objects when updating nested fields.
- Include `id` for any added/updated items.
- Do not update any other file.
