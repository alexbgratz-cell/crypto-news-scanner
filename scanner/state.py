"""State management for the news scanner (stdlib only, Python 3.9 compatible).

Persists to state.json: seen-hash dedup map, analyzed articles, digest log.
"""
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATE_PATH = os.path.join(BASE_DIR, "state.json")
MAX_ARTICLES = 200


def now_iso():
    """UTC timestamp in 'YYYY-MM-DDTHH:MM:SSZ' format (3.9-safe)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(ts):
    """Parse '...Z' ISO string; falls back to epoch on malformed input."""
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return datetime.fromtimestamp(0, tz=timezone.utc)


def default_state():
    return {"seen": {}, "pending": {}, "articles": [], "digest_log": [], "last_snapshot": None}


def load_state(path=None):
    """Load state; resolves STATE_PATH at call time (test-friendly)."""
    if path is None:
        path = STATE_PATH
    if not os.path.exists(path):
        return default_state()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        # merge with defaults in case of missing keys
        state = default_state()
        state.update({k: v for k, v in data.items() if k in state})
        return state
    except (json.JSONDecodeError, OSError):
        return default_state()


def save_state(state, path=None):
    if path is None:
        path = STATE_PATH
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def prune_state(state, window_days=7):
    """Remove seen-entries older than window_days (rolling window)."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
    keep = {}
    for h, ts in state.get("seen", {}).items():
        if parse_iso(ts) >= cutoff:
            keep[h] = ts
    state["seen"] = keep
    pending = {}
    for article_id, article in state.get("pending", {}).items():
        if parse_iso(article.get("published")) >= cutoff:
            pending[article_id] = article
    state["pending"] = pending
    return state


def is_seen(state, h):
    return h in state.get("seen", {})


def mark_seen(state, h, ts=None):
    state.setdefault("seen", {})[h] = ts or now_iso()


def append_article(state, article):
    """Append analyzed article; cap at MAX_ARTICLES (FIFO)."""
    state.setdefault("articles", []).append(article)
    if len(state["articles"]) > MAX_ARTICLES:
        state["articles"] = state["articles"][-MAX_ARTICLES:]


def article_id(title, link):
    """Stable dedup id: sha256 over normalized link + title."""
    raw = f"{link}|{title}".strip().lower()
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
