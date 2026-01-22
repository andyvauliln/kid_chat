Project: group_chat

Purpose
- Telegram bot with agent flow for planning/execution and manual test runner.

Key entry points
- Bot startup and handlers: src/group_chat_telegram_ai/bot.py
- Command registry: src/group_chat_telegram_ai/telegram_commands.py
- Agent logic and /agent_test: src/group_chat_telegram_ai/agent_command.py

Agent test flow
- Tests live in tests/agent_test_cases.json
- Status values: untested | passed | skiped
- plan/result fields store the latest successful plan output
- /agent_test runs the first untested test
- Replies: ok / not / skip, or feedback to revise plan

Logs and data
- Agent logs: data/agent_logs.jsonl
- Sessions: data/agent_sessions.json
- App data pages/json: data/app_pages, data/app_json

Working rules
- Keep code simple and split by function.
- Avoid extra logic not requested.
- Run the relevant tests after changes.
