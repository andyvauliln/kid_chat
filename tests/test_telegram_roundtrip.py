import asyncio
import os
import re
import time
import uuid

import pytest

from dotenv import load_dotenv

from group_chat_telegram_ai.bot import build_application


def _env(name: str) -> str | None:
    value = os.environ.get(name)
    return value if value and value.strip() else None


@pytest.mark.asyncio
async def test_send_message_and_receive_bot_reply_roundtrip() -> None:
    """
    Integration test:
    - starts the assistant bot (Bot API polling) with /ai handler
    - sends a message into GROUP_ID (either automated via a second bot, or manual via a human)
    - asserts the assistant replied (we observe by capturing the reply sent by the assistant handler)

    Required env vars:
    - TELEGRAM_BOT_TOKEN
    - GROUP_ID

    Optional for fully automated test:
    - TELEGRAM_SENDER_BOT_TOKEN

    Manual mode (no sender bot):
    - set MANUAL_CONFIRM=1 and the test will prompt you in the group to send `/ai ping <nonce>`.
    """

    bot_token = _env("TELEGRAM_BOT_TOKEN")
    group_id = _env("GROUP_ID")
 
    required = {
        "TELEGRAM_BOT_TOKEN": bot_token,
        "GROUP_ID": group_id,
    }
    missing = [k for k, v in required.items() if not v]
    if missing:
        pytest.skip(f"Missing env vars for Telegram integration test: {', '.join(missing)}")
 
    # Start the real bot handlers (includes /ping).
    app = build_application(str(bot_token))
    updater = app.updater
    assert updater is not None, "Application.updater is None; cannot start polling"
    try:
        await app.initialize()
        # Make sure we can poll (not webhook).
        await app.bot.delete_webhook(drop_pending_updates=True)
        await app.start()
        await updater.start_polling(drop_pending_updates=True)

        nonce = uuid.uuid4().hex[:10]
        message_text = f"/ping {nonce} {int(time.time())}"

        done_future: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        app.bot_data["expected_nonce"] = nonce
        app.bot_data["done_future"] = done_future

        prompt = (
            "🧪 Test started.\n"
            "Please send this message in the group within 90 seconds:\n\n"
            f"{message_text}\n\n"
            "When I receive it, I'll reply with `pong ...` and the test will finish."
        )
        sent = await app.bot.send_message(chat_id=int(str(group_id)), text=prompt)
        print(f"Sent message: {sent.message_id}")
       

        reply_text = await asyncio.wait_for(done_future, timeout=90)
        ok = await app.bot.delete_message(chat_id=int(str(group_id)), message_id=sent.message_id)
        print(f"Deleted message: {ok}")
        assert re.search("pong", reply_text), f"Expected pong in reply, got: {reply_text!r}"
    finally:
        # Stop assistant
        try:
            await updater.stop()
            await app.stop()
            await app.shutdown()
        except Exception:
            pass


def _run_as_script() -> None:
    load_dotenv()
    try:
        print("Starting Telegram test: will prompt group and wait for /ping ...")
        asyncio.run(test_send_message_and_receive_bot_reply_roundtrip())
        print("ALL GOOD ✅")
    except pytest.skip.Exception as e:  # type: ignore[attr-defined]
        print(f"SKIPPED: {e.msg}")
        raise SystemExit(0) from e


if __name__ == "__main__":
    _run_as_script()
