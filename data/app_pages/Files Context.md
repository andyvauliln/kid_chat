# Files Context

## Purpose
This file is the index of **all app pages** (`data/app_pages/*.md`) and **all LLM prompt files** (`prompts/*.md`).  
Use it to quickly understand what each file is for and what sections it contains.

## Structure
- **App Pages**: Human-readable knowledge pages used as context for the bot.
- **Prompts**: Instructions for LLM tasks (router, daily report, updates, etc.).

---

## App Pages (`data/app_pages/`)

### `data/app_pages/AI Gude.md`
- **Purpose**: Human onboarding guide explaining how to use the bot in Telegram.
- **Structure (topics)**:
  - Getting Started (Onboarding)
  - How to Use the Bot Daily (Ask Questions / Give Updates / Change the Rules)
  - Quick Tips
  - Knowledge Base (what pages exist)

### `data/app_pages/Andrei.md`
- **Purpose**: Notes/profile/context for Andrei (Papa) used by the bot.
- **Structure (topics)**:
  - Role & responsibilities
  - Time with Dante (typical)
  - Parenting principles (what to optimize)
  - Non‑negotiables & boundaries
  - Coordination with Vanya & Hirja
  - What to log/report
  - TODO / open questions

### `data/app_pages/Behavioral Data.md`
- **Purpose**: Dante behavior patterns, triggers, and recommended management strategies.
- **Structure (topics)**:
  - Purpose + Structure (header)
  - Behavioral Patterns (positive/challenging, triggers)
  - Authority and Response Patterns
  - Social Interactions
  - Current Problems and Challenges (behavioral/educational/family dynamics)
  - Current Strategy and Approach (discipline, authority reinforcement, daily reflection questions)
  - Notes / TODO

### `data/app_pages/Dante Summary File.md`
- **Purpose**: High-level summary of Dante’s background, status, and rules for caregivers.
- **Structure (topics)**:
  - Basic Information (personal details, family structure, living situation)
  - Psychological Portrait and Behavioral Type
  - Health & Medical (TBD details)
  - Daily Routine & Sleep (baseline)
  - Safety Rules & Emergency (baseline)
  - Screen Time & Media Rules (baseline)
  - Food / Education / Behavior overview (where to look)
  - Values & Social Skills (baseline)
  - Coordination & Reporting (baseline)
  - Important Reminders for All Caregivers (communication rules)

### `data/app_pages/Education.md`
- **Purpose**: Dante education goals, current status, and practical learning routines/resources.
- **Structure (topics)**:
  - Teaching Principles (baseline)
  - How to run a learning session (template)
  - Educational Goals and Current Status
    - Language Development
    - Numeracy Skills
    - Literacy Skills
    - Time Concepts

### `data/app_pages/Hirja.md`
- **Purpose**: Notes/profile/context for Hirja (nanny) used by the bot.
- **Structure (topics)**:
  - Role & responsibilities
  - Daily routines owned by nanny
  - Behavior management (day-to-day)
  - Education support (micro-sessions)
  - Food support (implementation)
  - Reporting standards (what/when/how)
  - TODO / open questions

### `data/app_pages/Menu.md`
- **Purpose**: Dante food rules and weekly menu planning (what to avoid, targets, and options).
- **Structure (topics)**:
  - Status note / confirmations
  - Health / allergies
  - Meal behavior rules (baseline)
  - NEVER (do not give him)
  - Likes / Dislikes (confirmation needed)
  - Daily targets
  - Sweets / candy rules
  - Nutrition overview (norms & safety)
  - Food options (notes)
  - Weekly menu (Mon–Sun) + daily checklist

### `data/app_pages/Schedule.md`
- **Purpose**: Weekly schedule structure and responsibilities (weekday/sat/sun).
- **Structure (topics)**:
  - Scheduling rules (baseline)
  - Schedule Overview
    - Monday–Friday structure
    - Saturday structure (Hirja)
    - Sunday structure (Andrei)

