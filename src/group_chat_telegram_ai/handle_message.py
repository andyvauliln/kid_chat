from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx


# Model configuration with pricing (per 1M tokens)
@dataclass
class ModelConfig:
    id: str
    name: str
    input_price: float  # $ per 1M tokens
    output_price: float  # $ per 1M tokens
    context_size: int


AVAILABLE_MODELS: list[ModelConfig] = [
    ModelConfig("google/gemini-2.0-flash-001", "Gemini 2.0 Flash", 0.10, 0.40, 1_048_576),
    ModelConfig("google/gemini-2.0-flash-lite-001", "Gemini 2.0 Flash Lite", 0.075, 0.30, 1_048_576),
    ModelConfig("google/gemini-2.5-flash", "Gemini 2.5 Flash", 0.30, 2.50, 1_048_576),
    ModelConfig("google/gemini-2.5-flash-lite", "Gemini 2.5 Flash Lite", 0.10, 0.40, 1_048_576),
    ModelConfig("google/gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.0, 1_048_576),
    ModelConfig("google/gemini-2.5-pro-preview", "Gemini 2.5 Pro Preview", 1.25, 10.0, 1_048_576),
    ModelConfig("google/gemini-2.5-flash-preview-09-2025", "Gemini 2.5 Flash Preview", 0.30, 2.50, 1_048_576),
    ModelConfig("google/gemini-2.5-flash-lite-preview-09-2025", "Gemini 2.5 Flash Lite Preview", 0.10, 0.40, 1_048_576),
    ModelConfig("google/gemini-3-flash-preview", "Gemini 3 Flash Preview", 0.50, 3.0, 1_048_576),
    ModelConfig("google/gemini-3-pro-preview", "Gemini 3 Pro Preview", 2.0, 12.0, 1_048_576),
    ModelConfig("mistralai/voxtral-small-24b-2507", "Voxtral Small 24B", 0.10, 0.30, 32_000),
    ModelConfig("openai/gpt-4o-audio-preview", "GPT-4o Audio", 2.50, 10.0, 128_000),
]

# Default models for fallback
DEFAULT_MODELS = [
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash",
    "mistralai/voxtral-small-24b-2507",
]

ROUTER_PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "message_router.md"
LLM_LOG_PATH = Path(__file__).parent.parent.parent / "data" / "llm_logs.jsonl"
DAILY_REPORTS_DIR = Path(__file__).parent.parent.parent / "data" / "daily_reports"


def get_model_config(model_id: str) -> ModelConfig | None:
    """Get model config by ID."""
    for m in AVAILABLE_MODELS:
        if m.id == model_id:
            return m
    return None


def calculate_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Calculate cost in dollars for a request."""
    config = get_model_config(model_id)
    if not config:
        return 0.0
    input_cost = (input_tokens / 1_000_000) * config.input_price
    output_cost = (output_tokens / 1_000_000) * config.output_price
    return input_cost + output_cost


def _load_router_prompt() -> str:
    """Load router prompt with today's date."""
    text = ROUTER_PROMPT_PATH.read_text(encoding="utf-8")
    today = date.today().isoformat()
    return text.replace("YYYY-MM-DD", today)


def _safe_llm_log_input(user_content: list | str) -> object:
    if isinstance(user_content, str):
        return user_content
    safe: list[dict[str, Any]] = []
    for part in user_content:
        if isinstance(part, dict) and part.get("type") == "input_audio":
            safe.append(
                {
                    "type": "input_audio",
                    "input_audio": {
                        "format": part.get("input_audio", {}).get("format"),
                        "data": "<omitted>",
                    },
                }
            )
        else:
            safe.append(part)
    return safe


def _append_llm_log(
    *,
    model: str,
    input_data: object,
    output_data: object,
    cost: float,
    context_files: list[str] | None,
) -> None:
    try:
        LLM_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry = {
            "datetime": datetime.now(timezone.utc).isoformat(),
            "input": input_data,
            "output": output_data,
            "cost": cost,
            "llm": model,
            "context_files": context_files or [],
        }
        with LLM_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        # Best-effort logging (never break routing).
        return


def _daily_messages_path(report_date: date) -> Path:
    return DAILY_REPORTS_DIR / f"{report_date.isoformat()}.messages.md"


def _append_daily_report_line(report_date: date, line: str) -> None:
    DAILY_REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = _daily_messages_path(report_date)
    with report_path.open("a", encoding="utf-8") as f:
        f.write(line.rstrip() + "\n")


def _model_tag(model_id: str | None) -> str:
    m = (model_id or "").strip()
    return m if m else "unknown"


def _context_prompt() -> str:
    return (
        "You are an assistant. Answer the user's question using ONLY the provided context files.\n"
        "If the context is missing info, say what is missing and give the best practical suggestion.\n"
        "Output JSON only (no markdown): {\"response\": \"...\"}.\n"
    )


def _read_context_files(context_files: list[str]) -> str:
    root = Path(__file__).parent.parent.parent
    parts: list[str] = []
    for rel in context_files:
        path = root / rel
        try:
            content = path.read_text(encoding="utf-8")
        except Exception as e:
            content = f"(failed to read: {e})"
        parts.append(f"### {rel}\n{content}\n")
    return "\n".join(parts)


def _telegram_ogg_opus_to_wav_bytes(ogg_opus: bytes) -> bytes:
    """
    Telegram voice messages are OGG/OPUS. Some providers expect WAV/PCM.
    Convert via ffmpeg.
    """
    with tempfile.TemporaryDirectory() as d:
        in_path = Path(d) / "voice.ogg"
        out_path = Path(d) / "voice.wav"
        in_path.write_bytes(ogg_opus)
        subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-hide_banner",
                "-loglevel",
                "error",
                "-i",
                str(in_path),
                "-ac",
                "1",
                "-ar",
                "16000",
                str(out_path),
            ],
            check=True,
        )
        return out_path.read_bytes()


