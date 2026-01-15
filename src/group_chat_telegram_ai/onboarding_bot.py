from __future__ import annotations

import json
import os
import base64
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import Application, ContextTypes, MessageHandler, filters

from .handle_message import (
    DEFAULT_MODELS,
    _call_model,
    _context_prompt,
    _download_telegram_voice,
    _models_to_try,
    _read_context_files,
    _telegram_ogg_opus_to_wav_bytes,
    get_default_model_from_env,
    send_telegram_long_text,
)
from .update_engine import UpdateResult, run_update_for_file


REPO_ROOT = Path(__file__).parent.parent.parent
APP_PAGES_DIR = REPO_ROOT / "data" / "app_pages"
PROMPTS_DIR = REPO_ROOT / "prompts"
REPORTS_DIR = REPO_ROOT / "reports"
APPROVALS_PATH = REPO_ROOT / "data" / "onboarding_approvals.json"
ONBOARDING_ROUTER_PROMPT_PATH = REPO_ROOT / "prompts" / "onboarding_router.md"

USER_ORDER = ["JohnnyPitt", "katanyanyanya", "hirja"]
STOP_WORDS = {"done", "all approved"}


@dataclass
class RouterOutput:
    message_en: str
    intent: str
    needs_context: bool
    context_files: list[str]
    question_for_next_llm: str | None
    response: str | None
    model: str
    cost: float


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_username(name: str) -> str:
    return name.strip().lstrip("@").lower()


def _display_username(name: str) -> str:
    n = name.strip().lstrip("@")
    return f"@{n}" if n else ""


def _load_router_prompt() -> str:
    return ONBOARDING_ROUTER_PROMPT_PATH.read_text(encoding="utf-8")


