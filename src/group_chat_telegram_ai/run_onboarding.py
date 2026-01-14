from __future__ import annotations

import argparse
import json
import os

from dotenv import load_dotenv

from .onboarding_bot import build_application


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _check() -> None:
    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    print(json.dumps({"ok": True, "token_len": len(bot_token)}, ensure_ascii=False))


def main() -> None:
    load_dotenv()

    parser = argparse.ArgumentParser(description="Run onboarding bot")
    parser.add_argument("--check", action="store_true", help="Validate env and exit")
    args = parser.parse_args()

    if args.check:
        _check()
        return

    bot_token = _require_env("TELEGRAM_BOT_TOKEN")
    app = build_application(bot_token)
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
