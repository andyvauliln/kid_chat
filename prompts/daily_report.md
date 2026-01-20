<!--
Used by:
- `src/group_chat_telegram_ai/daily_report.py` → `DAILY_REPORT_PROMPT_PATH` (legacy/alternate stage that outputs summary + updates)
Purpose (meta): Produce a day summary and propose concrete file updates (md via full_document; json via change operations).
-->

You are preparing a daily update run.
You will receive:
- the daily report messages for the day
- current contents of ALL app files (markdown + json)

Your job:
- Produce a short day summary
- Decide what files should be updated based on the day's messages
- Output JSON only.

Output schema:
{
  "summary": "markdown string",
  "updates": [
    {
      "file": "data/app_pages/Education.md",
      "format": "md|json",
      "reasoning": "why this file must be updated based on messages (string)",
      "updated_fields": ["field/path list (for md: section headings; for json: keys or dotted paths)"],
      "changes": [
        {"type": "added", "data": "..."} ,
        {"type": "removed", "data": "..."} ,
        {"type": "updated", "data": "...", "full_document": "FULL_FILE_CONTENT_FOR_MD"}
      ]
    }
  ]
}

Rules:
- If daily_report_messages is empty, set summary to '(no messages)' and output updates=[].
- For format=md: include exactly one change with type=updated and a full_document.
- For format=json: do NOT include full_document. Use changes with type added/removed/updated and put the structured object(s) in `data`.
- Always include `reasoning` and `updated_fields` for every item in updates.
- `reasoning` must explicitly cite the message(s) that triggered the update (quote short fragments).
- Do not output updates for files that should not change.
