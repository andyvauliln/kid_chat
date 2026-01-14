You are updating ONE markdown app page file: `data/app_pages/Education.md` (STAGE 2).

Scope:
- This file tracks Dante's educational goals, status, and learning plans.
- Keep the structure consistent with existing headings and numbering.

You will receive:
- `date`
- `daily_report_messages`
- `target_file` (will be `data/app_pages/Education.md`)
- `current_content` (full markdown file text)
- `updated_fields` (headings/sections to focus on)
- `reasoning` (why to update, with message quotes)
- `updates_context` (tracked files + structure)

Your job:
- Update ONLY `target_file`.
- Prefer small edits inside existing sections like numeracy/literacy/language/time concepts.
- When adding new content, keep it actionable (steps, routines, resources) and short.
- Output JSON only.

Output schema:
{
  "file": "data/app_pages/Education.md",
  "format": "md",
  "reasoning": "must include short quotes from daily_report_messages",
  "updated_fields": ["headings/sections you updated"],
  "changes": [
    {
      "type": "updated",
      "data": null,
      "full_document": "FULL updated markdown file content"
    }
  ]
}

Rules:
- Exactly ONE change with type=updated and non-empty full_document.
- Do not update any other file.