### `data/app_pages/Vanya.md`
- **Purpose**: Notes/profile/context for Vanya (Mama) used by the bot.
- **Structure (topics)**:
  - Role & responsibilities
  - Time with Dante (typical)
  - Non‑negotiables (authority + consistency)
  - How to support education (short, practical)
  - Communication / coordination rules
  - What to log/report
  - TODO / open questions

---

## Prompts (`prompts/`)

### `prompts/daily_report_updates_context.md`
- **Purpose**: Shared rules for how the LLM should use `updates_context` (tracked files + structures).
- **Prompt structure**:
  - Use `updates_context` to pick correct files and keep changes minimal
  - Constraints: don’t invent files/sections unless clearly required
- **LLM must do**: Follow file structures and propose minimal updates only to tracked files.

### `prompts/daily_report_stage1_plan.md`
- **Purpose**: Stage 1 planning prompt: produce summary + update plan (no file edits).
- **Prompt structure**:
  - Inputs: date, daily messages, updates_context
  - Output: JSON with summary + update_plan (file, format, reasoning, updated_fields, prompt_key)
- **LLM must do**: Decide which tracked files need updates and why (with message quotes).

### `prompts/daily_report.md`
- **Purpose**: (Legacy/alternate) daily report prompt that can output summary + actual updates.
- **Prompt structure**:
  - Output: JSON with summary + updates[] containing per-file changes
  - Rules: md requires full_document; json uses structured changes only
- **LLM must do**: Create a day summary and propose concrete file updates.

### `prompts/handle_income_message.md`
- **Purpose**: Normalize an incoming Telegram message (audio/text) into English + type classification.
- **Prompt structure**:
  - Inputs: username, message_raw (text or audio)
  - Output: strict JSON {message_en, username, type}
- **LLM must do**: Transcribe (if audio), translate to English, clean up, classify message type.

### `prompts/message_router.md`
- **Purpose**: Router that decides if a message needs file context and what question to ask next.
- **Prompt structure**:
  - Output: JSON {username, message_en, needs_context, context_files, question_for_next_llm, response}
  - Strict rules: no updates; mostly no responses unless translation request
- **LLM must do**: Decide context files needed (if any) and craft a clear question for the next LLM.

### `prompts/morning_plan.md`
- **Purpose**: Produce one structured “morning plan” message for the family group chat.
- **Prompt structure**:
  - Output: JSON {morning_message}
  - Requires 9 headings in exact order
- **LLM must do**: Generate a practical daily plan using provided context.

### `prompts/onboarding_router.md`
- **Purpose**: Onboarding review router (intent classification + context selection).
- **Prompt structure**:
  - Output: JSON with intent, needs_context, context_files, question_for_next_llm, response
  - Constraints: do not propose file updates
- **LLM must do**: Classify intent and pick needed app page context files.

### `prompts/update_json_app_data.md`
- **Purpose**: Stage 2 JSON updater for `data/app_json/*.json` using change operations.
- **Prompt structure**:
  - Output: JSON with changes[] of added/updated/removed (no full_document)
  - Constraints: minimal changes, stable ids
- **LLM must do**: Propose minimal structured JSON changes for a single target file.

### `prompts/update_md_app_page.md`
- **Purpose**: Stage 2 markdown updater for `data/app_pages/*.md` using full_document replacement.
- **Prompt structure**:
  - Output: JSON with exactly one md update containing full_document
  - Constraints: keep style consistent; prefer editing existing headings
- **LLM must do**: Propose an updated markdown file content for one target page.

### `prompts/update_md_file.md`
- **Purpose**: Stage 2 markdown updater for generic markdown files (non app-pages).
- **Prompt structure**:
  - Output: JSON with exactly one md update containing full_document
- **LLM must do**: Propose an updated markdown file for the target path only.

### `prompts/update__data_app_pages__Education.md`
- **Purpose**: Specialized Stage 2 updater rules for `data/app_pages/Education.md`.
- **Prompt structure**:
  - Constraints: keep numbering/structure; actionable small edits
  - Output: JSON with full_document
- **LLM must do**: Update Education plan minimally and consistently.

