#!/usr/bin/env python3
"""Cloud-News-Scanner für GitHub Actions (nur Python-Stdlib).

Übernimmt die Rolle des lokalen Hermes-Cron-Analyse-Agenten:
  1. config.json im Checkout aus config.cloud.json + Telegram-Env aufbauen
  2. fetch_new.py ausführen -> neue Artikel + frischer Snapshot (state.json,
     snapshot.json, snapshot_history.jsonl)
  3. Jeden neuen Artikel per LLM analysieren (analysis_prompt.md + Rubriken,
     strukturierte JSON-Antwort)
  4. record.py --batch persistiert; Score >= 8 -> Telegram-Push
  5. export_public.py -> öffentliche data/*.json
  6. git commit + push (mit rebase-Retry)

Aufruf: python3 scanner/cloud_scan.py
Env: NOUS_REFRESH_TOKEN, NOUS_CLIENT_ID, NOUS_PORTAL_BASE_URL,
     NOUS_INFERENCE_BASE_URL, TG_BOT_TOKEN, TG_CHAT_ID,
     GITHUB_TOKEN (Push), optional GH_MAINT_TOKEN (Secret-Selbstwartung)
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
PUSH_THRESHOLD = 8


def log(msg):
    print(f"[cloud_scan] {msg}")


def prepare_config():
    """config.json aus config.cloud.json + Telegram-Env bauen (nie Secrets committen)."""
    with open(os.path.join(BASE, "config.cloud.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["telegram"] = {
        "bot_token": os.environ["TG_BOT_TOKEN"],
        "chat_id": os.environ["TG_CHAT_ID"],
    }
    with open(os.path.join(BASE, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    return cfg


def get_nous_token():
    body = urllib.parse.urlencode({
        "grant_type": "refresh_token",
        "client_id": os.environ["NOUS_CLIENT_ID"],
    }).encode()
    req = urllib.request.Request(
        os.environ["NOUS_PORTAL_BASE_URL"].rstrip("/") + "/api/oauth/token",
        data=body,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
            "x-nous-refresh-token": os.environ["NOUS_REFRESH_TOKEN"],
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        t = json.load(r)
    return t["access_token"], t.get("token_type", "Bearer"), t.get("refresh_token", "")


def update_secret_if_possible(new_refresh):
    maint = os.environ.get("GH_MAINT_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not maint or not repo or not new_refresh:
        log("Secret-Update übersprungen (kein GH_MAINT_TOKEN/Repo/neuer Token)")
        return False
    p = subprocess.run(
        ["gh", "secret", "set", "NOUS_REFRESH_TOKEN", "--repo", repo],
        input=new_refresh.encode(),
        capture_output=True,
        env={**os.environ, "GH_TOKEN": maint},
    )
    if p.returncode == 0:
        log("Secret NOUS_REFRESH_TOKEN selbst erneuert")
        return True
    log(f"Secret-Update FEHLGESCHLAGEN: {(p.stderr or '').strip()[:200]}")
    return False


def analyze_article(prompt_txt, token, ttype, art):
    system = (
        prompt_txt
        + "\n\nWICHTIG (Ausgabeformat): Antworte NUR mit einem einzigen JSON-Objekt "
        "und keinem anderen Text, keiner Markdown-Umrandung, keiner Erklärung. "
        'Schema: {"score": 1-10, "category": "eine Kategorie des Streams", '
        '"sentiment": "bullish|bearish|neutral", "instruments": ["max 3 Crypto-Symbole"], '
        '"entities": ["max 3 AI-Entities"], '
        '"summary": "genau 3 Sätze Deutsch (WAS / WIRKUNG / KONSEQUENZ), max ~70 Wörter, nichts erfinden"}'
    )
    user = json.dumps({
        "id": art.get("id"),
        "title": art.get("title"),
        "url": art.get("url"),
        "stream": art.get("stream"),
        "source": art.get("source"),
        "published": art.get("published"),
    }, ensure_ascii=False)
    body = json.dumps({
        "model": "~deepseek/deepseek-v4-flash-latest",
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": 0.2,
    }).encode()
    req = urllib.request.Request(
        os.environ["NOUS_INFERENCE_BASE_URL"].rstrip("/") + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"{ttype} {token}",
            "User-Agent": USER_AGENT,
        },
    )
    with urllib.request.urlopen(req, timeout=120) as r:
        data = json.load(r)
    content = (data["choices"][0]["message"]["content"] or "").strip()
    # robustes JSON-Extrahieren (erste {...}-Klammer bis letzte)
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        raise RuntimeError(f"Kein JSON in LLM-Antwort: {content[:120]}")
    analysis = json.loads(content[start:end + 1])
    for key in ("score", "category", "sentiment", "summary"):
        if key not in analysis or analysis[key] in (None, ""):
            raise RuntimeError(f"LLM-Antwort ohne Pflichtfeld '{key}'")
    analysis["score"] = int(analysis["score"])
    return analysis


def build_push(art, analysis, snap):
    score = analysis["score"]
    se = "🔴" if score >= 9 else "🟠"
    head = f"{'🪙' if art.get('stream') == 'crypto' else '🤖'} <b>{se} {score}/10 · "
    head += f"{analysis.get('category', '')} · {art.get('source', '')}</b>"
    lines = [head, "", analysis.get("summary", "")]
    if art.get("stream") == "crypto" and analysis.get("instruments"):
        inst = snap.get("instruments", {})
        parts = []
        for s in analysis["instruments"][:3]:
            d = inst.get(s, {})
            v = d.get("value")
            if v is None:
                continue
            chg = d.get("change_24h")
            sval = f"{v:,.2f}" if isinstance(v, (int, float)) else str(v)
            parts.append(f"{s} {sval}" + (f" ({chg:+.2f}%)" if isinstance(chg, (int, float)) else ""))
        if parts:
            lines += ["", f"📊 {' · '.join(parts)}"]
    try:
        domain = urllib.parse.urlparse(art.get("url", "")).netloc
    except Exception:
        domain = art.get("source", "")
    lines += ["", f'🔗 <a href="{art.get("url", "")}">{domain}</a>']
    return {"text": "\n".join(lines)}


def commit_and_push(files, message):
    subprocess.run(["git", "config", "user.name", "alexbgratz-cell"], check=False)
    subprocess.run(["git", "config", "user.email", "alexbgratz-cell@users.noreply.github.com"], check=False)
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if token and repo:
        subprocess.run(["git", "remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{repo}.git"], check=False)
    subprocess.run(["git", "add"] + files, check=False)
    r = subprocess.run(["git", "commit", "-m", message], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if "nothing to commit" in out:
        return "keine Änderung"
    for attempt in range(3):
        p = subprocess.run(["git", "push", "origin", "HEAD"], capture_output=True, text=True)
        if p.returncode == 0:
            return "gepusht"
        subprocess.run(["git", "pull", "--rebase", "-q"], check=False)
    return "push fehlgeschlagen"


def main():
    cfg = prepare_config()
    log("config vorbereitet")
    # 1) Fetch + Snapshot (fetch_new.py erledigt state/snapshot/history)
    r = subprocess.run(
        [sys.executable, os.path.join(BASE, "scanner", "fetch_new.py")],
        capture_output=True, text=True, cwd=BASE, timeout=600,
    )
    if r.returncode != 0:
        log(f"fetch_new.py fehlgeschlagen: {(r.stderr or '')[:300]}")
    out = r.stdout.strip()
    new_items = json.loads(out) if out else []
    log(f"neue Artikel: {len(new_items)}")

    if not new_items:
        # Snapshot kann sich trotzdem geändert haben -> committen
        print(commit_and_push(
            ["snapshot.json", "snapshot_history.jsonl"],
            "Update snapshot (cloud)",
        ))
        return

    try:
        token, ttype, new_refresh = get_nous_token()
    except Exception as e:
        log(f"Token-Refresh FEHLGESCHLAGEN: {str(e)[:160]} — pending bleibt, nächster Lauf übernimmt")
        print(commit_and_push(["state.json", "snapshot.json", "snapshot_history.jsonl"],
                              "Scan ohne Analyse (Token-Fehler)"))
        sys.exit(1)
    if new_refresh:
        update_secret_if_possible(new_refresh)
    with open(os.path.join(BASE, "analysis_prompt.md"), encoding="utf-8") as f:
        prompt_txt = f.read()
    snap = json.load(open(os.path.join(BASE, "snapshot.json"), encoding="utf-8"))

    batch = []
    pushed = 0
    failed = 0
    for art in new_items:
        try:
            analysis = analyze_article(prompt_txt, token, ttype, art)
        except Exception as e:
            failed += 1
            log(f"Analyse-Fehler {art.get('id', '')[:24]}: {str(e)[:160]}")
            continue
        batch.append({
            "id": art.get("id"),
            "title": art.get("title"),
            "url": art.get("url"),
            "stream": art.get("stream"),
            "source": art.get("source"),
            "published": art.get("published"),
            **analysis,
        })
        if analysis.get("score", 0) >= PUSH_THRESHOLD:
            try:
                subprocess.run(
                    [sys.executable, os.path.join(BASE, "scanner", "send_telegram.py"),
                     json.dumps(build_push(art, analysis, snap), ensure_ascii=False)],
                    capture_output=True, text=True, cwd=BASE, timeout=60,
                )
                pushed += 1
            except Exception as e:
                log(f"Telegram-Fehler: {str(e)[:160]}")
        log(f"analysiert: {analysis.get('score')}/10 {art.get('title', '')[:60]}")

    if batch:
        batch_file = os.path.join(BASE, "analysis_batch.json")
        with open(batch_file, "w", encoding="utf-8") as f:
            json.dump(batch, f, ensure_ascii=False)
        rb = subprocess.run(
            [sys.executable, os.path.join(BASE, "scanner", "record.py"), "--batch", batch_file],
            capture_output=True, text=True, cwd=BASE, timeout=120,
        )
        log(f"record.py: {(rb.stdout or '').strip()[:120]}")
        os.remove(batch_file)

    # 5) öffentliche Daten aktualisieren (Cloud: Repo-Root = data/, kein public-site/-Ordner)
    subprocess.run(
        [sys.executable, os.path.join(BASE, "scanner", "export_public.py"), "--output", "."],
        capture_output=True, text=True, cwd=BASE, timeout=300,
    )

    files = ["state.json", "snapshot.json", "snapshot_history.jsonl",
             "data/news.json", "data/stats.json", "data/history.json",
             "data/snapshot.json", "data/categories.json"]
    print(commit_and_push(files, "Update scanner state (cloud)"))
    log(f"Fertig: {len(batch)} analysiert, {pushed} gepusht, {failed} fehlgeschlagen")


if __name__ == "__main__":
    main()
