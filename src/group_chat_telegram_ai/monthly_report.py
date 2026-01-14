from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .handle_message import DEFAULT_MODELS, get_default_model_from_env, send_telegram_long_text


REPO_ROOT = Path(__file__).parent.parent.parent
DAILY_REPORTS_DIR = REPO_ROOT / "reports"


def _model_slug(model_id: str) -> str:
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


def _daily_summary_path_with_model(d: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.summary.{_model_slug(model_id)}.md"


def _monthly_summary_path_with_model(month_start: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{month_start:%Y-%m}.summary.{_model_slug(model_id)}.md"


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _month_start(d: date) -> date:
    return d.replace(day=1)


def _month_end(month_start: date) -> date:
    if month_start.month == 12:
        next_month = month_start.replace(year=month_start.year + 1, month=1, day=1)
    else:
        next_month = month_start.replace(month=month_start.month + 1, day=1)
    return next_month - timedelta(days=1)


def _find_daily_summary_path(d: date, model_id: str) -> Path | None:
    preferred = _daily_summary_path_with_model(d, model_id)
    if preferred.exists():
        return preferred
    matches = sorted(DAILY_REPORTS_DIR.glob(f"{d.isoformat()}.summary.*.md"))
    if not matches:
        return None
    return matches[-1]


def _strip_summary_heading(text: str) -> str:
    lines = [ln.rstrip() for ln in text.strip().splitlines()]
    if not lines:
        return ""
    if lines[0].strip().lower().startswith("## summary"):
        return "\n".join(lines[1:]).strip()
    return "\n".join(lines).strip()


def _build_period_report_text(title: str, daily_items: list[tuple[date, str]]) -> str:
    if not daily_items:
        return f"## {title}\n(no daily summaries)\n"
    parts = [f"## {title}"]
    for d, content in daily_items:
        body = _strip_summary_heading(content)
        if not body:
            body = "(no summary)"
        parts.append(f"### {d.isoformat()}\n{body}")
    return "\n\n".join(parts).strip() + "\n"


def _collect_daily_summaries(start: date, end: date, model_id: str) -> list[tuple[date, str]]:
    items: list[tuple[date, str]] = []
    cur = start
    while cur <= end:
        path = _find_daily_summary_path(cur, model_id)
        if path and path.exists():
            items.append((cur, path.read_text(encoding="utf-8")))
        cur += timedelta(days=1)
    return items


async def run_monthly_report(
    d: date,
    model: str | None = None,
    *,
    send: bool = True,
) -> dict[str, Any]:
    model_to_use = model or get_default_model_from_env(DEFAULT_MODELS)
    month_start = _month_start(d)
    month_end = _month_end(month_start)
    daily_items = _collect_daily_summaries(month_start, month_end, model_to_use)
    report_md = _build_period_report_text(
        f"Monthly Summary ({month_start:%Y-%m})",
        daily_items,
    )
    _write_text(_monthly_summary_path_with_model(month_start, model_to_use), report_md)

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
        text = f"MONTHLY REPORT {month_start:%Y-%m}\n\n{report_md.strip()}"
        await send_telegram_long_text(bot_token=bot_token, chat_id=chat_id, text=text)

    return {
        "date": d.isoformat(),
        "month_start": month_start.isoformat(),
        "month_end": month_end.isoformat(),
        "daily_items": len(daily_items),
        "monthly_summary_path": str(_monthly_summary_path_with_model(month_start, model_to_use).relative_to(REPO_ROOT)),
        "monthly_summary_text": report_md,
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

    parser = argparse.ArgumentParser(description="Run monthly report update job")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--model", default=None, help="OpenRouter model id (optional)")
    parser.add_argument("--no-send", action="store_true", help="Do not send to Telegram (default: send)")
    args = parser.parse_args()

    d = _parse_date(args.date)
    model = args.model

    import asyncio

    result = asyncio.run(run_monthly_report(d, model=model, send=not args.no_send))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
