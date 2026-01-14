from __future__ import annotations

import argparse
import copy
import json
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Literal

from .handle_message import DEFAULT_MODELS, _append_llm_log, _call_model


REPO_ROOT = Path(__file__).parent.parent.parent
DAILY_REPORTS_DIR = REPO_ROOT / "data" / "daily_reports"
APP_PAGES_DIR = REPO_ROOT / "data" / "app_pages"
APP_JSON_DIR = REPO_ROOT / "data" / "app_json"


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


def _daily_prompt() -> str:
    return (
        "You are preparing a daily update run.\n"
        "You will receive:\n"
        "- the daily report messages for the day\n"
        "- current contents of ALL app files (markdown + json)\n"
        "\n"
        "Your job:\n"
        "- Produce a short day summary\n"
        "- Decide what files should be updated based on the day's messages\n"
        "- Output JSON only.\n"
        "\n"
        "Output schema:\n"
        "{\n"
        '  "summary": "markdown string",\n'
        '  "updates": [\n'
        "    {\n"
        '      "file": "data/app_pages/Education.md",\n'
        '      "format": "md|json",\n'
        '      "reasoning": "why this file must be updated based on messages (string)",\n'
        '      "updated_fields": ["field/path list (for md: section headings; for json: keys or dotted paths)"],\n'
        '      "changes": [\n'
        '        {"type": "added", "data": "..."} ,\n'
        '        {"type": "removed", "data": "..."} ,\n'
        '        {"type": "updated", "data": "...", "full_document": "FULL_FILE_CONTENT_FOR_MD"}\n'
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n"
        "\n"
        "Rules:\n"
        "- If daily_report_messages is empty, set summary to '(no messages)' and output updates=[].\n"
        "- For format=md: include exactly one change with type=updated and a full_document.\n"
        "- For format=json: do NOT include full_document. Use changes with type added/removed/updated and put the structured object(s) in `data`.\n"
        "- Always include `reasoning` and `updated_fields` for every item in updates.\n"
        "- Do not output updates for files that should not change.\n"
    )


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


def _daily_summary_path(d: date) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.summary.md"


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


def _daily_updates_path(d: date) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.updates.json"


def _daily_updates_path_with_model(d: date, model_id: str) -> Path:
    return DAILY_REPORTS_DIR / f"{d.isoformat()}.updates.{_model_slug(model_id)}.json"


def _collect_context_files() -> list[str]:
    files: list[str] = []
    for p in sorted(APP_PAGES_DIR.glob("*.md")):
        files.append(str(p.relative_to(REPO_ROOT)))
    for p in sorted(APP_JSON_DIR.glob("*.json")):
        files.append(str(p.relative_to(REPO_ROOT)))
    return files


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
    }


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


def _find_target_list(root: Any) -> tuple[Any, list]:
    if isinstance(root, list):
        return root, root
    if isinstance(root, dict):
        for key in ("items", "topics"):
            val = root.get(key)
            if isinstance(val, list):
                return root, val
    raise ValueError("Unsupported JSON shape (expected list or dict with items/topics list)")


def _get_id_value(item: Any) -> Any:
    if isinstance(item, dict) and "id" in item:
        return item["id"]
    return None


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


def _json_pretty(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2)


def _split_paragraphs(text: str) -> list[str]:
    """
    Split markdown/text into paragraphs (blank-line separated), trimmed.
    Empty paragraphs are removed.
    """
    parts = [p.strip() for p in re.split(r"\n\s*\n+", text.strip())]
    return [p for p in parts if p]


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

