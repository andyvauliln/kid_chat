from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from . import daily_report as dr


REPO_ROOT = Path(__file__).parent.parent.parent
PENDING_UPDATES_PATH = REPO_ROOT / "data" / "pending_updates.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_items() -> list[dict[str, Any]]:
    if not PENDING_UPDATES_PATH.exists():
        return []
    try:
        raw = PENDING_UPDATES_PATH.read_text(encoding="utf-8")
        data = json.loads(raw) if raw.strip() else []
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _write_items(items: list[dict[str, Any]]) -> None:
    PENDING_UPDATES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PENDING_UPDATES_PATH.write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def add_pending_update(
    *,
    update_obj: dict[str, Any],
    log_entry: dict[str, Any],
    source: str,
    requested_by: str,
    model: str,
    cost: float,
) -> dict[str, Any]:
    entry = {
        "id": str(uuid4()),
        "created_at": _now_iso(),
        "approved_at": None,
        "applied_at": None,
        "approval_status": "not_approved",
        "requested_by": requested_by,
        "source": source,
        "file": str(update_obj.get("file") or ""),
        "update": update_obj,
        "log_entry": log_entry,
        "model": model,
        "cost": float(cost or 0.0),
    }
    items = _load_items()
    items.append(entry)
    _write_items(items)
    return entry


def list_pending_updates(*, status: str | None = "not_approved") -> list[dict[str, Any]]:
    items = _load_items()
    if status is None:
        return items
    return [it for it in items if str(it.get("approval_status") or "").lower() == status.lower()]


def reject_pending_update(update_id: str) -> dict[str, Any] | None:
    items = _load_items()
    for it in items:
        if it.get("id") == update_id:
            if it.get("approval_status") == "approved":
                return it
            it["approval_status"] = "rejected"
            it["approved_at"] = _now_iso()
            _write_items(items)
            return it
    return None


@dataclass
class ApproveResult:
    entry: dict[str, Any]
    apply_log_entry: dict[str, Any] | None


def approve_pending_update(update_id: str) -> ApproveResult | None:
    items = _load_items()
    for it in items:
        if it.get("id") != update_id:
            continue
        if str(it.get("approval_status") or "") == "approved":
            return ApproveResult(entry=it, apply_log_entry=None)
        if str(it.get("approval_status") or "") == "rejected":
            return None

        upd_obj = it.get("update")
        if not isinstance(upd_obj, dict):
            return None
        dr._validate_stage2_update_object(upd_obj)
        upd = dr._parse_single_update(upd_obj)

        apply_log_entry = dr._apply_file_update_and_build_log_entry(
            date.today(),
            upd=upd,
            model_id=str(it.get("model") or ""),
            cost=float(it.get("cost") or 0.0),
            apply=True,
        )

        it["approval_status"] = "approved"
        it["approved_at"] = _now_iso()
        it["applied_at"] = _now_iso()
        it["apply_log_entry"] = apply_log_entry
        _write_items(items)
        return ApproveResult(entry=it, apply_log_entry=apply_log_entry)
    return None

