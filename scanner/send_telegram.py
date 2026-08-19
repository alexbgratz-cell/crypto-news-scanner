"""send_telegram.py — send a formatted message via Telegram Bot API.

Usage:
    python3 send_telegram.py '<json>'
        json: {"text": "<HTML-formatted message>"}
    python3 send_telegram.py --test          # send a simple test message
    python3 send_telegram.py --chat-id       # print configured chat id

Reads bot_token + chat_id from config.json. Exit 0 on ok:true, 1 otherwise.
"""
import json
import os
import sys
import urllib.parse
import urllib.request

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def send(text):
    cfg = load_config()
    token = (cfg.get("telegram") or {}).get("bot_token") or ""
    chat_id = (cfg.get("telegram") or {}).get("chat_id") or ""
    if not token or not chat_id:
        print("ERROR: telegram.bot_token/chat_id not configured in config.json", file=sys.stderr)
        return 1
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if data.get("ok"):
            return 0
        print(f"ERROR: telegram returned ok=false: {data}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"ERROR: telegram send failed: {e}", file=sys.stderr)
        return 1


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    if sys.argv[1] == "--test":
        sys.exit(send("✅ <b>Scanner-Test</b> — Telegram-Zustellung funktioniert."))
    if sys.argv[1] == "--chat-id":
        cfg = load_config()
        print((cfg.get("telegram") or {}).get("chat_id") or "(leer)")
        sys.exit(0)
    payload = json.loads(sys.argv[1])
    sys.exit(send(payload["text"]))


if __name__ == "__main__":
    main()
