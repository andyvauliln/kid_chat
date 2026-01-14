from __future__ import annotations

import argparse
import difflib
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

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
PROMPTS_DIR = REPO_ROOT / "prompts"
DAILY_REPORT_PROMPT_PATH = PROMPTS_DIR / "daily_report.md"
DAILY_REPORT_STAGE1_PROMPT_PATH = PROMPTS_DIR / "daily_report_stage1_plan.md"
DAILY_REPORT_UPDATES_CONTEXT_PROMPT_PATH = PROMPTS_DIR / "daily_report_updates_context.md"
UPDATE_MD_APP_PAGE_PROMPT_PATH = PROMPTS_DIR / "update_md_app_page.md"
UPDATE_JSON_APP_DATA_PROMPT_PATH = PROMPTS_DIR / "update_json_app_data.md"
UPDATE_EDUCATION_MD_PROMPT_PATH = PROMPTS_DIR / "update__data_app_pages__Education.md"
UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__dante_topics_to_discuss.json.md"
UPDATE_TODO_LIST_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__todo_list.json.md"
UPDATE_VIDEO_JSON_PROMPT_PATH = PROMPTS_DIR / "update__data_app_json__video.json.md"


ChangeType = Literal["added", "removed", "updated"]
FileFormat = Literal["md", "json"]


@dataclass
class FileChange:
    type: ChangeType
    data: Any | None = None
    full_document: str | None = None


@dataclass
class FileUpdate:
    file: str
    format: FileFormat
    changes: list[FileChange]
    reasoning: str | None = None
    updated_fields: list[str] | None = None


PromptKey = Literal["md_page", "json_app"]


@dataclass
class UpdatePlanItem:
    file: str
    format: FileFormat
    reasoning: str
    updated_fields: list[str]
    prompt_key: PromptKey


def _load_daily_prompt() -> str:
    return DAILY_REPORT_PROMPT_PATH.read_text(encoding="utf-8")


def _load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _load_updates_context_prompt() -> str:
    return _load_prompt(DAILY_REPORT_UPDATES_CONTEXT_PROMPT_PATH)


def _load_stage1_prompt() -> str:
    return _load_prompt(DAILY_REPORT_STAGE1_PROMPT_PATH)


def _build_stage1_system_prompt() -> str:
    # Combine context-guidance + stage1 instructions.
    return f"{_load_updates_context_prompt().rstrip()}\n\n{_load_stage1_prompt().rstrip()}\n"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _daily_messages_path(d: date) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.messages.md"


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


