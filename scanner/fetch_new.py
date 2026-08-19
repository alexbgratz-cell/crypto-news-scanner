"""fetch_new.py — main scanner script, used as monitor_script by cron job 1.

Every 10 minutes (driven by Hermes cron):
  1. Fetch all RSS feeds for both streams (crypto + ai), feed-tolerant
  2. Dedup against state.json (7-day window)
  3. Refresh market snapshot -> snapshot.json + append history line
  4. stdout: JSON array of ONLY new items (stable/empty when nothing new)
     -> monitor_script hash suppression: agent runs only when output changes

Output stability is CRITICAL: never print timestamps/logs to stdout.
Logs go to stderr and logs/scanner.log.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import state  # noqa: E402
from scanner.feeds import FeedError, fetch_feed  # noqa: E402
from scanner.snapshot import build_snapshot  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
SNAPSHOT_PATH = os.path.join(BASE_DIR, "snapshot.json")
HISTORY_PATH = os.path.join(BASE_DIR, "snapshot_history.jsonl")
LOG_PATH = os.path.join(BASE_DIR, "logs", "scanner.log")
HISTORY_MAX_LINES = 2016  # ~14 days at 10-min intervals
NEW_WINDOW_HOURS = 24     # only items published within this window are emitted


def log(msg):
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(f"{state.now_iso()} {msg}\n")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def collect_new_items(config, st):
    """Fetch all feeds, return list of new items within the recency window.

    Every fetched item is marked seen (dedup), but only items published within
    NEW_WINDOW_HOURS are returned for analysis — feed archives (OpenAI News
    has 1100+ items) would otherwise flood the LLM. Items without a parseable
    publish date are treated as stale (seen, not emitted).
    """
    from datetime import datetime, timedelta, timezone
    cutoff = datetime.now(timezone.utc) - timedelta(hours=NEW_WINDOW_HOURS)
    new_items = []
    for stream_name, stream_cfg in config["streams"].items():
        for url in stream_cfg["feeds"]:
            try:
                items = fetch_feed(url)
            except FeedError as e:
                log(f"WARN feed {url}: {e}")
                continue
            for it in items:
                h = it["guid"]
                if state.is_seen(st, h):
                    continue
                state.mark_seen(st, h)
                it["stream"] = stream_name
                it["id"] = h
                # recency gate: skip items without or with old publish dates
                published = state.parse_iso(it.get("published")) if it.get("published") else None
                if published is None or published < cutoff:
                    continue
                st.setdefault("pending", {})[h] = {
                    "id": h,
                    "title": it.get("title", h),
                    "url": it.get("link", ""),
                    "stream": stream_name,
                    "source": it.get("source", ""),
                    "published": it.get("published"),
                }
                new_items.append(it)
    return new_items


def write_snapshot(config):
    snap = build_snapshot()
    snap["timestamp"] = state.now_iso()
    with open(SNAPSHOT_PATH, "w", encoding="utf-8") as f:
        json.dump(snap, f, ensure_ascii=False, indent=2)
    # history line (append, bounded)
    line = json.dumps({"ts": snap["timestamp"], "instruments": snap["instruments"]}, ensure_ascii=False)
    try:
        with open(HISTORY_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # trim to HISTORY_MAX_LINES
        with open(HISTORY_PATH, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > HISTORY_MAX_LINES:
            with open(HISTORY_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-HISTORY_MAX_LINES:])
    except OSError as e:
        log(f"WARN history write: {e}")
    return snap


def main():
    config = load_config()
    st = state.load_state()
    state.prune_state(st, window_days=config.get("state_window_days", 7))

    new_items = collect_new_items(config, st)
    write_snapshot(config)
    state.save_state(st)

    if new_items:
        # stable output: only the items, nothing else
        print(json.dumps(new_items, ensure_ascii=False))
    log(f"scan done: {len(new_items)} new item(s)")

    # graceful: monitor_script reads stdout; no output = no agent run


if __name__ == "__main__":
    main()