### `prompts/update__data_app_json__dante_topics_to_discuss.json.md`
- **Purpose**: Specialized Stage 2 updater rules for `data/app_json/dante_topics_to_discuss.json`.
- **Prompt structure**:
  - Output: JSON change operations only
- **LLM must do**: Add/update/remove discussion topics minimally.

### `prompts/update__data_app_json__todo_list.json.md`
- **Purpose**: Specialized Stage 2 updater rules for `data/app_json/todo_list.json`.
- **Prompt structure**:
  - Output: JSON change operations only; include ids
- **LLM must do**: Maintain todo list based strictly on explicit requests/status changes.

### `prompts/update__data_app_json__video.json.md`
- **Purpose**: Specialized Stage 2 updater rules for `data/app_json/video.json`.
- **Prompt structure**:
  - Output: JSON change operations only; stable ids
- **LLM must do**: Maintain the video list based on explicit additions/feedback.

---

## App Data (`data/`)

### `data/app_json/dante_topics_to_discuss.json`
- **Purpose**: Curated list of “topics to discuss with Dante” (concepts/values/skills) with tracking fields (how many times discussed, whether he “got it”).
- **Structure**:
  - `meta` (title/source/created_at/language/notes)
  - `assignment` (people, rotation notes)
  - `topics[]` items with:
    - `id`, `topic`, `timeframe`, `category`, `importance`, `assignees[]`
    - `discussion` (how_many_times_discussed, last_time_discussed, is_he_got_it, notes, example_of_explanation)

### `data/app_json/todo_list.json`
- **Purpose**: Source-of-truth todo list for adults (Andrei/Vanya/Hirja) with a simple schema embedded.
- **Structure**:
  - `schema.item` (field descriptions)
  - `items[]` todo items with:
    - `id`, `title`, `status` (todo|in_progress|done|blocked), `who`
    - `when` (day/week/month/timeframe_label)
    - `notes`
    - `source` (file/section where it came from)

### `data/app_json/video.json`
- **Purpose**: Curated video/series recommendations + watch status + safety notes.
- **Structure**:
  - `[]` list of items with:
    - `id`, `name`, `type` (series|movie)
    - `recommended_age` (min/max)
    - `duration` (episode_minutes/movie_minutes), `seasons`
    - `status` (watched/watch_times/recently_watched/liked/last_watched)
    - `link`, `trailer`, `description`, `notes`
    - `content_warnings[]`, `tags[]`, `added_by`

### `data/llm_logs.jsonl`
- **Purpose**: Append-only JSONL log of LLM calls and outputs (routing, context selection, daily report runs).
- **Structure**:
  - One JSON object per line: `datetime`, `input`, `output`, `cost`, `llm`, `context_files`

### `data/onboarding_approvals.json`
- **Purpose**: Track onboarding approval status per file/user (who reviewed which page).
- **Structure**:
  - Array of objects: `file`, `telegram_name`, `approved`, `updated_at`

### `data/pending_updates.json`
- **Purpose**: Store proposed updates that are **not approved yet** (approval workflow).
- **Used by**: `src/group_chat_telegram_ai/pending_updates.py`
- **Structure**:
  - Array of entries: `id`, `created_at`, `approval_status`, `file`, `update`, `log_entry`, `model`, `cost`, timestamps

### `data/!original_source_file.md`
- **Purpose**: Imported “source notes” (initial human document) used to seed app pages and todo list.
- **Structure**:
  - Free-form markdown (historical source; do not treat as current rules unless copied into app pages).

---

## Source Code (`src/group_chat_telegram_ai/`)

### `src/group_chat_telegram_ai/__init__.py`
- **Purpose**: Package marker and short package docstring.
- **Structure**:
  - No runtime logic.

### `src/group_chat_telegram_ai/bot.py`
- **Purpose**: Minimal Telegram bot (currently echo placeholder + `/ping`) with logging hooks.
- **Structure**:
  - `build_application()` wires handlers
  - `run_bot()` loads env and runs polling

### `src/group_chat_telegram_ai/handle_message.py`
- **Purpose**: LLM calling utilities + message routing prompt loader + LLM logging (`data/llm_logs.jsonl`).
- **Structure**:
  - Model list + pricing
  - `_call_model()` (OpenRouter call), `_append_llm_log()`
  - Router prompt path: `prompts/message_router.md`

