import json
import os
import sys
import html
from pathlib import Path

# Ensure `src/` imports work when running as a script.
ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# Unbuffered output
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[attr-defined]

from dotenv import load_dotenv
from telegram import Update
from telegram.error import Conflict as TelegramConflict
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from group_chat_telegram_ai.handle_message import handle_telegram_message


load_dotenv()


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing: {name}")
    return value


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return
    # Avoid loops on bot messages
    if update.effective_user and getattr(update.effective_user, "is_bot", False):
        return
    try:
        result = await handle_telegram_message(update.message.to_dict())
        out = result.output or {}
        username = str(out.get("username") or "").strip()
        message_en = str(out.get("message_en") or "").strip()
        msg_type = str(out.get("type") or "other").strip()
        model_id = str(result.model or "").strip() or "unknown"

        user_id = None
        if update.effective_user:
            user_id = getattr(update.effective_user, "id", None)

        username_html = html.escape(username)
        message_html = html.escape(message_en)
        type_html = html.escape(msg_type)

        # Telegram does NOT support arbitrary text colors.
        # - Blue: achievable by making the name a link (Telegram renders links in blue).
        # - Green: emulate with a green square indicator.
        if user_id:
            name_part = f'<b><a href="tg://user?id={user_id}">{username_html}</a></b>'
        else:
            name_part = f"<b>{username_html}</b>"

        model_html = html.escape(model_id)
        formatted = f"{name_part}: {message_html} [{type_html} | {model_html}]"

        # Print only final output
        print(f"{username}: {message_en} [{msg_type} | {model_id}]")

        # Delete original message (requires permissions in groups)
        try:
            await update.message.delete()
        except Exception:
            pass

        # Post replacement message (not a reply)
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,  # type: ignore[arg-type]
                text=formatted,
                parse_mode="HTML",
                disable_web_page_preview=True,
            )
        except Exception:
            pass
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))


async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    err = context.error
    if isinstance(err, TelegramConflict):
        print("\nERROR: Telegram getUpdates conflict (another process is polling this bot token).")
        print("Fix: stop the other bot process OR use a different token via TELEGRAM_TEST_BOT_TOKEN.\n")
        try:
            if context.application.updater:
                await context.application.updater.stop()
        except Exception:
            pass
        try:
            await context.application.stop()
        except Exception:
            pass


def main() -> None:
    bot_token = os.environ.get("TELEGRAM_TEST_BOT_TOKEN") or _require_env("TELEGRAM_BOT_TOKEN")

    print("Starting bot... Send text or voice messages to test.")
    print("- If you see Conflict/getUpdates error: stop other bot OR set TELEGRAM_TEST_BOT_TOKEN")
    print("Press Ctrl+C to stop.\n")

    app = Application.builder().token(bot_token).build()

    # Auto-handle raw text/voice everywhere (non-commands)
    app.add_handler(MessageHandler((filters.TEXT | filters.VOICE) & ~filters.COMMAND, on_message))
    app.add_error_handler(on_error)

    try:
        app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)
    except Exception as e:
        msg = str(e)
        if "Conflict" in msg and "getUpdates" in msg:
            print("\nERROR: Telegram getUpdates conflict (another bot process is running).")
            print("Fix: stop the other bot process OR use a different token via TELEGRAM_TEST_BOT_TOKEN.\n")
        raise


if __name__ == "__main__":
    main()