async def _download_telegram_voice(file_id: str, bot_token: str) -> bytes:
    """Download voice file from Telegram servers."""
    async with httpx.AsyncClient() as client:
        # Get file path
        resp = await client.get(
            f"https://api.telegram.org/bot{bot_token}/getFile",
            params={"file_id": file_id}
        )
        resp.raise_for_status()
        file_path = resp.json()["result"]["file_path"]
        
        # Download file
        file_resp = await client.get(
            f"https://api.telegram.org/file/bot{bot_token}/{file_path}"
        )
        file_resp.raise_for_status()
        return file_resp.content


@dataclass
class LLMResponse:
    """Response from LLM call with usage data."""
    content: str
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    raw_response: dict


async def _call_model(
    model: str,
    api_key: str,
    system: str,
    user_content: list | str,
    max_tokens: int = 1500,
) -> LLMResponse:
    """Call OpenRouter API and return response with usage data."""
    # Convert string to list format if needed
    if isinstance(user_content, str):
        user_content = [{"type": "text", "text": user_content}]
    
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user_content},
                ],
                "response_format": {"type": "json_object"},
                "temperature": 0,
                "max_tokens": max_tokens,
            }
        )
        resp.raise_for_status()
        data = resp.json()
        
        content = data["choices"][0]["message"]["content"].strip()
        usage = data.get("usage", {})
        input_tokens = usage.get("prompt_tokens", 0)
        output_tokens = usage.get("completion_tokens", 0)
        cost = calculate_cost(model, input_tokens, output_tokens)
        
        return LLMResponse(
            content=content,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost=cost,
            raw_response=data,
        )


@dataclass
class RouterResult:
    """Result from message routing."""
    output: dict[str, Any]  # Parsed JSON output from LLM
    model: str
    input_tokens: int
    output_tokens: int
    cost: float
    error: str | None = None


async def route_message(
    input_data: dict,
    model: str | None = None,
    api_key: str | None = None,
) -> RouterResult:
    """
    Route a message through the LLM router.
    
    Args:
        input_data: Dict with 'username' and 'message_raw' keys
        model: Model ID to use (or None for default with fallback)
        api_key: OpenRouter API key (or None to use env var)
    
    Returns:
        RouterResult with parsed output and usage data
    """
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")
    
    prompt = _load_router_prompt()
    user_content = json.dumps(input_data, ensure_ascii=False)
    
    models_to_try = [model] if model else DEFAULT_MODELS
    
    last_error = None
    for m in models_to_try:
        try:
            response = await _call_model(m, api_key, prompt, user_content)
            parsed = json.loads(response.content)

            _append_llm_log(
                model=response.model,
                input_data=input_data,
                output_data=parsed,
                cost=response.cost,
                context_files=[],
            )
            
            return RouterResult(
                output=parsed,
                model=response.model,
                input_tokens=response.input_tokens,
                output_tokens=response.output_tokens,
                cost=response.cost,
            )
        except Exception as e:
            last_error = e
            if model:  # If specific model requested, don't fallback
                break
            continue
    
    # All models failed
    return RouterResult(
        output={},
        model=models_to_try[0] if models_to_try else "",
        input_tokens=0,
        output_tokens=0,
        cost=0,
        error=str(last_error),
    )


