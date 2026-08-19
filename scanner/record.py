"""record.py — persist LLM analysis results for one article into state.json.

Usage (called by the cron agent after analysis):
    python3 record.py '<article_id>' '<analysis_json>'
    python3 record.py --digest '<article_ids_json>'   # mark delivered as digest

analysis_json schema:
    {"score": 9, "category": "...", "sentiment": "...",
     "instruments": [...], "entities": [...], "summary_de": "..."}
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import state  # noqa: E402


def find_article(st, article_id):
    for a in st.get("articles", []):
        if a.get("id") == article_id:
            return a
    return None


def record_analysis(article_id, analysis):
    st = state.load_state()
    article = find_article(st, article_id)
    pending = st.setdefault("pending", {})
    if article is None:
        metadata = pending.get(article_id, {})
        article = {
            "id": article_id,
            "title": metadata.get("title", analysis.get("title", article_id)),
            "url": metadata.get("url", analysis.get("url", "")),
            "stream": metadata.get("stream", analysis.get("stream", "unknown")),
            "source": metadata.get("source", analysis.get("source", "")),
            "published": metadata.get("published", analysis.get("published", None)),
        }
        state.append_article(st, article)
    for key in ("score", "category", "sentiment", "instruments", "entities", "summary"):
        if key in analysis and analysis[key] is not None:
            article[key] = analysis[key]
    pending.pop(article_id, None)
    state.save_state(st)
    print(json.dumps({"ok": True, "id": article_id, "score": article.get("score")}))


def record_batch(batch_file):
    """Analyze a batch of articles from a JSON file in one call.

    batch_file: JSON array of {id, title, url, stream, source, published,
                score, category, sentiment, instruments, entities, summary}
    Returns count of records written.
    """
    with open(batch_file, "r", encoding="utf-8") as f:
        batch = json.load(f)
    st = state.load_state()
    pending = st.setdefault("pending", {})
    written = 0
    for item in batch:
        article_id = item.get("id") or state.article_id(item.get("title", ""), item.get("url", ""))
        article = find_article(st, article_id)
        if article is None:
            metadata = pending.get(article_id, {})
            article = {
                "id": article_id,
                "title": item.get("title", metadata.get("title", article_id)),
                "url": item.get("url", metadata.get("url", "")),
                "stream": item.get("stream", metadata.get("stream", "unknown")),
                "source": item.get("source", metadata.get("source", "")),
                "published": item.get("published", metadata.get("published", None)),
            }
            state.append_article(st, article)
        for key in ("score", "category", "sentiment", "instruments", "entities", "summary",
                    "title", "url", "stream", "source", "published"):
            if key in item and item[key] is not None:
                article[key] = item[key]
        pending.pop(article_id, None)
        written += 1
    state.save_state(st)
    print(json.dumps({"ok": True, "written": written}))
    return written


def mark_digested(article_ids):
    st = state.load_state()
    ts = state.now_iso()
    for aid in article_ids:
        a = find_article(st, aid)
        if a:
            a["delivered"] = "digest"
            a["delivered_at"] = ts
    st.setdefault("digest_log", []).append({"at": ts, "article_ids": article_ids})
    state.save_state(st)
    print(json.dumps({"ok": True, "digested": len(article_ids)}))


def main():
    if len(sys.argv) < 2:
        print("usage: record.py <article_id> <analysis_json> | record.py --digest '<ids_json>' | record.py --batch <batch_file>", file=sys.stderr)
        sys.exit(1)
    if sys.argv[1] == "--digest":
        ids = json.loads(sys.argv[2])
        mark_digested(ids)
    elif sys.argv[1] == "--batch":
        record_batch(sys.argv[2])
    else:
        article_id = sys.argv[1]
        analysis = json.loads(sys.argv[2])
        record_analysis(article_id, analysis)


if __name__ == "__main__":
    main()
