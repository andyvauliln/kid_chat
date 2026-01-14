You are updating ONE JSON app data file (STAGE 2).

You will receive:
- `date`
- `daily_report_messages`
- `target_file` (path)
- `current_content` (full JSON file text)
- `updated_fields` (keys/dotted paths to focus on)
- `reasoning` (why to update, with message quotes)
- `updates_context` (tracked files + structure)

Your job:
- Update ONLY the `target_file` data using structured change operations.
- Output JSON only (no markdown).

Output schema (single object, NOT wrapped in a list):
{
  "file": "data/app_json/todo_list.json",
  "format": "json",
  "reasoning": "why this file was updated (must include short quotes from daily_report_messages)",
  "updated_fields": ["list of keys/dotted paths you updated"],
  "changes": [
    {"type": "added", "data": {"id": 123, "...": "..."}},
    {"type": "updated", "data": {"id": 123, "someField": "new value"}},
    {"type": "removed", "data": {"ids": [123]}}
  ]
}

Rules:
- For format=json: do NOT include full_document anywhere.
- Prefer minimal changes: only the objects/ids that are clearly required by the messages.
- If your `data` includes an object with `id`, keep `id` stable.
- Do NOT use dotted keys inside `changes[].data`. Use nested JSON objects if you need to update nested fields.
- Do not update any other file.
