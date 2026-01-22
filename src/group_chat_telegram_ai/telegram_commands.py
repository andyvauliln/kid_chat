from __future__ import annotations

import json
import os
import re
from datetime import date, datetime
from pathlib import Path
from typing import Iterable

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from .agent_command import (
    build_agent_handlers,
    build_agent_reply_handler,
    build_agent_test_reply_handler,
)
from .daily_report import run_daily_report
from .handle_message import DEFAULT_MODELS, _call_model, get_default_model_from_env, send_telegram_long_text
from .pending_updates import approve_pending_update, list_pending_updates, reject_pending_update
from .update_engine import run_update_agent

REPO_ROOT = Path(__file__).parent.parent.parent
REPORTS_DIR = REPO_ROOT / "reports"

_DAILY_SUMMARY_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.summary\..+\.md$")
_WEEKLY_SUMMARY_RE = re.compile(r"\d{4}-W\d{2}\.summary\..+\.md$")
_MONTHLY_SUMMARY_RE = re.compile(r"\d{4}-\d{2}\.summary\..+\.md$")
_DAILY_UPDATES_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.updates\..+\.json$")
_MORNING_PLAN_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.morning_plan\..+\.md$")
_DAILY_MESSAGES_RE = re.compile(r"\d{4}-\d{2}-\d{2}\.messages\.md$")


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value or not value.strip():
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


async def _send_text(update: Update, text: str) -> None:
    if not update.message:
        return
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    await send_telegram_long_text(bot_token=bot_token, chat_id=update.message.chat_id, text=text)


def _read_text(path: Path) -> str:
    if not path.exists():
        return "(missing file)"
    return path.read_text(encoding="utf-8")


def _latest_path(paths: Iterable[Path]) -> Path | None:
    items = [p for p in paths if p.exists()]
    if not items:
        return None
    return max(items, key=lambda p: p.stat().st_mtime)


def _latest_report_path(regex: re.Pattern[str]) -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    matches = [p for p in REPORTS_DIR.iterdir() if regex.search(p.name)]
    return _latest_path(matches)


def _find_daily_summary(date_value: date) -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    matches = sorted(REPORTS_DIR.glob(f"{date_value.isoformat()}.summary.*.md"))
    return _latest_path(matches)


def _find_morning_plan(date_value: date) -> Path | None:
    if not REPORTS_DIR.exists():
        return None
    matches = sorted(REPORTS_DIR.glob(f"{date_value.isoformat()}.morning_plan.*.md"))
    return _latest_path(matches)


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _last_message_line() -> str:
    messages_path = _latest_report_path(_DAILY_MESSAGES_RE)
    if not messages_path:
        return ""
    lines = [ln.strip() for ln in messages_path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    return lines[-1] if lines else ""


def _strip_command(text: str, command: str) -> str:
    if not text:
        return ""
    pattern = rf"^/{re.escape(command)}(?:@\w+)?\s*"
    return re.sub(pattern, "", text.strip(), count=1).strip()


async def make_dayly_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = date.today()
    result = await run_daily_report(d, send=False)
    report_md = (result.get("daily_summary_text") or "").strip()
    text = report_md or "(no summary)"
    await _send_text(update, f"DAILY REPORT {d.isoformat()}\n\n{text}")


async def translate_last_message_to_ind_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    last_line = _last_message_line()
    if not last_line:
        await _send_text(update, "No messages found to translate.")
        return
    api_key = _require_env("OPENROUTER_API_KEY")
    model_to_use = get_default_model_from_env(DEFAULT_MODELS)
    response = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=(
            "Translate the input into Indonesian. "
            "Return JSON only: {\"text\": \"...\"}."
        ),
        user_content=json.dumps({"text": last_line}, ensure_ascii=False),
        max_tokens=1200,
    )
    parsed = json.loads(response.content)
    translated = str(parsed.get("text") or "").strip()
    if not translated:
        translated = "(empty translation)"
    await _send_text(update, f"Original:\n{last_line}\n\nIndonesian:\n{translated}")


async def show_last_updates_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    updates_path = _latest_report_path(_DAILY_UPDATES_RE)
    if not updates_path:
        await _send_text(update, "No updates report found.")
        return
    try:
        payload = json.loads(updates_path.read_text(encoding="utf-8"))
        text = json.dumps(payload, ensure_ascii=False, indent=2)
    except Exception:
        text = _read_text(updates_path)
    await _send_text(update, f"UPDATES FILE: {updates_path.name}\n\n{text}")


async def show_last_day_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    report_path = _latest_report_path(_DAILY_SUMMARY_RE)
    if not report_path:
        await _send_text(update, "No daily report found.")
        return
    await _send_text(update, _read_text(report_path))


async def show_last_week_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    report_path = _latest_report_path(_WEEKLY_SUMMARY_RE)
    if not report_path:
        await _send_text(update, "No weekly report found.")
        return
    await _send_text(update, _read_text(report_path))


async def show_last_month_report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    report_path = _latest_report_path(_MONTHLY_SUMMARY_RE)
    if not report_path:
        await _send_text(update, "No monthly report found.")
        return
    await _send_text(update, _read_text(report_path))


async def show_report_on_date_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send_text(update, "Usage: /show_report_on_date YYYY-MM-DD")
        return
    try:
        d = _parse_date(context.args[0])
    except Exception:
        await _send_text(update, "Invalid date. Use YYYY-MM-DD.")
        return
    report_path = _find_daily_summary(d)
    if not report_path:
        await _send_text(update, f"No daily report found for {d.isoformat()}.")
        return
    await _send_text(update, _read_text(report_path))