def _load_approvals() -> list[dict[str, Any]]:
    if not APPROVALS_PATH.exists():
        return []
    try:
        return json.loads(APPROVALS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []


def _write_approvals(items: list[dict[str, Any]]) -> None:
    APPROVALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    APPROVALS_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _queue_files() -> list[str]:
    files: list[str] = []
    for p in sorted(APP_PAGES_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(REPORTS_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(REPORTS_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def _approved_set(items: list[dict[str, Any]], user: str) -> set[str]:
    u = _normalize_username(user)
    out: set[str] = set()
    for entry in items:
        if _normalize_username(entry.get("telegram_name", "")) != u:
            continue
        if entry.get("approved") is True:
            f = entry.get("file")
            if isinstance(f, str) and f:
                out.add(f)
    return out


def _current_user(items: list[dict[str, Any]], files: list[str]) -> str | None:
    for u in USER_ORDER:
        approved = _approved_set(items, u)
        if any(f not in approved for f in files):
            return u
    return None


def _pending_file_for_user(items: list[dict[str, Any]], user: str, files: list[str]) -> str | None:
    approved = _approved_set(items, user)
    for f in files:
        if f not in approved:
            return f
    return None


def _set_file_status(items: list[dict[str, Any]], user: str, file_path: str, approved: bool) -> None:
    user_key = _display_username(user)
    for entry in items:
        if entry.get("file") == file_path and _normalize_username(entry.get("telegram_name", "")) == _normalize_username(user):
            entry["approved"] = bool(approved)
            entry["updated_at"] = _now_iso()
            return
    items.append(
        {
            "file": file_path,
            "telegram_name": user_key,
            "approved": bool(approved),
            "updated_at": _now_iso(),
        }
    )


def _ensure_sent(items: list[dict[str, Any]], user: str, file_path: str) -> bool:
    for entry in items:
        if entry.get("file") == file_path and _normalize_username(entry.get("telegram_name", "")) == _normalize_username(user):
            return False
    _set_file_status(items, user, file_path, approved=False)
    return True


def _is_stop_word(text: str) -> bool:
    return text.strip().lower() in STOP_WORDS


def _history_map(context: ContextTypes.DEFAULT_TYPE) -> dict[str, list[str]]:
    return context.application.bot_data.setdefault("onboarding_history", {})


def _append_history(context: ContextTypes.DEFAULT_TYPE, user: str, message_en: str) -> None:
    key = _normalize_username(user)
    hist = _history_map(context).setdefault(key, [])
    if message_en.strip():
        hist.append(message_en.strip())


def _clear_history(context: ContextTypes.DEFAULT_TYPE, user: str) -> None:
    key = _normalize_username(user)
    _history_map(context).pop(key, None)


def _history_text(context: ContextTypes.DEFAULT_TYPE, user: str) -> str:
    key = _normalize_username(user)
    items = _history_map(context).get(key, [])
    return "\n".join(items).strip()


async def _send_text(update: Update, text: str) -> None:
    if not update.message:
        return
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not bot_token:
        raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
    await send_telegram_long_text(bot_token=bot_token, chat_id=update.message.chat_id, text=text)


async def _send_file_content(update: Update, file_path: str) -> None:
    await _send_text(update, _build_file_content_text(file_path))


def _build_file_content_text(file_path: str) -> str:
    abs_path = REPO_ROOT / file_path
    if not abs_path.exists():
        content = "(missing file)"
    else:
        content = abs_path.read_text(encoding="utf-8")
        if not content.strip():
            content = "(empty file)"
    header = f"FILE: {file_path}"
    return f"{header}\n\n{content}"


def _current_file_header(user: str, file_path: str) -> str:
    return f"Onboarding for {_display_username(user)}. Current file: {file_path}"


def _get_onboarding_chat_id() -> int | None:
    raw = os.environ.get("ONBOARDING_CHAT_ID") or os.environ.get("GROUP_ID")
    if not raw or not raw.strip():
        return None
    try:
        return int(raw)
    except Exception as e:
        raise RuntimeError(f"Invalid onboarding chat id: {raw!r}") from e


async def _send_file_content_to_chat(bot_token: str, chat_id: int, file_path: str) -> None:
    await send_telegram_long_text(
        bot_token=bot_token,
        chat_id=chat_id,
        text=_build_file_content_text(file_path),
    )


def _format_update_summary(result: UpdateResult) -> list[str]:
    summary: list[str] = []
    changes = result.log_entry.get("changes") or []
    for group in changes:
        ctype = group.get("type")
        items = group.get("data") or []
        for item in items:
            title = item.get("title") or ""
            text = item.get("text") or ""
            item_id = item.get("id")
            changes_text = ""
            if item.get("changes"):
                changes_text = f"changes={item.get('changes')}"
            reason = item.get("reasoning") or ""
            line = " | ".join(
                [p for p in [ctype, title, text, f"id={item_id}" if item_id is not None else "", changes_text] if p]
            )
            if reason:
                line = f"{line} (reason: {reason})"
            if line:
                summary.append(line)
        if summary:
            break
    return summary[:5]


def _plan_summary(items: list[dict[str, Any]], user: str, files: list[str]) -> str:
    approved = _approved_set(items, user)
    remaining = [f for f in files if f not in approved]
    if not remaining:
        return "Plan: all files approved."
    next_file = remaining[0]
    return f"Plan: {len(remaining)} files remaining. Next file: {next_file}"


async def _call_router_with_fallback(
    *,
    username: str,
    message_raw: str,
    messages_context: str,
    model: str | None = None,
    api_key: str | None = None,
) -> RouterOutput:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    payload = {
        "username": username,
        "message_raw": message_raw,
        "messages_context": messages_context,
    }
    prompt = _load_router_prompt()
    models_to_try = _models_to_try(model)
    last_error: Exception | None = None
    for m in models_to_try:
        try:
            response = await _call_model(
                model=m,
                api_key=api_key,
                system=prompt,
                user_content=json.dumps(payload, ensure_ascii=False),
            )
            data = json.loads(response.content)
            return RouterOutput(
                message_en=str(data.get("message_en") or "").strip(),
                intent=str(data.get("intent") or "").strip(),
                needs_context=bool(data.get("needs_context")),
                context_files=list(data.get("context_files") or []),
                question_for_next_llm=data.get("question_for_next_llm"),
                response=data.get("response"),
                model=response.model,
                cost=response.cost,
            )
        except Exception as e:
            last_error = e
            if model:
                break
    raise RuntimeError(f"Router failed: {last_error}")


async def _call_router_with_audio(
    *,
    username: str,
    ogg_opus: bytes,
    messages_context: str,
    model: str | None = None,
    api_key: str | None = None,
) -> RouterOutput:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    wav_bytes = _telegram_ogg_opus_to_wav_bytes(ogg_opus)
    audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
    payload = {
        "username": username,
        "message_raw": "",
        "messages_context": messages_context,
    }
    prompt = _load_router_prompt()
    models_to_try = _models_to_try(model)
    last_error: Exception | None = None
    for m in models_to_try:
        try:
            response = await _call_model(
                model=m,
                api_key=api_key,
                system=prompt,
                user_content=[
                    {"type": "text", "text": json.dumps(payload, ensure_ascii=False)},
                    {"type": "input_audio", "input_audio": {"data": audio_base64, "format": "wav"}},
                ],
            )
            data = json.loads(response.content)
            return RouterOutput(
                message_en=str(data.get("message_en") or "").strip(),
                intent=str(data.get("intent") or "").strip(),
                needs_context=bool(data.get("needs_context")),
                context_files=list(data.get("context_files") or []),
                question_for_next_llm=data.get("question_for_next_llm"),
                response=data.get("response"),
                model=response.model,
                cost=response.cost,
            )
        except Exception as e:
            last_error = e
            if model:
                break
    raise RuntimeError(f"Router failed: {last_error}")


async def _answer_with_context(router_out: RouterOutput) -> str | None:
    if not router_out.needs_context:
        return router_out.response
    if not router_out.context_files or not router_out.question_for_next_llm:
        return router_out.response

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    ctx = _read_context_files(list(router_out.context_files))
    payload = {
        "message_en": router_out.message_en,
        "question": router_out.question_for_next_llm,
        "context_files": router_out.context_files,
        "context": ctx,
    }
    response = await _call_model(
        model=router_out.model or get_default_model_from_env(DEFAULT_MODELS),
        api_key=api_key,
        system=_context_prompt(),
        user_content=json.dumps(payload, ensure_ascii=False),
    )
    parsed = json.loads(response.content)
    return parsed.get("response")


async def onboarding_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.message:
        return

    from_user = update.message.from_user or {}
    username = from_user.username or from_user.first_name or ""
    if not username:
        return

    approvals = _load_approvals()
    files = _queue_files()
    current_user = _current_user(approvals, files)

    if not current_user:
        await _send_text(update, "All files are approved for all users.")
        return

    if _normalize_username(username) != _normalize_username(current_user):
        await _send_text(update, f"Onboarding is currently for {_display_username(current_user)}.")
        return

    current_file = _pending_file_for_user(approvals, current_user, files)
    if current_file:
        _ensure_sent(approvals, current_user, current_file)
        _write_approvals(approvals)
        await _send_text(update, _current_file_header(current_user, current_file))
        await _send_file_content(update, current_file)

    text = (update.message.text or "").strip()
    voice = update.message.voice

    if text and _is_stop_word(text):
        if not current_file:
            await _send_text(update, "All files are approved for you.")
            _clear_history(context, current_user)
            return
        _set_file_status(approvals, current_user, current_file, approved=True)
        _write_approvals(approvals)
        _clear_history(context, current_user)

        next_file = _pending_file_for_user(approvals, current_user, files)
        if not next_file:
            await _send_text(update, f"All files approved for {_display_username(current_user)}.")
            return

        await _send_text(update, f"Approved by {_display_username(current_user)}: {current_file}")
        _ensure_sent(approvals, current_user, next_file)
        _write_approvals(approvals)
        await _send_text(update, _current_file_header(current_user, next_file))
        await _send_file_content(update, next_file)
        return

    router_out: RouterOutput | None = None
    if voice:
        bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
        if not bot_token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
        ogg = await _download_telegram_voice(voice.file_id, bot_token)
        router_out = await _call_router_with_audio(
            username=username,
            ogg_opus=ogg,
            messages_context=_history_text(context, current_user),
        )
    elif text:
        router_out = await _call_router_with_fallback(
            username=username,
            message_raw=text,
            messages_context=_history_text(context, current_user),
        )

    if not router_out:
        return

    if router_out.message_en:
        _append_history(context, current_user, router_out.message_en)

    answer = await _answer_with_context(router_out)
    update_result: UpdateResult | None = None
    if router_out.intent == "update" and current_file:
        update_result = await run_update_for_file(
            target_file=current_file,
            user_message=router_out.message_en or text,
        )

    parts: list[str] = []
    if answer:
        parts.append(f"Answer: {answer}")
    if update_result:
        summary_lines = _format_update_summary(update_result)
        if summary_lines:
            parts.append("Updated:")
            parts.extend([f"- {ln}" for ln in summary_lines])
        else:
            parts.append("Updated: file changes applied.")
    parts.append(_plan_summary(approvals, current_user, files))

    await _send_text(update, "\n".join(parts).strip())

    if update_result:
        await _send_file_content(update, update_result.update.file)


def build_application(bot_token: str) -> Application:
    app = Application.builder().token(bot_token).build()
    app.add_handler(MessageHandler(filters.TEXT | filters.VOICE, onboarding_message))

    chat_id = _get_onboarding_chat_id()
    if chat_id is not None:
        async def _announce(_: Application) -> None:
            approvals = _load_approvals()
            files = _queue_files()
            current_user = _current_user(approvals, files)
            if not current_user:
                await send_telegram_long_text(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text="All files are approved for all users.",
                )
                return
            current_file = _pending_file_for_user(approvals, current_user, files)
            if not current_file:
                await send_telegram_long_text(
                    bot_token=bot_token,
                    chat_id=chat_id,
                    text=f"All files approved for {_display_username(current_user)}.",
                )
                return
            await send_telegram_long_text(
                bot_token=bot_token,
                chat_id=chat_id,
                text=_current_file_header(current_user, current_file),
            )
            await _send_file_content_to_chat(bot_token, chat_id, current_file)

        app.post_init = _announce
    return app
