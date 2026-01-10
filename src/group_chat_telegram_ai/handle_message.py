from __future__ import annotations

import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Literal, TypedDict

import httpx


MessageType = Literal["question", "report", "request", "no_need_response", "other"]


class FormattedMessage(TypedDict):
    message_en: str
    username: str
    type: MessageType


MODELS = [
    "google/gemini-2.0-flash-001",
    "google/gemini-2.5-flash",
    "mistralai/voxtral-small-24b-2507",
]

PROMPT_PATH = Path(__file__).parent.parent.parent / "prompts" / "handle_income_message.md"


def _load_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


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


async def _call_model(model: str, api_key: str, system: str, user_content: list) -> dict:
    """Call OpenRouter API with raw HTTP request."""
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
                "max_tokens": 500,
            }
        )
        resp.raise_for_status()
        return resp.json()


async def handle_telegram_message(telegram_message: dict) -> FormattedMessage:
    """
    Process incoming Telegram message (text or voice).
    Uses models with fallback - tries each model until one succeeds.
    
    Args:
        telegram_message: Standard Telegram message object
    
    Returns:
        FormattedMessage with English text, username, and message type
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
    
    prompt_text = _load_prompt()
    
    # Build user content
    if text:
        user_content = [
            {
                "type": "text",
                "text": json.dumps({"username": username, "message_raw": text}, ensure_ascii=False)
            }
        ]
    elif voice:
        if not bot_token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN for voice messages")
        
        file_id = voice.get("file_id")
        audio_bytes = await _download_telegram_voice(file_id, bot_token)
        wav_bytes = _telegram_ogg_opus_to_wav_bytes(audio_bytes)
        audio_base64 = base64.b64encode(wav_bytes).decode("utf-8")
        
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
    else:
        return {
            "message_en": "(unsupported message type)",
            "username": username,
            "type": "other",
        }
    
    # Try models with fallback
    last_error = None
    for model in MODELS:
        try:
            result = await _call_model(model, api_key, prompt_text, user_content)
            raw_out = result["choices"][0]["message"]["content"].strip()
            parsed = json.loads(raw_out)
            
            return {
                "message_en": str(parsed.get("message_en", "")).strip(),
                "username": username,
                "type": parsed.get("type", "other"),
            }
        except Exception as e:
            last_error = e
            continue
    
    # All models failed
    raise RuntimeError(f"All models failed. Last error: {last_error}")
