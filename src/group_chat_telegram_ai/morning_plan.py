from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .handle_message import (
    DEFAULT_MODELS,
    _append_llm_log,
    _call_model,
    get_default_model_from_env,
    send_telegram_long_text,
)


REPO_ROOT = Path(__file__).parent.parent.parent
DAILY_REPORTS_DIR = REPO_ROOT / "reports"
APP_PAGES_DIR = REPO_ROOT / "data" / "app_pages"
APP_JSON_DIR = REPO_ROOT / "data" / "app_json"
MORNING_PLAN_PROMPT_PATH = REPO_ROOT / "prompts" / "morning_plan.md"


def _load_morning_prompt() -> str:
    return MORNING_PLAN_PROMPT_PATH.read_text(encoding="utf-8")


def _model_slug(model_id: str) -> str:
    # Safe for filenames
    s = (model_id or "").strip()
    if not s:
        return "unknown"
    out: list[str] = []
    for ch in s:
        if ch.isalnum() or ch in {".", "-", "_"}:
            out.append(ch)
        else:
            out.append("_")
    return "".join(out)


def _read_text_if_exists(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def _collect_context_files() -> list[str]:
    files: list[str] = []
    for p in sorted(APP_PAGES_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(APP_JSON_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def _build_context_chunks(context_files: list[str]) -> str:
    chunks: list[str] = []
    for rel in context_files:
        abs_path = REPO_ROOT / rel
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            content = f"(failed to read: {e})"
        chunks.append(f"### {rel}\n{content}\n")
    return "\n".join(chunks)


def _daily_messages_path(d: date) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.messages.md"


def _daily_summary_paths(d: date) -> list[Path]:
    # Any model
    return sorted(DAILY_REPORTS_DIR.glob(f"{d.isoformat()}.summary.*.md"))


def _last_month_report_path(d: date, model_id: str) -> Path:
    prev_month_last_day = d.replace(day=1) - timedelta(days=1)
    month_key = prev_month_last_day.strftime("%Y-%m")
    return DAILY_REPORTS_DIR / f"{month_key}.month.{_model_slug(model_id)}.md"


def _morning_plan_path_with_model(d: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.morning_plan.{_model_slug(model_id)}.md"


def _validate_payload(payload: Any) -> str:
    if not isinstance(payload, dict):
        raise ValueError("Morning plan: LLM output must be a JSON object")
    msg = payload.get("morning_message")
    if not isinstance(msg, str) or not msg.strip():
        raise ValueError("Morning plan: missing/invalid 'morning_message' (must be non-empty string)")
    return msg


async def run_morning_plan(
    d: date,
    model: str | None = None,
    api_key: str | None = None,
    *,
    send: bool = True,
) -> dict[str, Any]:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    model_to_use = model or get_default_model_from_env(DEFAULT_MODELS)
    yesterday = d - timedelta(days=1)

    context_files = _collect_context_files()
    payload: dict[str, Any] = {
        "date": d.isoformat(),
        "yesterday_date": yesterday.isoformat(),
        "yesterday_messages": _read_text_if_exists(_daily_messages_path(yesterday)),
        "yesterday_summaries": [
            {"file": str(p.relative_to(REPO_ROOT)), "content": _read_text_if_exists(p)} for p in _daily_summary_paths(yesterday)
        ],
        "last_month_report": _read_text_if_exists(_last_month_report_path(d, model_to_use)),
        "context_files": context_files,
        "context": _build_context_chunks(context_files),
    }

    response = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=_load_morning_prompt(),
        user_content=json.dumps(payload, ensure_ascii=False),
        max_tokens=4000,
    )

    parsed = json.loads(response.content)
    morning_md = _validate_payload(parsed)

    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = _morning_plan_path_with_model(d, response.model)
    out_path.write_text(morning_md.strip() + "\n", encoding="utf-8")

    if send:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        group_id = os.environ.get("GROUP_ID")
        if not bot_token or not bot_token.strip():
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN (required for sending)")
        if not group_id or not group_id.strip():
            raise RuntimeError("Missing GROUP_ID (required for sending)")
        try:
            chat_id = int(group_id)
        except Exception as e:
            raise RuntimeError(f"Invalid GROUP_ID={group_id!r} (must be an integer chat id)") from e

        text = f"MORNING PLAN {d.isoformat()}\n\n{morning_md.strip()}"
        await send_telegram_long_text(bot_token=bot_token, chat_id=chat_id, text=text)

    _append_llm_log(
        model=response.model,
        input_data={
            "date": d.isoformat(),
            "yesterday_date": yesterday.isoformat(),
            "yesterday_messages": "<see file>",
            "yesterday_summaries": [str(p.relative_to(REPO_ROOT)) for p in _daily_summary_paths(yesterday)],
            "last_month_report": str(_last_month_report_path(d, model_to_use).relative_to(REPO_ROOT)),
            "context_files": context_files,
        },
        output_data=parsed,
        cost=response.cost,
        context_files=context_files,
    )

    return {
        "date": d.isoformat(),
        "model": response.model,
        "morning_plan_path": str(out_path.relative_to(REPO_ROOT)),
        "morning_message": morning_md,
        "sent": bool(send),
    }


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main() -> None:
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except Exception:
        pass

    parser = argparse.ArgumentParser(description="Generate morning plan message")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--model", default=None, help="OpenRouter model id (optional)")
    parser.add_argument("--no-send", action="store_true", help="Do not send to Telegram (default: send)")
    args = parser.parse_args()

    d = _parse_date(args.date)

    import asyncio

    result = asyncio.run(run_morning_plan(d, model=args.model, send=not args.no_send))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

