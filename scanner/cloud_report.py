#!/usr/bin/env python3
"""Cloud-Marktbericht fuer GitHub Actions. Nur Python-Stdlib.

Liest Secrets aus Umgebungsvariablen, erzeugt den KI-Marktbericht aus dem
Repo-Stand (snapshot.json + 12h-Verlauf + Top-News) und veroeffentlicht ihn
als public-site/data/market-report.json (git commit + push).

Aufrufe:
  python3 scanner/cloud_report.py           # immer Bericht erzeugen (workflow_dispatch)
  python3 scanner/cloud_report.py --slot    # nur zu 09:00 / 15:30 / 22:00 (Europe/Berlin)
  python3 scanner/cloud_report.py --ntfy    # nur wenn ntfy-Signal (Refresh-Button)
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.parse
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MODEL = "deepseek/deepseek-v4-flash"
TREND_KEYS = ["BTCUSD", "ETHUSD", "SOLUSD", "ETHBTC", "BTC.D", "F&G", "DXY", "NDX", "VIX", "US10Y"]
NTFY_TOPIC = "crypto-scanner-report-9f3k2"
# Cloudflare blockt Python-urllib als User-Agent (HTTP 1010) -> Browser-UA verwenden
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def read_json(rel, fallback):
    try:
        with open(os.path.join(BASE, rel), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return fallback


def get_nous_token():
    """Frischen access_token ueber das Portal holen (OAuth refresh).

    Endpoint + Header-Variante: {portal}/api/oauth/token mit
    x-nous-refresh-token-Header und form-urlencoded Body (so macht es Hermes).
    Gibt (access_token, token_type, neuer_refresh_token) zurueck.
    """
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
    """Rotierten refresh_token als GitHub-Secret aktualisieren (Selbstwartung).

    Braucht GH_MAINT_TOKEN (feingranuliertes PAT mit Actions-Secrets-Schreibrecht).
    Ohne PAT: gibt False zurueck, Secret muss dann manuell gepflegt werden.
    """
    maint = os.environ.get("GH_MAINT_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not maint or not repo or not new_refresh:
        return False
    p = subprocess.run(
        ["gh", "secret", "set", "NOUS_REFRESH_TOKEN", "--repo", repo],
        input=new_refresh.encode(),
        capture_output=True,
        env={**os.environ, "GH_TOKEN": maint},
    )
    return p.returncode == 0


def read_history(hours=12):
    out = []
    try:
        with open(os.path.join(BASE, "snapshot_history.jsonl"), encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    ts = rec.get("ts", "")
                    t = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except Exception:
                    continue
                if (datetime.datetime.now(datetime.timezone.utc) - t).total_seconds() <= hours * 3600:
                    out.append(rec)
    except Exception:
        pass
    return out


def generate_report():
    token, ttype, new_refresh = get_nous_token()
    if new_refresh:
        update_secret_if_possible(new_refresh)
    snap = read_json("snapshot.json", {"instruments": {}})
    inst = snap.get("instruments", {})
    history = read_history(12)
    trend = {}
    for k in TREND_KEYS:
        vals = [h.get("instruments", {}).get(k, {}).get("value") for h in history]
        vals = [v for v in vals if v is not None]
        if len(vals) >= 2:
            trend[k] = {"first": vals[0], "last": vals[-1], "punkte": len(vals)}
    st = read_json("state.json", {"articles": []})
    top = sorted(
        [a for a in st.get("articles", []) if (a.get("score") or 0) >= 7 and a.get("summary")],
        key=lambda a: a.get("published", ""),
        reverse=True,
    )[:8]
    headlines = [f"[{a.get('score')}/10 · {a.get('category', '')}] {a.get('title', '')} — {a['summary']}" for a in top]
    with open(os.path.join(BASE, "analysis_market_prompt.md"), encoding="utf-8") as f:
        prompt = f.read()
    user = (
        "Fokus: Gesamtmarkt\n"
        f"Snapshot:\n{json.dumps(inst, indent=1, ensure_ascii=False)}\n\n"
        f"Verlauf 12h (first→last):\n{json.dumps(trend, indent=1, ensure_ascii=False)}\n\n"
        "Top-Nachrichten:\n" + "\n---\n".join(headlines)
    )
    body = json.dumps({
        "model": MODEL,
        "messages": [
            {"role": "system", "content": prompt},
            {"role": "user", "content": user},
        ],
        "temperature": 0.3,
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
    text = (data["choices"][0]["message"]["content"] or "").strip()
    if not text:
        raise RuntimeError("LLM lieferte leere Antwort")
    numbers = [
        {
            "sym": s,
            "value": d.get("value"),
            "change": d.get("change_24h"),
            "label": d.get("label", ""),
            "group": d.get("group", ""),
        }
        for s, d in inst.items()
        if d.get("value") is not None or d.get("change_24h") is not None
    ]
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
    return {"ts": ts, "instrument": "ALL", "text": text, "numbers": numbers}


def publish(report):
    # Die öffentliche Seite liegt im Repo-ROOT (index.html + data/ direkt oben),
    # nicht in public-site/ — der Workflow-Checkout hat nur den Root.
    f = os.path.join(BASE, "data", "market-report.json")
    with open(f, "w", encoding="utf-8") as fh:
        json.dump({k: report[k] for k in ("ts", "instrument", "text", "numbers")}, fh, ensure_ascii=False, indent=2)
        fh.write("\n")
    subprocess.run(["git", "config", "user.name", "alexbgratz-cell"], check=False)
    subprocess.run(["git", "config", "user.email", "alexbgratz-cell@users.noreply.github.com"], check=False)
    token = os.environ.get("GITHUB_TOKEN", "")
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if token and repo:
        subprocess.run(["git", "remote", "set-url", "origin", f"https://x-access-token:{token}@github.com/{repo}.git"], check=False)
    subprocess.run(["git", "add", "data/market-report.json"], check=False)
    r = subprocess.run(["git", "commit", "-m", "Update market report (cloud)"], capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    if "nothing to commit" in out:
        return "keine Änderung (identisch)"
    p = subprocess.run(["git", "push", "origin", "HEAD"], capture_output=True, text=True)
    if p.returncode != 0:
        return f"push fehlgeschlagen: {(p.stderr or '')[:200]}"
    return "veröffentlicht"


def is_slot_time():
    now = datetime.datetime.now()  # TZ-Env Europe/Berlin
    return (now.hour, now.minute) in [(9, 0), (15, 30), (22, 0)]


def ntfy_trigger():
    url = f"https://ntfy.sh/{NTFY_TOPIC}/json?poll=1&since=5m"
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            data = json.load(r)
        msgs = data if isinstance(data, list) else [data]
        for m in msgs:
            body = (m.get("message") or "").lower()
            if "refresh" in body or "market-report" in body:
                return True
    except Exception:
        pass
    return False


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "now"
    if mode == "--slot":
        if not is_slot_time():
            print("Kein Berichts-Slot (09:00/15:30/22:00 Berlin) — übersprungen")
            return
    elif mode == "--ntfy":
        if not ntfy_trigger():
            print("Kein ntfy-Signal — übersprungen")
            return
    report = generate_report()
    print(publish(report))
    print(f"ts: {report['ts']} | text: {len(report['text'])} Zeichen | numbers: {len(report['numbers'])}")


if __name__ == "__main__":
    main()
