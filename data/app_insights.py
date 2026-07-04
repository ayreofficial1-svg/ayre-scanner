import datetime
import json
import os
import uuid

from config.settings import APP_INSIGHTS_FILE


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _normalize(entry: dict) -> dict:
    enabled = bool(entry.get("enabled", True))
    return {
        **entry,
        "title": str(entry.get("title") or "").strip(),
        "body": str(entry.get("body") or "").strip(),
        "category": (entry.get("category") or "").strip() or None,
        "enabled": enabled,
        "featured": bool(entry.get("featured", False)),
        "pinned": bool(entry.get("pinned", False)),
        "display_order": int(entry.get("display_order") or 0),
        "image_url": (entry.get("image_url") or "").strip() or None,
        "icon": (entry.get("icon") or "").strip() or None,
        "tone": (entry.get("tone") or "").strip() or "mint",
        "start_at": (entry.get("start_at") or "").strip() or None,
        "end_at": (entry.get("end_at") or "").strip() or None,
        "tags": entry.get("tags") if isinstance(entry.get("tags"), list) else [],
    }


def _visible(entry: dict, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not entry.get("enabled", True):
        return False
    for key, future_hidden in (("start_at", True), ("end_at", False)):
        raw = str(entry.get(key) or "").strip()
        if not raw:
            continue
        try:
            value = datetime.datetime.fromisoformat(raw)
        except ValueError:
            continue
        if value.tzinfo is None:
            value = value.replace(tzinfo=datetime.timezone.utc)
        if future_hidden and value > now:
            return False
        if not future_hidden and value <= now:
            return False
    return True


def _sort_key(entry: dict) -> tuple[int, int, str]:
    return (
        0 if entry.get("pinned") else 1,
        int(entry.get("display_order") or 0),
        str(entry.get("updated_at") or entry.get("created_at") or ""),
    )


def load_insights(visible_only: bool = False) -> list[dict]:
    insights: list[dict] = []
    if os.path.exists(APP_INSIGHTS_FILE):
        try:
            with open(APP_INSIGHTS_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    insights = data
        except Exception:
            pass
    items = [_normalize(i) for i in insights if isinstance(i, dict)]
    if visible_only:
        items = [i for i in items if _visible(i)]
    return sorted(items, key=_sort_key)


def save_insights(insights: list[dict]) -> None:
    with open(APP_INSIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(insights, f, indent=2)


def add_insight(title: str, body: str, **fields) -> dict:
    now = _now_iso()
    entry = _normalize({
        "id": uuid.uuid4().hex,
        "title": title,
        "body": body,
        "created_at": now,
        "updated_at": now,
        **fields,
    })
    insights = load_insights()
    insights.insert(0, entry)
    save_insights(insights)
    return entry


def update_insight(insight_id: str, **fields) -> dict | None:
    insights = load_insights()
    for idx, insight in enumerate(insights):
        if insight.get("id") == insight_id:
            updated = _normalize({**insight, **fields, "updated_at": _now_iso()})
            insights[idx] = updated
            save_insights(insights)
            return updated
    return None


def delete_insight(insight_id: str) -> bool:
    return update_insight(insight_id, enabled=False) is not None