async def handle_telegram_message(
    telegram_message: dict,
    model: str | None = None,
) -> RouterResult:
    """
    Process incoming Telegram message (text or voice).
    
    Args:
        telegram_message: Standard Telegram message object
        model: Model ID to use (or None for default with fallback)
    
    Returns:
        RouterResult with full router output
    """
    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")
    
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN")
    
    # Extract username
    from_user = telegram_message.get("from", {})
    username = from_user.get("username") or from_user.get("first_name") or ""
    
    # Extract message content
    text = telegram_message.get("text")
    voice = telegram_message.get("voice")
    answer_model: str | None = None
    
    # Text message - use route_message directly
    if text:
        result = await route_message(
            {"username": username, "message_raw": text},
            model=model,
            api_key=api_key,
        )
        out = result.output or {}
        out["llm_model"] = result.model
        message_en = out.get("message_en")
        if message_en:
            _append_daily_report_line(date.today(), f"[{username}] {message_en}")

        if out.get("needs_context") and out.get("context_files") and out.get("question_for_next_llm"):
            ctx = _read_context_files(list(out["context_files"]))
            user_payload = {
                "message_en": out.get("message_en"),
                "question": out.get("question_for_next_llm"),
                "context_files": out.get("context_files"),
                "context": ctx,
            }
            response2 = await _call_model(
                model=result.model or (model or DEFAULT_MODELS[0]),
                api_key=api_key,
                system=_context_prompt(),
                user_content=json.dumps(user_payload, ensure_ascii=False),
            )
            answer_model = response2.model
            parsed2 = json.loads(response2.content)
            out["response"] = parsed2.get("response")
            out["llm_model_context"] = response2.model
            result.output = out
            result.input_tokens += response2.input_tokens
            result.output_tokens += response2.output_tokens
            result.cost += response2.cost

            _append_llm_log(
                model=response2.model,
                input_data=user_payload,
                output_data=parsed2,
                cost=response2.cost,
                context_files=list(out["context_files"]),
            )

        if out.get("response"):
            _append_daily_report_line(
                date.today(),
                f"[AI|{_model_tag(answer_model or result.model)}] {out.get('response')}",
            )
        return result
    
    # Voice message - need to download and convert
    if voice:
        if not bot_token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN for voice messages")
        
        file_id = voice.get("file_id")
        audio_bytes = await _download_telegram_voice(file_id, bot_token)
        wav_bytes = _telegram_ogg_opus_to_wav_bytes(audio_bytes)
        audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
        
        prompt = _load_router_prompt()
        user_content = [
            {
                "type": "text",
                "text": json.dumps({"username": username}, ensure_ascii=False)
            },
            {
                "type": "input_audio",
                "input_audio": {
                    "data": audio_base64,
                    "format": "wav"
                }
            }
        ]
        
        models_to_try = [model] if model else DEFAULT_MODELS
        last_error = None
        
        for m in models_to_try:
            try:
                response = await _call_model(m, api_key, prompt, user_content)
                parsed = json.loads(response.content)

                _append_llm_log(
                    model=response.model,
                    input_data={"username": username, "voice": {"file_id": file_id}},
                    output_data=parsed,
                    cost=response.cost,
                    context_files=[],
                )

                parsed["llm_model"] = response.model
                message_en = parsed.get("message_en")
                if message_en:
                    _append_daily_report_line(date.today(), f"[{username}] {message_en}")

                if parsed.get("needs_context") and parsed.get("context_files") and parsed.get("question_for_next_llm"):
                    ctx = _read_context_files(list(parsed["context_files"]))
                    user_payload = {
                        "message_en": parsed.get("message_en"),
                        "question": parsed.get("question_for_next_llm"),
                        "context_files": parsed.get("context_files"),
                        "context": ctx,
                    }
                    response2 = await _call_model(
                        model=response.model,
                        api_key=api_key,
                        system=_context_prompt(),
                        user_content=json.dumps(user_payload, ensure_ascii=False),
                    )
                    answer_model = response2.model
                    parsed2 = json.loads(response2.content)
                    parsed["response"] = parsed2.get("response")
                    parsed["llm_model_context"] = response2.model

                    _append_llm_log(
                        model=response2.model,
                        input_data=user_payload,
                        output_data=parsed2,
                        cost=response2.cost,
                        context_files=list(parsed["context_files"]),
                    )

                    return RouterResult(
                        output=parsed,
                        model=response2.model,
                        input_tokens=response.input_tokens + response2.input_tokens,
                        output_tokens=response.output_tokens + response2.output_tokens,
                        cost=response.cost + response2.cost,
                    )

                if parsed.get("response"):
                    _append_daily_report_line(
                        date.today(),
                        f"[AI|{_model_tag(answer_model or response.model)}] {parsed.get('response')}",
                    )
                
                return RouterResult(
                    output=parsed,
                    model=response.model,
                    input_tokens=response.input_tokens,
                    output_tokens=response.output_tokens,
                    cost=response.cost,
                )
            except Exception as e:
                last_error = e
                if model:
                    break
                continue
        
        return RouterResult(
            output={},
            model=models_to_try[0] if models_to_try else "",
            input_tokens=0,
            output_tokens=0,
            cost=0,
            error=str(last_error),
        )
    
    # Unsupported message type
    return RouterResult(
        output={
            "message_en": "(unsupported message type)",
            "username": username,
            "needs_context": False,
            "context_files": [],
            "question_for_next_llm": None,
            "response": None,
        },
        model="",
        input_tokens=0,
        output_tokens=0,
        cost=0,
    )