def _md_change_paragraphs(before: str, after: str) -> dict[str, list[str]]:
    """
    Returns paragraphs changed, grouped by:
      - added: paragraphs present in `after` inserted vs `before`
      - deleted: paragraphs removed from `before`
      - updated: paragraphs that replaced others (new paragraphs)

    For 'replace', we treat it as deleted(old) + updated(new).
    """
    before_paras = _split_paragraphs(before)
    after_paras = _split_paragraphs(after)

    # Simple sequence matching without external dependencies:
    # Use python's built-in difflib-like approach via SequenceMatcher from difflib module,
    # but avoid importing it by implementing minimal logic: fallback to full replace.
    #
    # Keep it simple: if lists match, nothing changed.
    if before_paras == after_paras:
        return {"added": [], "deleted": [], "updated": []}

    # Minimal, deterministic approach:
    # - added: paragraphs in after not in before (by exact string)
    # - deleted: paragraphs in before not in after
    # - updated: if both added and deleted exist, consider added as updated and clear added
    before_set = set(before_paras)
    after_set = set(after_paras)
    added = [p for p in after_paras if p not in before_set]
    deleted = [p for p in before_paras if p not in after_set]
    updated: list[str] = []

    if added and deleted:
        # Treat replacements as updates (new paragraphs)
        updated = added
        added = []

    return {"added": added, "deleted": deleted, "updated": updated}


def _flatten_value_changes(before: Any, after: Any, *, prefix: str = "") -> dict[str, Any]:
    """
    Return mapping of changed property paths -> new value.
    - For dicts: recurse into keys.
    - For lists/other: treat as atomic; if changed, record whole new value.
    """
    if before == after:
        return {}

    if isinstance(before, dict) and isinstance(after, dict):
        out: dict[str, Any] = {}
        keys = set(before.keys()) | set(after.keys())
        for k in sorted(keys):
            p = f"{prefix}.{k}" if prefix else str(k)
            b = before.get(k)
            a = after.get(k)
            if isinstance(b, dict) and isinstance(a, dict):
                out.update(_flatten_value_changes(b, a, prefix=p))
            else:
                if b != a:
                    out[p] = a
        return out

    # Lists (or any other type) are treated as a whole value
    return {prefix or "value": after}


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


def _index_items_by_id(obj: Any) -> dict[Any, Any]:
    _, target_list = _find_target_list(obj)
    out: dict[Any, Any] = {}
    for item in target_list:
        if isinstance(item, dict) and "id" in item:
            out[item["id"]] = item
    return out


def _ids_from_applied(applied: list[str]) -> list[int]:
    # applied strings look like: "added(id=12)" or "updated(id=12)"
    ids: list[int] = []
    for s in applied:
        m = re.search(r"id=(\d+)", s)
        if m:
            ids.append(int(m.group(1)))
    return ids


def _json_items_diff_only(before_obj: Any, after_obj: Any, ids: list[Any]) -> list[dict[str, Any]]:
    """
    Return only actual diffs, formatted as requested:
      {"id": <id>, "changes": [{"path": new_value}, ...]}
    """
    before_by_id = _index_items_by_id(before_obj)
    after_by_id = _index_items_by_id(after_obj)

    out: list[dict[str, Any]] = []
    for item_id in ids:
        b = before_by_id.get(item_id)
        a = after_by_id.get(item_id)

        # deleted item
        if b is not None and a is None:
            out.append({"id": item_id, "changes": [{"__deleted__": True}]})
            continue

        # created item: treat all fields (except id) as "changed"
        if b is None and isinstance(a, dict):
            a_no_id = dict(a)
            a_no_id.pop("id", None)
            flat = _flatten_value_changes({}, a_no_id, prefix="")
            out.append({"id": item_id, "changes": [{k: v} for k, v in flat.items()]})
            continue

        # updated item: only changed fields (excluding id)
        if isinstance(b, dict) and isinstance(a, dict):
            b_no_id = dict(b)
            a_no_id = dict(a)
            b_no_id.pop("id", None)
            a_no_id.pop("id", None)
            flat = _flatten_value_changes(b_no_id, a_no_id, prefix="")
            out.append({"id": item_id, "changes": [{k: v} for k, v in flat.items()]})
            continue

        out.append({"id": item_id, "changes": []})

    return out


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