def _daily_summary_path_with_model(d: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.summary.{_model_slug(model_id)}.md"


def _daily_updates_path_with_model(d: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.updates.{_model_slug(model_id)}.json"


def _collect_context_files() -> list[str]:
    files: list[str] = []
    for p in sorted(APP_PAGES_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(APP_JSON_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def _collect_prompt_files() -> list[str]:
    files: list[str] = []
    for p in sorted(PROMPTS_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


def _summarize_md_structure(content: str) -> dict[str, Any]:
    lines = content.splitlines()
    headings: list[dict[str, Any]] = []
    description = ""
    for ln in lines:
        s = ln.strip()
        if not s:
            continue
        if s.startswith("#"):
            level = len(s) - len(s.lstrip("#"))
            title = s.lstrip("#").strip()
            if title:
                headings.append({"level": level, "title": title})
            continue
        if not description:
            # First non-heading, non-empty line as a best-effort description.
            description = s[:200]
    return {
        "type": "md",
        "headings": headings[:80],
        "description": description,
    }


def _summarize_json_structure(obj: Any) -> dict[str, Any]:
    if isinstance(obj, list):
        sample_keys: list[str] = []
        for item in obj:
            if isinstance(item, dict):
                sample_keys = sorted(item.keys())
                break
        return {
            "type": "json",
            "shape": "list",
            "sample_item_keys": sample_keys[:80],
        }

    if isinstance(obj, dict):
        top_keys = sorted(obj.keys())
        container_key = None
        sample_keys: list[str] = []
        for k in ("items", "topics"):
            v = obj.get(k)
            if isinstance(v, list):
                container_key = k
                for item in v:
                    if isinstance(item, dict):
                        sample_keys = sorted(item.keys())
                        break
                break
        return {
            "type": "json",
            "shape": "dict",
            "top_level_keys": top_keys[:200],
            "list_container_key": container_key,
            "sample_item_keys": sample_keys[:80],
        }

    return {"type": "json", "shape": type(obj).__name__}


def _summarize_file_structure(rel_path: str) -> dict[str, Any]:
    abs_path = REPO_ROOT / rel_path
    try:
        raw = abs_path.read_text(encoding="utf-8")
    except Exception as e:
        return {"error": f"failed to read: {e}"}

    if rel_path.endswith(".md"):
        return _summarize_md_structure(raw)

    if rel_path.endswith(".json"):
        try:
            obj = json.loads(raw)
        except Exception as e:
            return {"type": "json", "error": f"failed to parse json: {e}"}
        return _summarize_json_structure(obj)

    return {"error": "unsupported file type"}


def _build_updates_context() -> dict[str, Any]:
    tracked_files = _collect_context_files()
    prompt_files = _collect_prompt_files()
    prompt_mapping = {
        "stage1_plan": str(DAILY_REPORT_STAGE1_PROMPT_PATH.relative_to(REPO_ROOT)),
        "updates_context": str(DAILY_REPORT_UPDATES_CONTEXT_PROMPT_PATH.relative_to(REPO_ROOT)),
        "md_page": str(UPDATE_MD_APP_PAGE_PROMPT_PATH.relative_to(REPO_ROOT)),
        "json_app": str(UPDATE_JSON_APP_DATA_PROMPT_PATH.relative_to(REPO_ROOT)),
        "file_prompts": {
            "data/app_pages/Education.md": str(UPDATE_EDUCATION_MD_PROMPT_PATH.relative_to(REPO_ROOT)),
            "data/app_json/dante_topics_to_discuss.json": str(UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH.relative_to(REPO_ROOT)),
            "data/app_json/todo_list.json": str(UPDATE_TODO_LIST_JSON_PROMPT_PATH.relative_to(REPO_ROOT)),
            "data/app_json/video.json": str(UPDATE_VIDEO_JSON_PROMPT_PATH.relative_to(REPO_ROOT)),
        },
    }
    file_structures = {rel: _summarize_file_structure(rel) for rel in tracked_files}
    return {
        "tracked_files": tracked_files,
        "prompts": prompt_files,
        "prompt_mapping": prompt_mapping,
        "file_structures": file_structures,
    }


def _build_context_payload(d: date) -> dict[str, Any]:
    messages_path = _daily_messages_path(d)
    report_text = messages_path.read_text(encoding="utf-8") if messages_path.exists() else ""

    context_files = _collect_context_files()
    context_chunks: list[str] = []
    for rel in context_files:
        abs_path = REPO_ROOT / rel
        try:
            content = abs_path.read_text(encoding="utf-8")
        except Exception as e:
            content = f"(failed to read: {e})"
        context_chunks.append(f"### {rel}\n{content}\n")

    return {
        "date": d.isoformat(),
        "daily_report_messages": report_text,
        "context_files": context_files,
        "context": "\n".join(context_chunks),
        "updates_context": _build_updates_context(),
    }


def _build_stage1_payload(d: date) -> dict[str, Any]:
    messages_path = _daily_messages_path(d)
    report_text = messages_path.read_text(encoding="utf-8") if messages_path.exists() else ""
    return {
        "date": d.isoformat(),
        "daily_report_messages": report_text,
        "updates_context": _build_updates_context(),
    }


def _validate_stage1_payload(payload: Any, *, tracked_files: list[str]) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Daily report stage1: LLM output must be a JSON object")

    summary = payload.get("summary")
    update_plan = payload.get("update_plan")
    if not isinstance(summary, str):
        raise ValueError("Daily report stage1: 'summary' must be a string")
    if not isinstance(update_plan, list):
        raise ValueError("Daily report stage1: 'update_plan' must be a list")

    for idx, u in enumerate(update_plan):
        if not isinstance(u, dict):
            raise ValueError(f"Daily report stage1: update_plan[{idx}] must be an object")

        file = u.get("file")
        fmt = u.get("format")
        reasoning = u.get("reasoning")
        updated_fields = u.get("updated_fields")
        prompt_key = u.get("prompt_key")

        if not isinstance(file, str) or not file.strip():
            raise ValueError(f"Daily report stage1: update_plan[{idx}].file must be a non-empty string")
        if file not in tracked_files:
            raise ValueError(f"Daily report stage1: update_plan[{idx}].file must be a tracked file: {file}")
        if fmt not in ("md", "json"):
            raise ValueError(f"Daily report stage1: update_plan[{idx}].format must be 'md' or 'json'")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError(f"Daily report stage1: update_plan[{idx}].reasoning must be a non-empty string")
        if not isinstance(updated_fields, list):
            raise ValueError(f"Daily report stage1: update_plan[{idx}].updated_fields must be a list")
        if prompt_key not in ("md_page", "json_app"):
            raise ValueError(f"Daily report stage1: update_plan[{idx}].prompt_key invalid")


def _parse_update_plan(payload: dict[str, Any]) -> list[UpdatePlanItem]:
    raw = payload.get("update_plan") or []
    out: list[UpdatePlanItem] = []
    for u in raw:
        out.append(
            UpdatePlanItem(
                file=str(u.get("file") or ""),
                format=u.get("format"),
                reasoning=str(u.get("reasoning") or ""),
                updated_fields=list(u.get("updated_fields") or []),
                prompt_key=u.get("prompt_key"),
            )
        )
    return out


def _parse_updates(payload: dict[str, Any]) -> list[FileUpdate]:
    updates_raw = payload.get("updates") or []
    updates: list[FileUpdate] = []
    for u in updates_raw:
        changes: list[FileChange] = []
        for c in (u.get("changes") or []):
            changes.append(
                FileChange(
                    type=c.get("type"),
                    data=c.get("data"),
                    full_document=c.get("full_document"),
                )
            )
        updates.append(
            FileUpdate(
                file=u.get("file"),
                format=u.get("format"),
                reasoning=u.get("reasoning"),
                updated_fields=list(u.get("updated_fields") or []),
                changes=changes,
            )
        )
    return updates


def _validate_daily_report_payload(payload: Any) -> None:
    if not isinstance(payload, dict):
        raise ValueError("Daily report: LLM output must be a JSON object")

    updates = payload.get("updates") or []
    if not isinstance(updates, list):
        raise ValueError("Daily report: 'updates' must be a list")

    for idx, u in enumerate(updates):
        if not isinstance(u, dict):
            raise ValueError(f"Daily report: updates[{idx}] must be an object")

        file = u.get("file")
        fmt = u.get("format")
        reasoning = u.get("reasoning")
        updated_fields = u.get("updated_fields")
        changes = u.get("changes")

        if not isinstance(file, str) or not file.strip():
            raise ValueError(f"Daily report: updates[{idx}].file must be a non-empty string")
        if fmt not in ("md", "json"):
            raise ValueError(f"Daily report: updates[{idx}].format must be 'md' or 'json'")
        if not isinstance(reasoning, str) or not reasoning.strip():
            raise ValueError(f"Daily report: updates[{idx}].reasoning must be a non-empty string")
        if not isinstance(updated_fields, list):
            raise ValueError(f"Daily report: updates[{idx}].updated_fields must be a list")
        if not isinstance(changes, list):
            raise ValueError(f"Daily report: updates[{idx}].changes must be a list")

        for c_idx, c in enumerate(changes):
            if not isinstance(c, dict):
                raise ValueError(f"Daily report: updates[{idx}].changes[{c_idx}] must be an object")
            if c.get("type") not in ("added", "removed", "updated"):
                raise ValueError(f"Daily report: updates[{idx}].changes[{c_idx}].type invalid")

        if fmt == "md":
            if len(changes) != 1:
                raise ValueError(f"Daily report: md updates must include exactly one change (updates[{idx}])")
            c0 = changes[0]
            if c0.get("type") != "updated":
                raise ValueError(f"Daily report: md change must be type='updated' (updates[{idx}])")
            full_doc = c0.get("full_document")
            if not isinstance(full_doc, str) or not full_doc.strip():
                raise ValueError(f"Daily report: md update missing full_document (updates[{idx}])")
        else:
            # json: should not include full_document payloads
            for c_idx, c in enumerate(changes):
                if c.get("full_document") not in (None, ""):
                    raise ValueError(
                        f"Daily report: json change must not include full_document (updates[{idx}].changes[{c_idx}])"
                    )


def _find_target_list(root: Any) -> tuple[Any, list]:
    if isinstance(root, list):
        return root, root
    if isinstance(root, dict):
        for key in ("items", "topics"):
            val = root.get(key)
            if isinstance(val, list):
                return root, val
    raise ValueError("Unsupported JSON shape (expected list or dict with items/topics list)")


def _apply_json_added(target_list: list, data: Any) -> list[str]:
    applied: list[str] = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if item_id is None:
            # Best-effort for list-based schemas like video.json
            existing_ids = [x.get("id") for x in target_list if isinstance(x, dict) and "id" in x]
            numeric_ids = [x for x in existing_ids if isinstance(x, int)]
            next_id = (max(numeric_ids) + 1) if numeric_ids else 1
            item = dict(item)
            item["id"] = next_id
            item_id = next_id
        for existing in target_list:
            if isinstance(existing, dict) and existing.get("id") == item_id:
                existing.update(item)
                applied.append(f"updated(id={item_id})")
                break
        else:
            target_list.append(item)
            applied.append(f"added(id={item_id})")
    return applied


def _apply_json_removed(target_list: list, data: Any) -> list[str]:
    applied: list[str] = []
    ids: list[Any] = []
    if isinstance(data, dict) and "ids" in data:
        ids = list(data["ids"])
    elif isinstance(data, dict) and "id" in data:
        ids = [data["id"]]
    elif isinstance(data, list):
        ids = data
    else:
        ids = [data]
    before = len(target_list)
    target_list[:] = [x for x in target_list if not (isinstance(x, dict) and x.get("id") in ids)]
    removed = before - len(target_list)
    applied.append(f"removed(count={removed})")
    return applied


def _apply_json_updated(target_list: list, data: Any) -> list[str]:
    applied: list[str] = []
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get("id")
        if item_id is None:
            continue
        for existing in target_list:
            if isinstance(existing, dict) and existing.get("id") == item_id:
                existing.update(item)
                applied.append(f"updated(id={item_id})")
                break
        else:
            target_list.append(item)
            applied.append(f"added(id={item_id})")
    return applied


def apply_json_changes(current: Any, changes: list[FileChange]) -> tuple[Any, list[str]]:
    _, target_list = _find_target_list(current)
    applied: list[str] = []
    for ch in changes:
        if ch.type == "added":
            applied.extend(_apply_json_added(target_list, ch.data))
        elif ch.type == "removed":
            applied.extend(_apply_json_removed(target_list, ch.data))
        elif ch.type == "updated":
            applied.extend(_apply_json_updated(target_list, ch.data))
    return current, applied


def _split_paragraphs(text: str) -> list[str]:
    """
    Split markdown/text into paragraphs (blank-line separated), trimmed.
    Empty paragraphs are removed.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip())]
    return [p for p in parts if p]


def _normalize_md_log_lines(items: list[str]) -> list[str]:
    """
    Convert multi-line markdown snippets into a nice JSON-friendly array of lines.
    - Splits on newlines
    - Trims whitespace
    - Drops empty lines
    """
    out: list[str] = []
    for item in items:
        for line in str(item).splitlines():
            s = line.strip()
            if s:
                out.append(s)
    return out


def _nearest_heading(lines: list[str], idx: int) -> str:
    """
    Return the nearest markdown heading line at or above idx.
    """
    i = min(max(idx, 0), len(lines) - 1) if lines else 0
    for j in range(i, -1, -1):
        s = lines[j].strip()
        if s.startswith("#"):
            return s
    return ""


def _md_line_changes(before: str, after: str) -> dict[str, list[dict[str, str]]]:
    """
    Produce minimal markdown changes as line items:
      {"added":[{title,text}], "deleted":[{title,text}], "updated":[{title,text}]}

    - We treat "replace" as deleted(old) + updated(new)
    - We ignore empty lines.
    """
    before_lines = [ln.rstrip() for ln in before.splitlines()]
    after_lines = [ln.rstrip() for ln in after.splitlines()]

    sm = difflib.SequenceMatcher(a=before_lines, b=after_lines)
    added: list[dict[str, str]] = []
    deleted: list[dict[str, str]] = []
    updated: list[dict[str, str]] = []

    def _emit(target: list[dict[str, str]], lines: list[str], i: int, title: str) -> None:
        text = lines[i].strip()
        if not text:
            return
        target.append({"title": title, "text": text})

    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == "equal":
            continue
        if tag == "insert":
            for j in range(j1, j2):
                _emit(added, after_lines, j, _nearest_heading(after_lines, j))
            continue
        if tag == "delete":
            for i in range(i1, i2):
                _emit(deleted, before_lines, i, _nearest_heading(before_lines, i))
            continue
        if tag == "replace":
            for i in range(i1, i2):
                _emit(deleted, before_lines, i, _nearest_heading(before_lines, i))
            for j in range(j1, j2):
                _emit(updated, after_lines, j, _nearest_heading(after_lines, j))
            continue

    return {"added": added, "deleted": deleted, "updated": updated}


def _extract_md_snippets(full_doc: str, updated_fields: list[str] | None) -> list[str]:
    """
    Best-effort extraction of "what changed" snippets for markdown when a re-run
    produces no textual diff (because the file already contains the content).

    Returns a list of short snippets (paragraph-ish strings).
    """
    fields = [f.strip() for f in (updated_fields or []) if str(f).strip()]
    if not fields:
        return []

    lines = full_doc.splitlines()

    snippets: list[str] = []
    seen: set[str] = set()

    # 1) Try to capture heading blocks for any heading-like field.
    for field in fields:
        # Find a line that contains the field (case-insensitive).
        idx = None
        field_l = field.lower()
        for i, line in enumerate(lines):
            if field_l in line.lower():
                idx = i
                break
        if idx is None:
            continue

        # If it's a heading line, capture until next heading of same/higher level.
        line = lines[idx]
        if line.lstrip().startswith("#"):
            level = len(line) - len(line.lstrip("#"))
            buf = [line.rstrip()]
            for j in range(idx + 1, len(lines)):
                nxt = lines[j]
                if nxt.lstrip().startswith("#"):
                    nxt_level = len(nxt) - len(nxt.lstrip("#"))
                    if nxt_level <= level:
                        break
                buf.append(nxt.rstrip())
            block = "\n".join(buf).strip()
            if block and block not in seen:
                seen.add(block)
                snippets.append(block)
            continue

        # Otherwise capture the matching line + a few neighbors (useful for bullets).
        start = max(0, idx - 2)
        end = min(len(lines), idx + 4)
        block = "\n".join(x.rstrip() for x in lines[start:end]).strip()
        if block and block not in seen:
            seen.add(block)
            snippets.append(block)

    # 2) As a fallback, return any paragraphs that mention any field.
    if not snippets:
        paras = _split_paragraphs(full_doc)
        for p in paras:
            pl = p.lower()
            if any(f.lower() in pl for f in fields):
                if p not in seen:
                    seen.add(p)
                    snippets.append(p)

    return snippets[:10]


def _extract_md_snippets_from_reasoning(full_doc: str, reasoning: str | None) -> list[str]:
    """
    Fallback: pick paragraph(s) from full_doc that best match the reasoning text.
    """
    r = (reasoning or "").strip().lower()
    if not r:
        return []
    words = [w for w in re.findall(r"[a-z0-9]+", r) if len(w) >= 4]
    if not words:
        return []
    keywords: list[str] = []
    seen: set[str] = set()
    for w in words:
        if w in seen:
            continue
        seen.add(w)
        keywords.append(w)
        if len(keywords) >= 12:
            break

    paras = _split_paragraphs(full_doc)
    scored: list[tuple[int, str]] = []
    for p in paras:
        pl = p.lower()
        score = sum(1 for k in keywords if k in pl)
        if score:
            scored.append((score, p))
    scored.sort(key=lambda x: (-x[0], len(x[1])))
    return [p for _, p in scored[:5]]


def _extract_md_relevant_lines(full_doc: str, reasoning: str | None) -> list[tuple[int, str]]:
    """
    Pick the most relevant *single lines* from full_doc based on reasoning text.
    Prefers bullet lines and lines containing numbers.
    Returns (line_index, line_text).
    """
    r = (reasoning or "").strip().lower()
    if not r:
        return []

    keywords = [w for w in re.findall(r"[a-z0-9]+", r) if len(w) >= 4]
    keywords = list(dict.fromkeys(keywords))[:12]

    full_lines = [ln.rstrip("\n") for ln in full_doc.splitlines()]
    scored: list[tuple[int, int, int, str]] = []
    # score tuple: (score, is_bullet, has_number, idx, text)
    for idx, ln in enumerate(full_lines):
        s = ln.strip()
        if not s:
            continue
        sl = s.lower()
        score = sum(1 for k in keywords if k in sl)
        if score == 0:
            continue
        is_bullet = 1 if s.startswith(("-", "*")) else 0
        has_number = 1 if re.search(r"\d", s) else 0
        # Keep logs minimal: only bullets (or numeric lines).
        if not is_bullet and not has_number:
            continue
        scored.append((score, is_bullet, has_number, idx, s))

    scored.sort(key=lambda x: (-x[0], -x[1], -x[2], x[3]))
    out: list[tuple[int, str]] = []
    seen_text: set[str] = set()
    for _, _, _, idx, s in scored:
        if s in seen_text:
            continue
        seen_text.add(s)
        out.append((idx, s))
        if len(out) >= 3:
            break
    return out

def _extract_ids_from_change(ch: FileChange) -> list[Any]:
    data = ch.data
    if ch.type in {"added", "updated"}:
        if isinstance(data, dict) and "id" in data:
            return [data["id"]]
        if isinstance(data, list):
            ids: list[Any] = []
            for item in data:
                if isinstance(item, dict) and "id" in item:
                    ids.append(item["id"])
            return ids
        return []

    if ch.type == "removed":
        if isinstance(data, dict) and "ids" in data and isinstance(data["ids"], list):
            return list(data["ids"])
        if isinstance(data, dict) and "id" in data:
            return [data["id"]]
        if isinstance(data, list):
            return list(data)
        return [data]

    return []


def _flatten_payload(obj: Any, *, prefix: str = "") -> dict[str, Any]:
    """
    Flatten a payload dict into dot-path -> value.
    - Dicts are flattened recursively.
    - Lists are treated as a whole value at the current path.
    """
    if isinstance(obj, dict):
        out: dict[str, Any] = {}
        for k in sorted(obj.keys()):
            p = f"{prefix}.{k}" if prefix else str(k)
            v = obj[k]
            if isinstance(v, dict):
                out.update(_flatten_payload(v, prefix=p))
            else:
                out[p] = v
        return out
    return {prefix or "value": obj}


def _json_items_from_llm_changes_compact(changes: list[FileChange]) -> list[dict[str, Any]]:
    """
    Build the exact compact JSON format requested from the LLM changes:
      [{"id": <id>, "changes": [{"path": value}, ...]}, ...]
    Note: This represents what the LLM requested to set (not a before/after diff).
    """
    by_id: dict[Any, dict[str, Any]] = {}

    for ch in changes:
        if ch.type == "removed":
            for item_id in _extract_ids_from_change(ch):
                by_id.setdefault(item_id, {})["__deleted__"] = True
            continue

        data = ch.data
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict) or "id" not in item:
                continue
            item_id = item["id"]
            payload = dict(item)
            payload.pop("id", None)
            flat = _flatten_payload(payload, prefix="")
            target = by_id.setdefault(item_id, {})
            target.update(flat)

    out: list[dict[str, Any]] = []
    for item_id, flat_changes in by_id.items():
        out.append({"id": item_id, "changes": [{k: v} for k, v in flat_changes.items()]})
    return out


def _write_daily_summary(d: date, *, summary_md: str, model_id: str) -> str:
    text = f"## Summary\n{summary_md.strip()}\n"
    _write_text(_daily_summary_path_with_model(d, model_id), text)
    return text


def _write_daily_updates_log(
    d: date,
    *,
    cost: float,
    update_entries: list[dict[str, Any]],
    model_id: str,
) -> None:
    # Schema requested by user:
    # [
    #   {
    #     "file": "",
    #     "date": "",
    #     "model": "",
    #     "cost": "",
    #     "type": "day.summary/week.summary/month.summary",
    #     "changes": {
    #       "type": "updated/deleted/created",
    #       "data": "...",
    #       "reasoning": "..."
    #     }
    #   }
    # ]
    _write_json(_daily_updates_path_with_model(d, model_id), update_entries)


def _load_stage2_md_prompt() -> str:
    return _load_prompt(UPDATE_MD_APP_PAGE_PROMPT_PATH)


def _load_stage2_json_prompt() -> str:
    return _load_prompt(UPDATE_JSON_APP_DATA_PROMPT_PATH)


def _load_stage2_file_prompt(target_file: str) -> str | None:
    file_prompts: dict[str, Path] = {
        "data/app_pages/Education.md": UPDATE_EDUCATION_MD_PROMPT_PATH,
        "data/app_json/dante_topics_to_discuss.json": UPDATE_DANTE_TOPICS_JSON_PROMPT_PATH,
        "data/app_json/todo_list.json": UPDATE_TODO_LIST_JSON_PROMPT_PATH,
        "data/app_json/video.json": UPDATE_VIDEO_JSON_PROMPT_PATH,
    }
    p = file_prompts.get(target_file)
    if not p:
        return None
    if not p.exists():
        return None
    return _load_prompt(p)


def _build_stage2_system_prompt(prompt_key: PromptKey, *, target_file: str) -> str:
    base = _load_updates_context_prompt().rstrip()
    file_prompt = _load_stage2_file_prompt(target_file)
    if file_prompt:
        p = file_prompt.rstrip()
    elif prompt_key == "md_page":
        p = _load_stage2_md_prompt().rstrip()
    else:
        p = _load_stage2_json_prompt().rstrip()
    return f"{base}\n\n{p}\n"


def _validate_stage2_update_object(obj: Any) -> None:
    """
    Validate a single stage2 update object using the existing per-update rules.
    """
    _validate_daily_report_payload({"updates": [obj]})


def _parse_single_update(obj: dict[str, Any]) -> FileUpdate:
    updates = _parse_updates({"updates": [obj]})
    if len(updates) != 1:
        raise ValueError("Stage2: expected exactly one update object")
    return updates[0]


def _read_current_file_content(rel_path: str) -> str:
    abs_path = REPO_ROOT / rel_path
    if not abs_path.exists():
        return ""
    return abs_path.read_text(encoding="utf-8")


def _apply_file_update_and_build_log_entry(
    d: date,
    *,
    upd: FileUpdate,
    model_id: str,
    cost: float,
) -> dict[str, Any]:
    abs_path = REPO_ROOT / upd.file

    if upd.format == "md":
        full_doc = None
        for c in upd.changes:
            if c.full_document:
                full_doc = c.full_document
                break
        if full_doc is None:
            raise ValueError(f"Missing full_document for md update: {upd.file}")

        before = _read_text(abs_path) if abs_path.exists() else ""
        _write_text(abs_path, full_doc)
        line_changes = _md_line_changes(before, full_doc)

        # Re-run fallback: if no actual diffs, still log relevant lines (as updated)
        if not line_changes["added"] and not line_changes["updated"] and not line_changes["deleted"]:
            doc_lines = [ln.rstrip("\n") for ln in full_doc.splitlines()]

            picked = _extract_md_relevant_lines(full_doc, upd.reasoning)
            if picked:
                line_changes["updated"] = [{"title": _nearest_heading(doc_lines, idx), "text": txt} for idx, txt in picked]
            else:
                fallback_blocks = _extract_md_snippets(full_doc, upd.updated_fields)
                if not fallback_blocks:
                    fallback_blocks = _extract_md_snippets_from_reasoning(full_doc, upd.reasoning)
                fallback_lines = _normalize_md_log_lines(fallback_blocks)

                bullets = [ln for ln in fallback_lines if ln.startswith(("-", "*"))]
                chosen = bullets[:8] if bullets else fallback_lines[:3]

                r = (upd.reasoning or "").lower()
                keys = [w for w in re.findall(r"[a-z0-9]+", r) if len(w) >= 4]
                keys = list(dict.fromkeys(keys))[:12]
                if keys:
                    chosen2 = [ln for ln in chosen if any(k in ln.lower() for k in keys)]
                    chosen = chosen2 if chosen2 else (bullets[:1] if bullets else chosen[:1])

                if chosen:
                    default_title = next((ln.strip() for ln in doc_lines if ln.strip().startswith("#")), "")
                    line_changes["updated"] = [{"title": default_title, "text": ln} for ln in chosen]

        md_changes_arr: list[dict[str, Any]] = []
        reason = (upd.reasoning or "").strip() or "(missing)"
        for t in ("added", "updated", "deleted"):
            items = line_changes[t]
            if not items:
                continue
            data = [{"title": it.get("title", ""), "text": it.get("text", ""), "reasoning": reason} for it in items]
            md_changes_arr.append({"type": t, "data": data})

        return {
            "file": upd.file,
            "date": d.isoformat(),
            "model": model_id,
            "cost": cost,
            "type": "day.summary",
            "changes": md_changes_arr,
        }

    if upd.format == "json":
        current = _read_json(abs_path) if abs_path.exists() else []
        updated_obj, _ = apply_json_changes(
            current=current,
            changes=upd.changes,
        )
        _write_json(abs_path, updated_obj)

        grouped: dict[str, list[dict[str, Any]]] = {"added": [], "updated": [], "deleted": []}
        for ch in upd.changes:
            if ch.type == "removed":
                ids = _extract_ids_from_change(ch)
                for item_id in ids:
                    grouped["deleted"].append({"id": item_id, "changes": [{"__deleted__": True}]})
                continue
            items = _json_items_from_llm_changes_compact([ch])
            if ch.type == "added":
                grouped["added"].extend(items)
            elif ch.type == "updated":
                grouped["updated"].extend(items)

        def _dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[Any] = set()
            out: list[dict[str, Any]] = []
            for it in items:
                i = it.get("id")
                if i in seen:
                    continue
                seen.add(i)
                out.append(it)
            return out

        json_changes_arr: list[dict[str, Any]] = []
        reason = (upd.reasoning or "").strip() or "(missing)"
        for t in ("added", "updated", "deleted"):
            items = _dedup(grouped[t])
            if not items:
                continue
            data = [{"id": it.get("id"), "changes": it.get("changes") or [], "reasoning": reason} for it in items]
            json_changes_arr.append({"type": t, "data": data})

        return {
            "file": upd.file,
            "date": d.isoformat(),
            "model": model_id,
            "cost": cost,
            "type": "day.summary",
            "changes": json_changes_arr,
        }

    raise ValueError(f"Unknown format: {upd.format} for {upd.file}")


async def run_daily_report(
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

    # Fast path: no messages => no LLM calls and no updates.
    messages_path = _daily_messages_path(d)
    report_text = messages_path.read_text(encoding="utf-8") if messages_path.exists() else ""
    if not report_text.strip():
        summary_md = "(no messages)"
        model_id_for_files = model_to_use
        report_md = _write_daily_summary(d, summary_md=summary_md, model_id=model_id_for_files)
        _write_daily_updates_log(d, cost=0.0, update_entries=[], model_id=model_id_for_files)
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
            text = f"DAILY REPORT {d.isoformat()}\n\n{report_md.strip()}"
            await send_telegram_long_text(bot_token=bot_token, chat_id=chat_id, text=text)
        return {
            "date": d.isoformat(),
            "summary": summary_md,
            "updates": [],
            "update_plan": [],
            "daily_messages_path": str(_daily_messages_path(d).relative_to(REPO_ROOT)),
            "daily_summary_path": str(_daily_summary_path_with_model(d, model_id_for_files).relative_to(REPO_ROOT)),
            "daily_updates_path": str(_daily_updates_path_with_model(d, model_id_for_files).relative_to(REPO_ROOT)),
            "daily_summary_text": report_md,
        }

    # Stage 1: summary + update_plan (no file edits).
    payload_stage1 = _build_stage1_payload(d)
    tracked_files = list((payload_stage1.get("updates_context") or {}).get("tracked_files") or [])
    response_stage1 = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=_build_stage1_system_prompt(),
        user_content=json.dumps(payload_stage1, ensure_ascii=False),
        max_tokens=2500,
    )
    parsed_stage1 = json.loads(response_stage1.content)
    _validate_stage1_payload(parsed_stage1, tracked_files=tracked_files)
    plan_items = _parse_update_plan(parsed_stage1)

    _append_llm_log(
        model=response_stage1.model,
        input_data={"date": d.isoformat(), "daily_report_messages": "<see file>", "stage": 1, "updates_context": "<catalog>"},
        output_data=parsed_stage1,
        cost=response_stage1.cost,
        context_files=tracked_files,
    )

    # Stage 2: file-by-file updates based on plan_items.
    update_entries: list[dict[str, Any]] = []
    updates_out: list[dict[str, Any]] = []
    total_cost = float(response_stage1.cost or 0.0)
    updates_context = payload_stage1.get("updates_context") or {}

    for item in plan_items:
        current_content = _read_current_file_content(item.file)
        stage2_payload = {
            "date": d.isoformat(),
            "daily_report_messages": report_text,
            "target_file": item.file,
            "current_content": current_content,
            "updated_fields": item.updated_fields,
            "reasoning": item.reasoning,
            "updates_context": updates_context,
        }
        response2 = await _call_model(
            model=model_to_use,
            api_key=api_key,
            system=_build_stage2_system_prompt(item.prompt_key, target_file=item.file),
            user_content=json.dumps(stage2_payload, ensure_ascii=False),
            max_tokens=4000,
        )
        total_cost += float(response2.cost or 0.0)

        parsed_update = json.loads(response2.content)
        _validate_stage2_update_object(parsed_update)

        _append_llm_log(
            model=response2.model,
            input_data={"date": d.isoformat(), "target_file": item.file, "stage": 2, "updated_fields": item.updated_fields},
            output_data=parsed_update,
            cost=response2.cost,
            context_files=[item.file],
        )

        upd = _parse_single_update(parsed_update)
        entry = _apply_file_update_and_build_log_entry(d, upd=upd, model_id=response2.model, cost=response2.cost)
        update_entries.append(entry)
        updates_out.append(parsed_update)

    summary_md = str(parsed_stage1.get("summary") or "").strip() or "(no summary)"
    model_id_for_files = response_stage1.model
    report_md = _write_daily_summary(d, summary_md=summary_md, model_id=model_id_for_files)
    _write_daily_updates_log(d, cost=total_cost, update_entries=update_entries, model_id=model_id_for_files)
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
        text = f"DAILY REPORT {d.isoformat()}\n\n{report_md.strip()}"
        await send_telegram_long_text(bot_token=bot_token, chat_id=chat_id, text=text)

    return {
        "date": d.isoformat(),
        "summary": summary_md,
        "updates": updates_out,
        "update_plan": [u.__dict__ for u in plan_items],
        "daily_messages_path": str(_daily_messages_path(d).relative_to(REPO_ROOT)),
        "daily_summary_path": str(_daily_summary_path_with_model(d, model_id_for_files).relative_to(REPO_ROOT)),
        "daily_updates_path": str(_daily_updates_path_with_model(d, model_id_for_files).relative_to(REPO_ROOT)),
        "daily_summary_text": report_md,
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

    parser = argparse.ArgumentParser(description="Run daily report update job")
    parser.add_argument("--date", default=date.today().isoformat(), help="YYYY-MM-DD (default: today)")
    parser.add_argument("--model", default=None, help="OpenRouter model id (optional)")
    parser.add_argument("--no-send", action="store_true", help="Do not send to Telegram (default: send)")
    args = parser.parse_args()

    d = _parse_date(args.date)
    model = args.model

    import asyncio

    result = asyncio.run(run_daily_report(d, model=model, send=not args.no_send))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

