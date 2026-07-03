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
      "date_added": "2026-07-03"
    },
    ...
  ]

v1 is a flat list — no categories/quizzes in the app UI yet (see master
prompt §5 "Open items for later"). The field is stored now so it's free to
surface later without a schema change.
"""

import os
import json
import uuid
import datetime
from config.settings import APP_LEARN_FILE


def load_articles() -> list[dict]:
    if os.path.exists(APP_LEARN_FILE):
        try:
            with open(APP_LEARN_FILE, encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    return data
        except Exception:
            pass
    return []


def save_articles(articles: list[dict]) -> None:
    with open(APP_LEARN_FILE, "w", encoding="utf-8") as f:
        json.dump(articles, f, indent=2)


def add_article(title: str, body: str, category: str | None = None) -> dict:
    entry = {
        "id"        : uuid.uuid4().hex,
        "title"     : title.strip(),
        "body"      : body.strip(),
        "category"  : (category or "").strip() or None,
        "date_added": datetime.date.today().isoformat(),
    }
    articles = load_articles()
    articles.insert(0, entry)
    save_articles(articles)
    return entry


def update_article(article_id: str, title: str | None, body: str | None, category: str | None) -> dict | None:
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
            save_articles(articles)
            return a
    return None
