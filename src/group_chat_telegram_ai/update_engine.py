from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Literal

from . import daily_report as dr
from .handle_message import _append_llm_log, _call_model, get_default_model_from_env


REPO_ROOT = Path(__file__).parent.parent.parent
PROMPTS_DIR = REPO_ROOT / "prompts"
APP_PAGES_DIR = REPO_ROOT / "data" / "app_pages"
APP_JSON_DIR = REPO_ROOT / "data" / "app_json"
REPORTS_DIR = REPO_ROOT / "reports"

UPDATES_CONTEXT_PROMPT_PATH = PROMPTS_DIR / "daily_report_updates_context.md"
UPDATE_MD_APP_PAGE_PROMPT_PATH = PROMPTS_DIR / "update_md_app_page.md"
UPDATE_JSON_APP_DATA_PROMPT_PATH = PROMPTS_DIR / "update_json_app_data.md"
UPDATE_MD_FILE_PROMPT_PATH = PROMPTS_DIR / "update_md_file.md"

UPDATE_EDUCATION_MD_PROMPT_PATH = PROMPTS_DIR / "update__data_app_pages__Education.md"
UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__dante_topics_to_discuss.json.md"
UPDATE_TODO_LIST_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__todo_list.json.md"
UPDATE_VIDEO_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__video.json.md"


PromptKey = Literal["md_page", "json_app", "md_file"]