### `src/group_chat_telegram_ai/daily_report.py`
- **Purpose**: Daily report pipeline (summary + file updates + approval workflow).
- **Structure**:
  - Stage 1 (plan) using `prompts/daily_report_stage1_plan.md`
  - Stage 2 (per-file update) using `prompts/update_*` prompts
  - Writes outputs into `reports/` and pending updates into `data/pending_updates.json`

### `src/group_chat_telegram_ai/update_engine.py`
- **Purpose**: Update router/engine for “update file X” requests (selects prompt by file type and produces pending updates).
- **Structure**:
  - Builds `updates_context` (tracked files + structures)
  - Chooses stage2 prompt and logs to `data/llm_logs.jsonl`

### `src/group_chat_telegram_ai/morning_plan.py`
- **Purpose**: Generate and optionally send the “morning plan” message using `prompts/morning_plan.md`.
- **Structure**:
  - Collects context files from `data/app_pages` + `data/app_json`
  - Writes output into `reports/*.morning_plan.*.md`

### `src/group_chat_telegram_ai/weekly_report.py`
- **Purpose**: Build weekly summaries from daily summary files in `reports/`.
- **Structure**:
  - Collect daily summaries → write `reports/YYYY-Www.summary.*.md`

### `src/group_chat_telegram_ai/monthly_report.py`
- **Purpose**: Build monthly summaries from daily summary files in `reports/`.
- **Structure**:
  - Collect daily summaries → write `reports/YYYY-MM.summary.*.md`

### `src/group_chat_telegram_ai/pending_updates.py`
- **Purpose**: Manage pending update approvals stored in `data/pending_updates.json`.
- **Structure**:
  - `add_pending_update()`, `list_pending_updates()`, `approve_pending_update()`, `reject_pending_update()`

### `src/group_chat_telegram_ai/onboarding_bot.py`
- **Purpose**: Telegram onboarding flow (review pages, approve, and route onboarding questions).
- **Structure**:
  - Uses `prompts/onboarding_router.md` and writes approvals to `data/onboarding_approvals.json`

### `src/group_chat_telegram_ai/run_onboarding.py`
- **Purpose**: CLI entrypoint to run the onboarding bot (uses env + `onboarding_bot.build_application`).
- **Structure**:
  - `--check` validates env; otherwise runs polling

### `src/group_chat_telegram_ai/telegram_commands.py`
- **Purpose**: Telegram command handlers (helper commands and admin utilities).
- **Structure**:
  - Command functions + wiring helpers.

---

## Scripts (`scripts/`)

### `scripts/report_scheduler.js`
- **Purpose**: Node cron scheduler to run python modules for morning/daily/weekly/monthly reports.
- **Structure**:
  - Defines cron tasks → spawns `python -m group_chat_telegram_ai.<module>`
  - Flags: `--dry-run`, `--run-once`

### `scripts/start_all.sh`
- **Purpose**: Start background jobs (bot + schedulers) in one command (ops convenience).
- **Structure**:
  - Shell script wrapper for starting processes.

### `scripts/close_processes.sh`
- **Purpose**: Stop/cleanup running processes (ops convenience).
- **Structure**:
  - Shell script wrapper for stopping processes.

### `scripts/run_daily_report.sh`
- **Purpose**: Run daily report module with env-controlled send/tz settings.
- **Structure**:
  - Calls `python -m group_chat_telegram_ai.daily_report ...`

### `scripts/run_weekly_report.sh`
- **Purpose**: Run weekly report module.
- **Structure**:
  - Calls `python -m group_chat_telegram_ai.weekly_report ...`

### `scripts/run_monthly_report.sh`
- **Purpose**: Run monthly report module.
- **Structure**:
  - Calls `python -m group_chat_telegram_ai.monthly_report ...`

### `scripts/run_morning_plan.sh`
- **Purpose**: Run morning plan module.
- **Structure**:
  - Calls `python -m group_chat_telegram_ai.morning_plan ...`

