"""digest.py — collect articles with score 6-7 not yet digested (job 2 script).

stdout: JSON array of pending digest articles, sorted by score desc,
        empty when none (stable output for monitor_script suppression).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import state  # noqa: E402


def pending_digest_articles(st, digest_min=6, instant=8):
    """Articles with digest_min <= score < instant, not yet delivered as digest."""
    out = []
    for a in st.get("articles", []):
        score = a.get("score")
        if not isinstance(score, (int, float)):
            continue
        if digest_min <= score < instant and a.get("delivered") != "digest":
            out.append(a)
    out.sort(key=lambda x: (x.get("score") or 0), reverse=True)
    return out


def main():
    st = state.load_state()
    pending = pending_digest_articles(st)
    if pending:
        print(json.dumps(pending, ensure_ascii=False))
    # no output = nothing pending = no agent run


if __name__ == "__main__":
    main()
