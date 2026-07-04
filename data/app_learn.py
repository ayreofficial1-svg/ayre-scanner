"""
data/app_learn.py
──────────────────
Persistent store for the consumer app's "Learn" tab — articles/lessons about
the platform and stock market basics. Read by the Flutter app via
GET /api/learn; written by the website admin panel via POST /api/learn.

Entry schema (JSON list, newest first)
───────────────────────────────────────
  [
    {
      "id"        : "a1c2...",         # uuid4 hex
      "title"     : "What is a swing trade?",
      "body"      : "...",             # plain text or simple markdown
      "category"  : "basics",          # optional, may be null; unused in v1 UI
      "published" : true,              # false = draft, hidden from the consumer app
      "created_at": "2026-07-03T10:15:00+00:00",
      "updated_at": "2026-07-03T10:15:00+00:00"
    },
    ...
  ]

v1 is a flat list — no categories/quizzes in the app UI yet (see master
prompt §5 "Open items for later"). The field is stored now so it's free to
surface later without a schema change.

Unpublished (draft) articles are only ever created by an explicit
published=False on POST /api/learn — by default new articles are published
immediately, since the website currently has no separate "save draft" step
in its UI. GET /api/learn and GET /api/learn/<id> only ever return
published articles to the consumer app.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_LEARN_FILE


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _is_visible(entry: dict, now: datetime.datetime | None = None) -> bool:
    now = now or datetime.datetime.now(datetime.timezone.utc)
    if not entry.get("enabled", entry.get("published", True)):
        return False
    start = str(entry.get("start_at") or "").strip()
    end = str(entry.get("end_at") or "").strip()
    try:
        start_at = datetime.datetime.fromisoformat(start) if start else None
        if start_at and start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=datetime.timezone.utc)
        if start_at and start_at > now:
            return False
    except ValueError:
        pass
    try:
        end_at = datetime.datetime.fromisoformat(end) if end else None
        if end_at and end_at.tzinfo is None:
            end_at = end_at.replace(tzinfo=datetime.timezone.utc)
        if end_at and end_at <= now:
            return False
    except ValueError:
        pass
    return True


def _sort_key(entry: dict) -> tuple[int, int, str]:
    pinned = 0 if entry.get("pinned") else 1
    order = int(entry.get("display_order") or 0)
    updated = str(entry.get("updated_at") or entry.get("created_at") or "")
    return (pinned, order, updated)


def load_articles(published_only: bool = False) -> list[dict]:
    """
    Load all articles. Pass published_only=True to filter out drafts
    (this is what GET /api/learn and GET /api/learn/<id> use).
    """
    articles: list[dict] = []
    if os.path.exists(APP_LEARN_FILE):
        try:
            with open(APP_LEARN_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    articles = data
        except Exception:
            pass

    normalized = [_normalize_article(a) for a in articles if isinstance(a, dict)]
    if published_only:
        normalized = [a for a in normalized if _is_visible(a)]
    return sorted(normalized, key=_sort_key)


def get_article(article_id: str, published_only: bool = False) -> dict | None:
    """Look up a single article by id. Returns None if not found (or, when
    published_only=True, if it exists but is an unpublished draft)."""
    for a in load_articles():
        if a.get("id") == article_id:
            a = _normalize_article(a)
            if published_only and not _is_visible(a):
                return None
            return a
    return None


def save_articles(articles: list[dict]) -> None:
    with open(APP_LEARN_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2)


def _normalize_article(entry: dict) -> dict:
    published = bool(entry.get("published", entry.get("enabled", True)))
    normalized = {
        **entry,
        "category": (entry.get("category") or "").strip() or None,
        "published": published,
        "enabled": bool(entry.get("enabled", published)),
        "featured": bool(entry.get("featured", False)),
        "pinned": bool(entry.get("pinned", False)),
        "display_order": int(entry.get("display_order") or 0),
        "image_url": (entry.get("image_url") or "").strip() or None,
        "icon": (entry.get("icon") or "").strip() or None,
        "tone": (entry.get("tone") or "").strip() or "orange",
        "start_at": (entry.get("start_at") or "").strip() or None,
        "end_at": (entry.get("end_at") or "").strip() or None,
        "tags": entry.get("tags") if isinstance(entry.get("tags"), list) else [],
    }
    return normalized


def add_article(
    title: str,
    body: str,
    category: str | None = None,
    published: bool = True,
    **fields,
) -> dict:
    now = _now_iso()
    entry = _normalize_article({
        "id"        : uuid.uuid4().hex,
        "title"     : title.strip(),
        "body"      : body.strip(),
        "category"  : (category or "").strip() or None,
        "published" : bool(published),
        "enabled"   : bool(fields.get("enabled", published)),
        "created_at": now,
        "updated_at": now,
        **fields,
    })
    articles = load_articles()
    articles.insert(0, entry)
    save_articles(articles)
    return entry


def update_article(
    article_id: str,
    title: str | None,
    body: str | None,
    category: str | None,
    published: bool | None = None,
    **fields,
) -> dict | None:
    """Edit an existing article in place. Returns the updated entry, or None if not found."""
    articles = load_articles()
    for idx, a in enumerate(articles):
        if a.get("id") == article_id:
            if title is not None:
                a["title"] = title.strip()
            if body is not None:
                a["body"] = body.strip()
            if category is not None:
                a["category"] = category.strip() or None
            if published is not None:
                a["published"] = bool(published)
                a["enabled"] = bool(fields.get("enabled", published))
            for key in (
                "enabled",
                "featured",
                "pinned",
                "display_order",
                "image_url",
                "icon",
                "tone",
                "start_at",
                "end_at",
                "tags",
            ):
                if key in fields:
                    a[key] = fields[key]
            a["updated_at"] = _now_iso()
            a.setdefault("created_at", a["updated_at"])
            a = _normalize_article(a)
            articles[idx] = a
            save_articles(articles)
            return a
    return None


def delete_article(article_id: str) -> bool:
    return update_article(article_id, None, None, None, False, enabled=False) is not None
