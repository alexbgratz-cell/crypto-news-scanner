#!/usr/bin/env python3
"""Cloud-Digest für GitHub Actions (nur Stdlib).

Sammelt Artikel mit Score 6-7 (noch nicht als Digest zugestellt), baut eine
Telegram-Nachricht, sendet sie und markiert die Artikel als delivered.

Aufruf: python3 scanner/cloud_digest.py
Env: TG_BOT_TOKEN, TG_CHAT_ID, GITHUB_TOKEN (Commit), NOUS_* (nur fuer
     config-Aufbau, nicht fuer LLM)
"""
import datetime
import json
import os
import subprocess
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def log(msg):
    print(f"[cloud_digest] {msg}")


def prepare_config():
    with open(os.path.join(BASE, "config.cloud.json"), encoding="utf-8") as f:
        cfg = json.load(f)
    cfg["telegram"] = {
        "bot_token": os.environ["TG_BOT_TOKEN"],
        "chat_id": os.environ["TG_CHAT_ID"],
    }
    with open(os.path.join(BASE, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def domain_of(url):
    try:
        return urllib.parse.urlparse(url).netloc
    except Exception:
        return ""


def build_message(pending):
    now = datetime.datetime.now().strftime("%d.%m. %H:%M")  # TZ-Env Europe/Berlin
    lines = [f"📋 <b>News-Digest · {now}</b>", ""]
    for i, a in enumerate(pending[:8], 1):
        lines.append(
            f"{i}. 🟠 {a.get('score')}/10 · {a.get('category', '')} · {a.get('source', '')}\n"
            f"{a.get('summary', '')}\n"
            f"🔗 {domain_of(a.get('url', ''))}"
        )
    if len(pending) > 8:
        lines.append(f"\n… und {len(pending) - 8} weitere.")
    return {"text": "\n\n".join(lines)}


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
    prepare_config()
    r = subprocess.run(
        [sys.executable, os.path.join(BASE, "scanner", "digest.py")],
        capture_output=True, text=True, cwd=BASE, timeout=120,
    )
    out = r.stdout.strip()
    pending = json.loads(out) if out else []
    log(f"Digest-Kandidaten: {len(pending)}")
    if not pending:
        print(commit_and_push(["state.json"], "Digest-Lauf (cloud) — nichts fällig"))
        return
    msg = build_message(pending)
    sp = subprocess.run(
        [sys.executable, os.path.join(BASE, "scanner", "send_telegram.py"),
         json.dumps(msg, ensure_ascii=False)],
        capture_output=True, text=True, cwd=BASE, timeout=60,
    )
    log(f"Telegram: exit {sp.returncode} {(sp.stderr or '').strip()[:120]}")
    # als digest zugestellt markieren
    ids = json.dumps([a.get("id") for a in pending], ensure_ascii=False)
    subprocess.run(
        [sys.executable, os.path.join(BASE, "scanner", "record.py"), "--digest", ids],
        capture_output=True, text=True, cwd=BASE, timeout=120,
    )
    print(commit_and_push(["state.json"], "Digest gesendet (cloud)"))
    log("Fertig")


if __name__ == "__main__":
    main()