async def run_daily_report(d: date, model: str | None = None, api_key: str | None = None) -> dict[str, Any]:
    api_key = api_key or os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("Missing OPENROUTER_API_KEY")

    payload = _build_context_payload(d)
    model_to_use = model or DEFAULT_MODELS[0]

    response = await _call_model(
        model=model_to_use,
        api_key=api_key,
        system=_daily_prompt(),
        user_content=json.dumps(payload, ensure_ascii=False),
        max_tokens=4000,
    )
    parsed = json.loads(response.content)

    _append_llm_log(
        model=response.model,
        input_data={"date": d.isoformat(), "daily_report_messages": "<see file>", "context_files": payload["context_files"]},
        output_data=parsed,
        cost=response.cost,
        context_files=list(payload["context_files"]),
    )

    updates = _parse_updates(parsed)
    update_entries: list[dict[str, Any]] = []

    for upd in updates:
        abs_path = REPO_ROOT / upd.file
        existed_before = abs_path.exists()
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
            para_changes = _md_change_paragraphs(before, full_doc)

            # If this is a re-run and there is no textual diff, still record the
            # relevant updated snippet(s) from the LLM output.
            if not para_changes["added"] and not para_changes["updated"] and not para_changes["deleted"]:
                fallback = _extract_md_snippets(full_doc, upd.updated_fields)
                if not fallback:
                    fallback = _extract_md_snippets_from_reasoning(full_doc, upd.reasoning)
                if fallback:
                    para_changes["updated"] = fallback

            md_changes_arr: list[dict[str, Any]] = []
            for t in ("added", "updated", "deleted"):
                if para_changes[t]:
                    md_changes_arr.append(
                        {"type": t, "data": para_changes[t], "reasoning": (upd.reasoning or "").strip() or "(missing)"}
                    )
            update_entries.append(
                {
                    "file": upd.file,
                    "date": d.isoformat(),
                    "model": response.model,
                    "cost": response.cost,
                    "type": "day.summary",
                    "changes": md_changes_arr,
                }
            )
            continue

        if upd.format == "json":
            current = _read_json(abs_path) if abs_path.exists() else []
            before_obj = copy.deepcopy(current)
            updated_obj, applied = apply_json_changes(
                current=current,
                changes=upd.changes,
            )
            _write_json(abs_path, updated_obj)
            _ = before_obj
            _ = updated_obj
            # Group compact items by change type (added/updated/deleted)
            grouped: dict[str, list[dict[str, Any]]] = {"added": [], "updated": [], "deleted": []}
            for ch in upd.changes:
                if ch.type == "removed":
                    ids = _extract_ids_from_change(ch)
                    for item_id in ids:
                        grouped["deleted"].append({"id": item_id, "changes": [{"__deleted__": True}]})
                    continue
                # added/updated: use compact per-id changes
                items = _json_items_from_llm_changes_compact([ch])
                if ch.type == "added":
                    grouped["added"].extend(items)
                elif ch.type == "updated":
                    grouped["updated"].extend(items)

            # De-dup within each group by id (preserve first)
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
            for t in ("added", "updated", "deleted"):
                items = _dedup(grouped[t])
                if items:
                    json_changes_arr.append(
                        {"type": t, "data": items, "reasoning": (upd.reasoning or "").strip() or "(missing)"}
                    )
            update_entries.append(
                {
                    "file": upd.file,
                    "date": d.isoformat(),
                    "model": response.model,
                    "cost": response.cost,
                    "type": "day.summary",
                    "changes": json_changes_arr,
                }
            )
            continue

        raise ValueError(f"Unknown format: {upd.format} for {upd.file}")

    summary_md = str(parsed.get("summary") or "").strip() or "(no summary)"
    report_text = _write_daily_summary(d, summary_md=summary_md, model_id=response.model)
    _write_daily_updates_log(d, cost=response.cost, update_entries=update_entries, model_id=response.model)

    return {
        "date": d.isoformat(),
        "summary": summary_md,
        "updates": parsed.get("updates") or [],
        "daily_messages_path": str(_daily_messages_path(d).relative_to(REPO_ROOT)),
        "daily_summary_path": str(_daily_summary_path_with_model(d, response.model).relative_to(REPO_ROOT)),
        "daily_updates_path": str(_daily_updates_path_with_model(d, response.model).relative_to(REPO_ROOT)),
        "daily_summary_text": report_text,
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
    args = parser.parse_args()

    d = _parse_date(args.date)
    model = args.model

    import asyncio

    result = asyncio.run(run_daily_report(d, model=model))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