### `scripts/auto_update_repo.sh`
- **Purpose**: Automation hook for updating repo content (ops).
- **Structure**:
  - Shell automation script.

---

## Docs (`docs/`)

### `docs/MESSAGE_ROUTER_PLAN.md`
- **Purpose**: Design notes/plan for message routing behavior and expectations.
- **Structure**:
  - Human documentation (not used by runtime).

### `docs/Notes.md`
- **Purpose**: Working notes / brainstorm / testing ideas.
- **Structure**:
  - Free-form notes.

### `docs/operouter_models.md`
- **Purpose**: Notes about OpenRouter models and choices.
- **Structure**:
  - Free-form notes.

---

## Tests (`tests/`)

### `tests/test_message_router.py`
- **Purpose**: Automated tests for the message router behavior and output shape.
- **Structure**:
  - Pytest tests.

### `tests/test_telegram_roundtrip.py`
- **Purpose**: Integration-style test for Telegram bot command roundtrip (echo/ping flow).
- **Structure**:
  - Pytest async tests.

### `tests/run_router_tests.py`
- **Purpose**: Script to run router test cases from JSON files (manual/extended verification).
- **Structure**:
  - Loads `message_types_test.json` and writes results.

### `tests/message_types_test.json`
- **Purpose**: Test cases (inputs + expectations) for router behavior.
- **Structure**:
  - JSON with `meta` + `test_cases[]`.

### `tests/router_test_results.json`
- **Purpose**: Output file for router test run results.
- **Structure**:
  - JSON results (generated by test script).

### `tests/manual_handle_message_bot.py`
- **Purpose**: Manual harness for running/inspecting message handling logic during development.
- **Structure**:
  - Developer-only script.

### `tests/conftest.py`
- **Purpose**: Pytest configuration and shared fixtures.
- **Structure**:
  - Fixtures/helpers.

---

## Runtime Outputs

### `reports/`
- **Purpose**: Generated outputs (daily messages, summaries, updates, weekly/monthly summaries, morning plans).
- **Structure (patterns)**:
  - `YYYY-MM-DD.messages.md`
  - `YYYY-MM-DD.summary.<model>.md`
  - `YYYY-MM-DD.updates.<model>.json`
  - `YYYY-Www.summary.<model>.md`
  - `YYYY-MM.summary.<model>.md`
  - `YYYY-MM-DD.morning_plan.<model>.md`

### `logs/`
- **Purpose**: Process logs (pm2/stdout/stderr) for bot and scheduled jobs.
- **Structure (patterns)**:
  - `bot.out.log`, `bot.err.log`
  - `daily_report.out.log`, `daily_report.err.log`
  - `weekly_report.out.log`, `weekly_report.err.log`
  - `monthly_report.out.log`, `monthly_report.err.log`
  - `morning_plan.out.log`, `morning_plan.err.log`

---

## Project Config / Tooling

### `README.md`
- **Purpose**: Quick start instructions and high-level repo description.
- **Structure**:
  - Install steps
  - Run instructions
  - TODO section

### `pyproject.toml`
- **Purpose**: Python project config (deps, pytest, ruff, build).
- **Structure**:
  - `[project]`, `[project.optional-dependencies]`, `[tool.pytest...]`, `[tool.ruff]`

### `pyrightconfig.json`
- **Purpose**: Pyright (type checker) configuration.
- **Structure**:
  - Virtualenv config + missing imports setting.

### `package.json`
- **Purpose**: Node tooling config for scheduler + pm2 scripts.
- **Structure**:
  - npm scripts (`scheduler`, `pm2:*`)
  - dependencies (`node-cron`, `pm2`)

### `package-lock.json`
- **Purpose**: Locked Node dependency tree (generated).
- **Structure**:
  - Auto-generated lockfile.

### `ecosystem.config.js`
- **Purpose**: PM2 process definitions (bot + scheduled jobs).
- **Structure**:
  - `apps[]` entries with env and log paths.

### `TODO.md`
- **Purpose**: Human project todo backlog (not used by runtime).
- **Structure**:
  - Free-form checklist items.

