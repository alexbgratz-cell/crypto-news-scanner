#!/usr/bin/env python3
"""Einmalige Diagnose: listet die am Nous-Inference-Endpoint verfuegbaren Modelle."""
import json, os, sys, urllib.request, urllib.parse, urllib.error

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def token():
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
            "User-Agent": UA,
        },
    )
    with urllib.request.urlopen(req, timeout=30) as r:
        t = json.load(r)
    return t["access_token"], t.get("token_type", "Bearer")


tok, tt = token()
base = os.environ["NOUS_INFERENCE_BASE_URL"].rstrip("/")
for path in ("/models", "/v1/models"):
    req = urllib.request.Request(base + path, headers={"Authorization": f"{tt} {tok}", "User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)
        items = data.get("data", data) if isinstance(data, dict) else data
        ids = sorted(str(i.get("id") if isinstance(i, dict) else i) for i in items)
        print(f"ENDPOINT {path}: {len(ids)} Modelle")
        for i in ids:
            print("MODEL", i)
        sys.exit(0)
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")[:150]
        print(f"ENDPOINT {path}: HTTP {e.code} {detail}")
print("Kein Modell-Endpunkt gefunden")
sys.exit(1)
