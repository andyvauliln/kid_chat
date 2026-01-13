# AI Message Router System

## Overview

System that processes Telegram messages (text/audio), classifies intent, updates data files, and responds with what was updated.

## Flow

```
User Message (text/audio)
    ↓
[FIRST LLM CALL]
- Transcribe audio (if audio)
- Translate to English
- Classify intent
- Detect file updates needed
- Check if needs context
- Generate response (if no context needed)
    ↓
[TELEGRAM MESSAGE 1]
- Send translated message: "[Username]: {message_en}"
    ↓
[FILE UPDATES]
- Execute all file_updates (no LLM needed for updates)
    ↓
[IF needs_context=true]
- Load context_files
- [SECOND LLM CALL] with context + question_for_next_llm
- Generate response
    ↓
[TELEGRAM MESSAGE 2]
- Send AI response with update summary
```

## Output Structure from First LLM

```json
{
  "message_en": "English translation of message",
  "username": "from input",
  "intent": "one of the intents",
  "needs_context": true/false,
  "context_files": ["file paths if needs_context=true"],
  "question_for_next_llm": "specific question for next LLM",
  "response": "AI response OR null",
  "file_updates": [
    {
      "file": "path/to/file",
      "action": "append|update|add",
      "section": "section name for markdown",
      "what": "content to add/update",
      "key": "for JSON - which id to update"
    }
  ]
}
```

## Intent Types

| Intent | Description | Needs Response |
|--------|-------------|----------------|
| `report_daily` | Daily report about Dante | Yes |
| `report_incident` | Specific incident | Yes |
| `question_guidance` | How to handle situation | Yes (needs context) |
| `question_info` | Asking for information | Yes (needs context) |
| `idea_suggestion` | Proposing for discussion | Yes |
| `concern_warning` | Expressing concern | Yes |
| `info_fyi` | Just informing | Maybe |
| `schedule_notify` | Schedule notification | Yes |
| `media_share` | Sharing photo/video | Maybe |
| `request_update` | Asking to update something | Yes |
| `request_translate` | Translate to Indonesian | Yes |
| `direct_message` | Message to specific person | No |
| `acknowledge` | Thanks, ok | No |
| `greeting` | Hi, hello | No |

## Files That Can Be Updated

### Markdown Files (append to sections)
- `data/app_pages/Dante Summary File.md` - background, psychology, progress
- `data/app_pages/Schedule.md` - weekly schedule, activity lists
- `data/app_pages/Menu.md` - food menu, likes/dislikes
- `data/app_pages/Education.md` - learning goals, progress
- `data/app_pages/Behavioral Data.md` - behavior patterns, triggers
- `data/app_pages/Andrei.md` / `Vanya.md` / `Hirja.md` - personal notes
- `data/daily_reports/YYYY-MM-DD.md` - daily message log

### JSON Files (update objects)
- `data/app_json/video.json` - video list with watch status
- `data/app_json/todo_list.json` - todos with status
- `data/app_json/dante_topics_to_discuss.json` - discussion topics

## Update Rules

1. **ALWAYS** log message to `data/daily_reports/YYYY-MM-DD.md`
2. Mentions food → update `Menu.md`
3. Mentions video watched → update `video.json`
4. Mentions todo completed → update `todo_list.json` status
5. New info about Dante → update `Dante Summary File.md`
6. Idea/suggestion → add todo for Andrei + add to relevant file
7. Schedule change → update `Schedule.md`
8. Do NOT add duplicate information

## Response Rules

1. If `needs_context=false` → provide response directly
2. If `needs_context=true` → load files, call second LLM
3. If `acknowledge`/`greeting`/`direct_message` → no response needed
4. Always mention what was updated in response
