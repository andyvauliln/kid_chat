<!--
Used by:
- `src/group_chat_telegram_ai/daily_report.py` → `DAILY_REPORT_STAGE1_PROMPT_PATH` (Stage 1 planning prompt)
Purpose (meta): Produce a day summary + a list of files to update (planning only; no file content edits).
-->

You are preparing a daily update run (STAGE 1: planning only).

You will receive:
- `date`
- `daily_report_messages` for the day
- `updates_context` (tracked files + file structures + prompt mapping)

Your job:
- Produce a short day summary (markdown string).
- Decide what files should be updated based on the day's messages.
- Do NOT update files in this stage. Do NOT output file contents. Planning only.
- Output JSON only.

Output schema:
{
  "summary": "markdown string",
  "update_plan": [
    {
      "file": "data/app_pages/Education.md",
      "format": "md|json",
      "reasoning": "why this file must be updated based on messages (string). Must quote short fragments from daily_report_messages.",
      "updated_fields": ["for md: section headings; for json: keys or dotted paths"],
      "prompt_key": "md_page|json_app"
    }
  ]
}

Rules:
- If daily_report_messages is empty, set summary to '(no messages)' and output update_plan=[].
- `reasoning` must explicitly cite the message(s) that triggered the update (quote short fragments).
- Only include files present in updates_context.tracked_files.
- Choose prompt_key based on file type:
  - md files => prompt_key='md_page'
  - json files => prompt_key='json_app'
- Do not output items for files that should not change.
