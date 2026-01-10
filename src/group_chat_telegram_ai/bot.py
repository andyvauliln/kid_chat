import json
import os

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


def _json_dump(obj: object) -> str:
    try:
        if hasattr(obj, "to_dict"):
            payload = obj.to_dict()  # type: ignore[attr-defined]
        else:
            payload = obj
        return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True, default=str)
    except Exception as e:  # pragma: no cover
        return json.dumps({"error": f"failed to json dump: {e}", "repr": repr(obj)}, ensure_ascii=False)


def _log_received(update: Update) -> None:
    if (_env("LOG_JSON") or "true").lower() in {"0", "false", "no", "off"}:
        return
    print("<<< RECEIVED UPDATE >>>")
    print(_json_dump(update))


def _log_sent(message: object) -> None:
    if (_env("LOG_JSON") or "true").lower() in {"0", "false", "no", "off"}:
        return
    print(">>> SENT MESSAGE >>>")
    print(_json_dump(message))


async def log_all_updates(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Runs for all message updates (and logs full JSON).
    _log_received(update)


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Group-friendly entrypoint: user sends `/ai <message>`, bot responds.
    """
    if not update.message:
        return
    text = update.message.text or ""
    sent = await update.message.reply_text(f"Echo: {text}")
    _log_sent(sent)

    # Test hook: if a test is waiting for an echo containing a nonce, resolve it.
    expected_nonce = context.application.bot_data.get("expected_nonce")
    if expected_nonce and expected_nonce in text:
        fut = context.application.bot_data.get("done_future")
        if hasattr(fut, "done") and hasattr(fut, "set_result") and not fut.done():  # type: ignore[attr-defined]
            fut.set_result(f"Echo: {text}")  # type: ignore[attr-defined]


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Health-check command. Works in groups with privacy mode.
    Usage: /ping <optional nonce>
    """
    if not update.message:
        return
    text = update.message.text or ""
    sent = await update.message.reply_text(f"pong {text}".strip())
    _log_sent(sent)

    # Test hook: resolve when /ping contains expected nonce.
    expected_nonce = context.application.bot_data.get("expected_nonce")
    if expected_nonce and expected_nonce in text:
        fut = context.application.bot_data.get("done_future")
        if hasattr(fut, "done") and hasattr(fut, "set_result") and not fut.done():  # type: ignore[attr-defined]
            fut.set_result(f"pong {text}".strip())  # type: ignore[attr-defined]


async def dm_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    In DMs, respond to any plain text (non-command).
    """
    if not update.message:
        return
    text = update.message.text or ""
    sent = await update.message.reply_text(f"Echo: {text}")
    _log_sent(sent)


def build_application(bot_token: str) -> Application:
    app = Application.builder().token(bot_token).build()
    # Log everything we receive (message updates).
    app.add_handler(MessageHandler(filters.ALL, log_all_updates), group=-100)
    # Group: only respond when invoked.
    app.add_handler(CommandHandler("ai", ai_command))
    app.add_handler(CommandHandler("ping", ping_command))
    # DMs: respond to regular text.
    app.add_handler(MessageHandler(filters.ChatType.PRIVATE & filters.TEXT & ~filters.COMMAND, dm_text))
    return app
 
 
def run_bot() -> None:
    load_dotenv()  # loads from .env if present (optional)
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    group_id = _env("GROUP_ID")
    announce = (_env("ANNOUNCE_ON_START") or "").lower() in {"1", "true", "yes", "y", "on"}
    app = build_application(bot_token)

    if group_id and announce:
        async def _announce(_: Application) -> None:
            sent = await app.bot.send_message(
                chat_id=int(group_id),
                text="✅ Assistant bot is online. Use /ai <msg>.",
            )
            _log_sent(sent)

        app.post_init = _announce

    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    run_bot()
