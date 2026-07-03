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

    if published_only:
        # Entries written before the "published" field existed are treated
        # as published, so existing content doesn't silently disappear.
        articles = [a for a in articles if a.get("published", True)]
    return articles


def get_article(article_id: str, published_only: bool = False) -> dict | None:
    """Look up a single article by id. Returns None if not found (or, when
    published_only=True, if it exists but is an unpublished draft)."""
    for a in load_articles():
        if a.get("id") == article_id:
            if published_only and not a.get("published", True):
                return None
            return a
    return None


def save_articles(articles: list[dict]) -> None:
    with open(APP_LEARN_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2)


def add_article(title: str, body: str, category: str | None = None, published: bool = True) -> dict:
    now = _now_iso()
    entry = {
        "id"        : uuid.uuid4().hex,
        "title"     : title.strip(),
        "body"      : body.strip(),
        "category"  : (category or "").strip() or None,
        "published" : bool(published),
        "created_at": now,
        "updated_at": now,
    }
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
) -> dict | None:
    """Edit an existing article in place. Returns the updated entry, or None if not found."""
    articles = load_articles()
    for a in articles:
        if a.get("id") == article_id:
            if title is not None:
                a["title"] = title.strip()
            if body is not None:
                a["body"] = body.strip()
            if category is not None:
                a["category"] = category.strip() or None
            if published is not None:
                a["published"] = bool(published)
            a["updated_at"] = _now_iso()
            a.setdefault("created_at", a["updated_at"])
            save_articles(articles)
            return a
    return None
