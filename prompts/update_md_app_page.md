You are updating ONE markdown app page file (STAGE 2).

You will receive:
- `date`
- `daily_report_messages`
- `target_file` (path)
- `current_content` (full markdown file text)
- `updated_fields` (headings/sections to focus on)
- `reasoning` (why to update, with message quotes)
- `updates_context` (tracked files + structure)

Your job:
- Update ONLY the `target_file` markdown content.
- Keep the file style consistent.
- Prefer editing existing sections/headings. Add a new section ONLY if the messages require it.
- Output JSON only (no markdown outside JSON).

Output schema (single object, NOT wrapped in a list):
{
  "file": "data/app_pages/Education.md",
  "format": "md",
  "reasoning": "why this file was updated (must include short quotes from daily_report_messages)",
  "updated_fields": ["list of headings/sections you updated"],
  "changes": [
    {
      "type": "updated",
      "data": null,
      "full_document": "FULL updated markdown file content for target_file"
    }
  ]
}

Rules:
- For format=md: include exactly ONE change with type=updated and a non-empty full_document.
- Do not update any other file.