@dataclass
class UpdateResult:
    update: dr.FileUpdate
    log_entry: dict[str, Any]
    updated_content: str
    model: str
    cost: float


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _collect_tracked_files() -> list[str]:
    files: list[str] = []
    for p in sorted(APP_PAGES_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(APP_JSON_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(REPORTS_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(REPORTS_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def _collect_prompt_files() -> list[str]:
    return [str(p.relative_to(REPO_ROOT)) for p in sorted(PROMPTS_DIR.glob("*.md"))]


def _build_updates_context() -> dict[str, Any]:
    tracked_files = _collect_tracked_files()
    prompt_files = _collect_prompt_files()
    prompt_mapping = {
        "updates_context": str(UPDATES_CONTEXT_PROMPT_PATH.relative_to(REPO_ROOT)),
        "md_page": str(UPDATE_MD_APP_PAGE_PROMPT_PATH.relative_to(REPO_ROOT)),
        "json_app": str(UPDATE_JSON_APP_DATA_PROMPT_PATH.relative_to(REPO_ROOT)),
        "md_file": str(UPDATE_MD_FILE_PROMPT_PATH.relative_to(REPO_ROOT)),
        "file_prompts": {
            "data/app_pages/Education.md": str(UPDATE_EDUCATION_MD_PROMPT_PATH.relative_to(REPO_ROOT)),
            "data/app_json/dante_topics_to_discuss.json": str(
                UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH.relative_to(REPO_ROOT)
            ),
            "data/app_json/todo_list.json": str(UPDATE_TODO_LIST_JSON_PROMPT_PATH.relative_to(REPO_ROOT)),
            "data/app_json/video.json": str(UPDATE_VIDEO_JSON_PROMPT_PATH.relative_to(REPO_ROOT)),
        },
    }
    file_structures = {rel: dr._summarize_file_structure(rel) for rel in tracked_files}
    return {
        "tracked_files": tracked_files,
        "prompts": prompt_files,
        "prompt_mapping": prompt_mapping,
        "file_structures": file_structures,
    }


def _prompt_key_for_file(target_file: str) -> PromptKey:
    if target_file.endswith(".json"):
        return "json_app"
    if target_file.startswith("data/app_pages/"):
        return "md_page"
    return "md_file"


def _build_stage2_system_prompt(prompt_key: PromptKey, *, target_file: str) -> str:
    base = _load_prompt(UPDATES_CONTEXT_PROMPT_PATH).rstrip()
    file_prompts: dict[str, Path] = {
        "data/app_pages/Education.md": UPDATE_EDUCATION_MD_PROMPT_PATH,
        "data/app_json/dante_topics_to_discuss.json": UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH,
        "data/app_json/todo_list.json": UPDATE_TODO_LIST_JSON_PROMPT_PATH,
        "data/app_json/video.json": UPDATE_VIDEO_JSON_PROMPT_PATH,
    }
    p = file_prompts.get(target_file)
    if not p:
        if prompt_key == "md_page":
            p = UPDATE_MD_APP_PAGE_PROMPT_PATH
        elif prompt_key == "json_app":
            p = UPDATE_JSON_APP_DATA_PROMPT_PATH
        else:
            p = UPDATE_MD_FILE_PROMPT_PATH
    return f"{base}\n\n{_load_prompt(p).rstrip()}\n"


def _read_current_file_content(rel_path: str) -> str:
    abs_path = REPO_ROOT / rel_path
    if not abs_path.exists():
        return ""
    return abs_path.read_text(encoding="utf-8")


def _update_agent_system_prompt() -> str:
    return (
        "You are an update router. Choose which files to update based on the user message. "
        "Return JSON only: {\"message\": \"...\", \"files\": [\"path\", ...]}. "
        "Use only paths from tracked_files. If no files apply, return an empty list."
    )


def _parse_update_agent_output(payload: Any, *, fallback_message: str, tracked_files: set[str]) -> tuple[str, list[str]]:
    if not isinstance(payload, dict):
        return fallback_message, []
    message = str(payload.get("message") or fallback_message).strip()
    raw_files = payload.get("files") or []
    files: list[str] = []
    if isinstance(raw_files, list):
        for item in raw_files:
            if isinstance(item, str) and item in tracked_files:
                files.append(item)
    return message or fallback_message, files


async def run_update_for_file(
    *,
    target_file: str,
    user_message: str,
    updated_fields: list[str] | None = None,
    model: str | None = None,
    api_key: str | None = None,
) -> UpdateResult:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    model_to_use = model or get_default_model_from_env()
    prompt_key = _prompt_key_for_file(target_file)

    updates_context = _build_updates_context()
    system_prompt = _build_stage2_system_prompt(prompt_key, target_file=target_file)
    current_content = _read_current_file_content(target_file)

    payload = {
        "date": date.today().isoformat(),
        "daily_report_messages": user_message.strip(),
        "target_file": target_file,
        "current_content": current_content,
        "updated_fields": updated_fields or [],
        "reasoning": f'User requested updates: "{user_message.strip()}"',
        "updates_context": updates_context,
    }

    response = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=system_prompt,
        user_content=json.dumps(payload, ensure_ascii=False),
        max_tokens=3500,
    )

    parsed = json.loads(response.content)
    dr._validate_stage2_update_object(parsed)
    update = dr._parse_single_update(parsed)
    if update.file != target_file:
        raise ValueError(f"Update response file mismatch: {update.file} (expected {target_file})")

    log_entry = dr._apply_file_update_and_build_log_entry(
        date.today(),
        upd=update,
        model_id=response.model,
        cost=response.cost,
    )
    updated_content = _read_current_file_content(target_file)

    _append_llm_log(
        model=response.model,
        input_data=payload,
        output_data=parsed,
        cost=response.cost,
        context_files=[target_file],
    )

    return UpdateResult(
        update=update,
        log_entry=log_entry,
        updated_content=updated_content,
        model=response.model,
        cost=response.cost,
    )


async def run_update_agent(
    *,
    user_message: str,
    model: str | None = None,
    api_key: str | None = None,
) -> list[UpdateResult]:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    model_to_use = model or get_default_model_from_env()
    updates_context = _build_updates_context()
    tracked_files = list(updates_context.get("tracked_files") or [])
    payload = {
        "message": user_message.strip(),
        "tracked_files": tracked_files,
        "file_structures": updates_context.get("file_structures") or {},
    }
    response = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=_update_agent_system_prompt(),
        user_content=json.dumps(payload, ensure_ascii=False),
        max_tokens=1200,
    )
    parsed = json.loads(response.content)
    message, files = _parse_update_agent_output(
        parsed,
        fallback_message=user_message.strip(),
        tracked_files=set(tracked_files),
    )
    if not files:
        return []

    results: list[UpdateResult] = []
    for target_file in files:
        results.append(await run_update_for_file(target_file=target_file, user_message=message))
    return results