async def show_last_morning_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    plan_path = _latest_report_path(_MORNING_PLAN_RE)
    if not plan_path:
        await _send_text(update, "No morning plan found.")
        return
    await _send_text(update, _read_text(plan_path))


async def show_morning_plan_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    d = date.today()
    plan_path = _find_morning_plan(d)
    if not plan_path:
        await _send_text(update, f"No morning plan found for {d.isoformat()}.")
        return
    await _send_text(update, _read_text(plan_path))


async def udpate_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    message = _strip_command(update.message.text or "", "udpate")
    if not message:
        message = _strip_command(update.message.text or "", "update")
    if not message:
        await _send_text(update, "Usage: /udpate {message}")
        return
    try:
        updates = await run_update_agent(user_message=message)
    except Exception as e:
        await _send_text(update, f"Update failed: {e}")
        return
    if not updates:
        await _send_text(update, "No files selected for update.")
        return
    lines: list[str] = []
    for u in updates:
        if u.pending_update_id:
            lines.append(f"queued (Not Approved): {u.update.file} id={u.pending_update_id}")
        else:
            lines.append(f"updated: {u.update.file}")
    await _send_text(update, "\n".join(lines).strip())


async def pending_updates_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = list_pending_updates(status="not_approved")
    if not items:
        await _send_text(update, "No pending updates.")
        return
    lines: list[str] = ["Pending updates (Not Approved):"]
    for it in items[:30]:
        lines.append(
            f"- id={it.get('id')} file={it.get('file')} source={it.get('source')} created_at={it.get('created_at')}"
        )
    if len(items) > 30:
        lines.append(f"... and {len(items) - 30} more")
    await _send_text(update, "\n".join(lines).strip())


async def approve_update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send_text(update, "Usage: /approve_update <id>")
        return
    update_id = str(context.args[0]).strip()
    res = approve_pending_update(update_id)
    if not res:
        await _send_text(update, f"Not found or not approvable: {update_id}")
        return
    await _send_text(update, f"Approved + applied: {res.entry.get('file')} id={update_id}")


async def approve_all_updates_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    items = list_pending_updates(status="not_approved")
    if not items:
        await _send_text(update, "No pending updates.")
        return
    applied: list[str] = []
    failed: list[str] = []
    for it in items:
        uid = str(it.get("id") or "")
        if not uid:
            continue
        res = approve_pending_update(uid)
        if res:
            applied.append(f"{res.entry.get('file')} id={uid}")
        else:
            failed.append(uid)
    lines: list[str] = []
    if applied:
        lines.append("Approved + applied:")
        lines.extend([f"- {x}" for x in applied[:30]])
        if len(applied) > 30:
            lines.append(f"... and {len(applied) - 30} more")
    if failed:
        lines.append("Failed:")
        lines.extend([f"- id={x}" for x in failed[:30]])
        if len(failed) > 30:
            lines.append(f"... and {len(failed) - 30} more")
    await _send_text(update, "\n".join(lines).strip() or "(no changes)")


async def reject_update_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await _send_text(update, "Usage: /reject_update <id>")
        return
    update_id = str(context.args[0]).strip()
    res = reject_pending_update(update_id)
    if not res:
        await _send_text(update, f"Not found: {update_id}")
        return
    await _send_text(update, f"Rejected: {res.get('file')} id={update_id}")


async def delete_messages_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    chat_id = update.message.chat_id
    start_message_id = update.message.message_id
    delete_all = True
    delete_count: int | None = None

    if context.args:
        raw = str(context.args[0]).strip()
        if raw.lower() != "all":
            try:
                delete_count = int(raw)
            except ValueError:
                delete_count = None
            if not delete_count or delete_count < 1:
                await _send_text(update, "Usage: /delete_messages <number|all>")
                return
            delete_all = False

    deleted = 0
    failed = 0
    limit = delete_count if delete_count is not None else start_message_id

    for i in range(limit):
        message_id = start_message_id - i
        if message_id <= 0:
            break
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
            deleted += 1
        except Exception:
            failed += 1
            if delete_all:
                break

    if delete_all:
        await _send_text(update, f"Deleted {deleted} messages. Failed: {failed}.")
    else:
        await _send_text(update, f"Deleted {deleted}/{delete_count} messages. Failed: {failed}.")


def build_command_handlers() -> list[CommandHandler]:
    handlers = [
        CommandHandler("make_dayly_report", make_dayly_report_command),
        CommandHandler("translate_last_message_to_ind", translate_last_message_to_ind_command),
        CommandHandler("show_last_udpates", show_last_updates_command),
        CommandHandler("show_last_updates", show_last_updates_command),
        CommandHandler("show_last_day_report", show_last_day_report_command),
        CommandHandler("show_last_week_report", show_last_week_report_command),
        CommandHandler("show_last_month_report", show_last_month_report_command),
        CommandHandler("show_report_on_date", show_report_on_date_command),
        CommandHandler("show_last_morning_plan", show_last_morning_plan_command),
        CommandHandler("show_morning_plan", show_morning_plan_command),
        CommandHandler("udpate", udpate_command),
        CommandHandler("update", udpate_command),
        CommandHandler("pending_updates", pending_updates_command),
        CommandHandler("approve_update", approve_update_command),
        CommandHandler("approve_all_updates", approve_all_updates_command),
        CommandHandler("reject_update", reject_update_command),
        CommandHandler("delete_messages", delete_messages_command),
    ]
    # Add agent command handlers
    handlers.extend(build_agent_handlers())
    return handlers


def register_command_handlers(app: Application) -> None:
    for handler in build_command_handlers():
        app.add_handler(handler)
    # Add agent reply handler (for session continuity)
    app.add_handler(build_agent_test_reply_handler())
    app.add_handler(build_agent_reply_handler())