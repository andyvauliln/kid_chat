You are updating ONE JSON app data file: `data/app_json/video.json` (STAGE 2).

Scope:
- This file contains curated video/series recommendations and metadata.
- Preserve the existing schema and keep values consistent (age range, type, duration, status, tags).

You will receive:
- `date`
- `daily_report_messages`
- `target_file` (will be `data/app_json/video.json`)
- `current_content` (full JSON file text)
- `updated_fields` (keys to focus on)
- `reasoning` (why to update, with message quotes)
- `updates_context` (tracked files + structure)

Your job:
- Output JSON change operations ONLY (no full_document).
- When adding an entry, include complete fields consistent with existing items.
- If some metadata is unknown (duration, link, etc.), set it to null rather than guessing.

Output schema:
{
  "file": "data/app_json/video.json",
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
