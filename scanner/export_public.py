"""Create a sanitized, read-only data bundle for the public dashboard.

The exporter uses explicit field allowlists. It never copies config.json, Telegram
credentials, dedup state, pending records, digest logs, delivery metadata, or logs.
"""
import argparse
import json
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ARTICLE_FIELDS = (
    "title", "url", "source", "published", "stream", "score", "category",
    "sentiment", "summary", "instruments", "entities",
)
INSTRUMENT_FIELDS = ("value", "change_24h", "group", "label")
CATEGORIES = {
    "crypto": ["FUD", "Tech Update", "Regulation", "Macro", "Hack & Security", "Other"],
    "ai": [
        "Model Release & Capability", "Research & Open Source", "Regulation & Policy",
        "Business & Funding", "Hardware & Infrastructure", "Safety & Risk", "Society & Work",
    ],
}


def _sanitize_instruments(instruments):
    clean = {}
    for symbol, data in (instruments or {}).items():
        data = data or {}
        clean[symbol] = {key: data.get(key) for key in INSTRUMENT_FIELDS}
    return clean


def _write_json(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def export_public(state_data, snapshot_data, history_records, output_dir, article_limit=100, history_limit=864, market_reports=None):
    """Write sanitized static JSON files and return export counts."""
    articles = []
    for article in state_data.get("articles", []):
        if article.get("stream") not in ("crypto", "ai"):
            continue
        if not article.get("title") or not article.get("url") or not article.get("published"):
            continue
        if not isinstance(article.get("score"), (int, float)):
            continue
        articles.append({key: article.get(key, [] if key in ("instruments", "entities") else None)
                         for key in ARTICLE_FIELDS})
    articles.sort(key=lambda item: item.get("published") or "", reverse=True)
    articles = articles[:article_limit]

    snapshot = {
        "timestamp": snapshot_data.get("timestamp"),
        "instruments": _sanitize_instruments(snapshot_data.get("instruments")),
    }
    history = []
    for record in history_records[-history_limit:]:
        if not isinstance(record, dict) or not record.get("ts"):
            continue
        history.append({
            "ts": record.get("ts"),
            "instruments": _sanitize_instruments(record.get("instruments")),
        })

    stats = {
        "articles": len(articles),
        "by_stream": {
            "crypto": sum(1 for article in articles if article["stream"] == "crypto"),
            "ai": sum(1 for article in articles if article["stream"] == "ai"),
        },
        "last_updated": snapshot.get("timestamp"),
    }
    data_dir = os.path.join(output_dir, "data")
    _write_json(os.path.join(data_dir, "news.json"), articles)
    _write_json(os.path.join(data_dir, "snapshot.json"), snapshot)
    _write_json(os.path.join(data_dir, "history.json"), history)
    _write_json(os.path.join(data_dir, "categories.json"), CATEGORIES)
    _write_json(os.path.join(data_dir, "stats.json"), stats)
    # Latest AI market report (sanitized to fixed fields), if any
    report = None
    if market_reports:
        for candidate in market_reports:
            if isinstance(candidate, dict) and candidate.get("text"):
                report = {
                    "ts": candidate.get("ts"),
                    "instrument": candidate.get("instrument", "ALL"),
                    "text": candidate.get("text"),
                    "numbers": [
                        {key: number.get(key) for key in ("sym", "value", "change", "label", "group")}
                        for number in (candidate.get("numbers") or [])
                        if isinstance(number, dict) and number.get("sym")
                    ],
                }
                break
    _write_json(os.path.join(data_dir, "market-report.json"), report)
    return {"articles": len(articles), "history": len(history), "report": bool(report), "output": output_dir}


def _read_json(path, fallback):
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, json.JSONDecodeError):
        return fallback


def _read_market_reports():
    """Read the dashboard's market-reports.json (newest first), if present."""
    path = os.path.join(BASE_DIR, "dashboard", "data", "market-reports.json")
    return _read_json(path, [])


def _read_history(path):
    records = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                try:
                    records.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return records


def main():
    parser = argparse.ArgumentParser(description="Export sanitized data for GitHub Pages")
    parser.add_argument("--output", default=os.path.join(BASE_DIR, "public-site"))
    parser.add_argument("--article-limit", type=int, default=100)
    parser.add_argument("--history-limit", type=int, default=864)
    args = parser.parse_args()
    result = export_public(
        _read_json(os.path.join(BASE_DIR, "state.json"), {"articles": []}),
        _read_json(os.path.join(BASE_DIR, "snapshot.json"), {"instruments": {}}),
        _read_history(os.path.join(BASE_DIR, "snapshot_history.jsonl")),
        os.path.abspath(args.output),
        article_limit=max(1, args.article_limit),
        history_limit=max(1, args.history_limit),
        market_reports=_read_market_reports(),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
