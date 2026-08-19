"""Market snapshot: 14 crypto/tradfi/sentiment instruments (stdlib only).

Sources (verified live 11.08.2026):
  - CoinGecko simple/price   -> BTCUSD, ETHUSD, SOLUSD + 24h change
  - CoinGecko global         -> BTC.D, USDT.D dominance
  - alternative.me           -> Fear & Greed index (0-100) + classification
  - Binance fapi (public)    -> BTC funding rate (derivatives sentiment)
  - mempool.space            -> Bitcoin mempool size (on-chain activity)
  - FRED (St. Louis Fed)     -> DXY proxy (DTWEXBGS), VIX (VIXCLS),
                               S&P 500 (SP500), US 10Y yield (DGS10)
                               NOTE: no API key, daily granularity, change vs
                               previous available observation
  - Nasdaq official API      -> NDX (NASDAQ-100)
  - ETHBTC computed from ETHUSD/BTCUSD

Per-instrument failures produce null values instead of aborting the scan.
Each instrument carries a "group" for dashboard grouping.
"""
import csv
import io
import json
import time
import urllib.request

from .feeds import USER_AGENT, TIMEOUT

COINGECKO_PRICE_URL = (
    "https://api.coingecko.com/api/v3/simple/price"
    "?ids=bitcoin,ethereum,solana&vs_currencies=usd&include_24hr_change=true"
)
COINGECKO_GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
FNG_URL = "https://api.alternative.me/fng/?limit=2"
BINANCE_FUNDING_URL = "https://fapi.binance.com/fapi/v1/premiumIndex?symbol=BTCUSDT"
MEMPOOL_URL = "https://mempool.space/api/mempool"
NASDAQ_NDX_URL = "https://api.nasdaq.com/api/quote/NDX/info?assetclass=index"
BINANCE_DEPTH_URL = "https://api.binance.com/api/v3/depth?symbol=BTCUSDT&limit=100"
BINANCE_OI_URL = "https://fapi.binance.com/fapi/v1/openInterest?symbol=BTCUSDT"
COINGECKO_CHART_URL = (
    "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart"
    "?vs_currency=usd&days=35&interval=daily"
)

FRED_SERIES = {
    "DXY": "DTWEXBGS",    # Nominal Broad US Dollar Index
    "VIX": "VIXCLS",      # CBOE Volatility Index
    "SPX": "SP500",       # S&P 500
    "US10Y": "DGS10",     # 10-Year Treasury Constant Maturity Rate
}

GROUPS = {
    "BTCUSD": "Direkte Korrelation",
    "ETHUSD": "Direkte Korrelation",
    "SOLUSD": "Direkte Korrelation",
    "ETHBTC": "Kapitalrotation",
    "BTC.D": "Liquidität & Sentiment",
    "USDT.D": "Liquidität & Sentiment",
    "F&G": "Liquidität & Sentiment",
    "FUNDING": "Derivate",
    "OI": "Derivate",
    "ORDERBOOK": "Derivate",
    "MEMPOOL": "On-Chain",
    "MO30": "On-Chain",
    "DXY": "TradFi/Makro",
    "NDX": "TradFi/Makro",
    "VIX": "TradFi/Makro",
    "US10Y": "TradFi/Makro",
    "SPX": "TradFi/Makro",
}


def _get_bytes(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
        if resp.status != 200:
            raise RuntimeError(f"HTTP {resp.status}")
        return resp.read()


def _get_json(url, headers=None, retries=3):
    """GET JSON with retry/backoff for transient 429 rate limits."""
    import urllib.error
    for attempt in range(retries):
        try:
            return json.loads(_get_bytes(url, headers).decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < retries - 1:
                time.sleep(3 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def _safe(fn, default=None):
    """Run fn; return default on ANY error (network, parse, key)."""
    try:
        return fn()
    except Exception:
        return default


def _num(v):
    """Parse number that may contain US thousands separators or % suffix."""
    if v is None:
        return None
    s = str(v).replace(",", "").replace("%", "").replace("$", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _coingecko_prices():
    data = _safe(lambda: _get_json(COINGECKO_PRICE_URL), {}) or {}
    out = {}
    for coin, key in (("bitcoin", "BTCUSD"), ("ethereum", "ETHUSD"), ("solana", "SOLUSD")):
        entry = data.get(coin, {})
        price = entry.get("usd")
        chg = entry.get("usd_24h_change")
        out[key] = {
            "value": round(price, 2) if isinstance(price, (int, float)) else None,
            "change_24h": round(chg, 2) if isinstance(chg, (int, float)) else None,
        }
    return out


def _coingecko_dominance():
    data = _safe(lambda: _get_json(COINGECKO_GLOBAL_URL), {}) or {}
    pct = (data.get("data") or {}).get("market_cap_percentage") or {}
    out = {}
    for key, coin in (("BTC.D", "btc"), ("USDT.D", "usdt")):
        v = pct.get(coin)
        out[key] = {
            "value": round(v, 2) if isinstance(v, (int, float)) else None,
            "change_24h": None,  # not provided by free API
        }
    return out


def _fear_greed():
    """Fear & Greed index (0-100); change vs previous daily value."""
    def _fetch():
        data = _get_json(FNG_URL)
        entries = data.get("data") or []
        vals = [(_num(e.get("value")), e.get("value_classification")) for e in entries]
        vals = [v for v in vals if v[0] is not None]
        if not vals:
            return {"value": None, "change_24h": None, "label": None}
        cur, label = vals[0]
        change = None
        if len(vals) >= 2 and vals[1][0]:
            change = round(cur - vals[1][0], 1)
        return {"value": round(cur, 1), "change_24h": change, "label": label}
    return _safe(_fetch, {"value": None, "change_24h": None, "label": None})


def _funding_rate():
    """BTC funding rate in % (positive = longs pay shorts)."""
    def _fetch():
        data = _get_json(BINANCE_FUNDING_URL)
        rate = _num(data.get("lastFundingRate"))
        return {
            "value": round(rate * 100, 4) if rate is not None else None,
            "change_24h": None,
        }
    return _safe(_fetch, {"value": None, "change_24h": None})


def _mempool():
    """Number of unconfirmed transactions in the Bitcoin mempool."""
    def _fetch():
        data = _get_json(MEMPOOL_URL)
        return {"value": _num(data.get("count")), "change_24h": None}
    return _safe(_fetch, {"value": None, "change_24h": None})


def _orderbook():
    """BTC orderbook: bid/ask spread in bps + imbalance (spot, top 100 levels).

    value = spread in basis points (asks[0] - bids[0]) / mid * 10000
    change_24h = imbalance % = (bid_qty - ask_qty) / (bid_qty + ask_qty) * 100
    (positive = more bid liquidity = buy-side pressure)
    """
    def _fetch():
        data = _get_json(BINANCE_DEPTH_URL)
        bids = data.get("bids") or []
        asks = data.get("asks") or []
        if not bids or not asks:
            return {"value": None, "change_24h": None}
        bid_p = _num(bids[0][0])
        ask_p = _num(asks[0][0])
        mid = (bid_p + ask_p) / 2
        spread_bps = round((ask_p - bid_p) / mid * 10000, 2) if mid else None
        bid_qty = sum(_num(b[1]) or 0 for b in bids[:20])
        ask_qty = sum(_num(a[1]) or 0 for a in asks[:20])
        imb = round((bid_qty - ask_qty) / (bid_qty + ask_qty) * 100, 2) if (bid_qty + ask_qty) else None
        return {"value": spread_bps, "change_24h": imb}
    return _safe(_fetch, {"value": None, "change_24h": None})


def _open_interest():
    """BTC futures open interest (contracts) — liquidation-risk proxy.

    Rising OI + extreme funding = crowded leverage = higher liquidation risk.
    """
    def _fetch():
        data = _get_json(BINANCE_OI_URL)
        return {"value": _num(data.get("openInterest")), "change_24h": None}
    return _safe(_fetch, {"value": None, "change_24h": None})


def _monthly_return():
    """BTC return over the last ~30 days (%) from CoinGecko daily closes."""
    def _fetch():
        # market_chart is heavier -> more retries against free-tier 429s
        data = _get_json(COINGECKO_CHART_URL, retries=5)
        prices = data.get("prices") or []
        vals = [p[1] for p in prices if isinstance(p[1], (int, float))]
        if len(vals) < 25:  # need ~30 days; be lenient on missing points
            return {"value": None, "change_24h": None}
        first = vals[0]
        last = vals[-1]
        if not first:
            return {"value": None, "change_24h": None}
        return {"value": round((last - first) / first * 100, 2), "change_24h": None}
    return _safe(_fetch, {"value": None, "change_24h": None})


def _fred_series(series_id):
    """FRED daily series; change vs previous observation. No UA header!"""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}"

    def _fetch():
        # FRED blocks browser-like UAs -> send no UA header
        raw = _get_bytes(url, headers={"Accept": "text/csv"})
        rows = list(csv.reader(io.StringIO(raw.decode("utf-8"))))
        vals = [(r[0], _num(r[1])) for r in rows[1:] if len(r) >= 2 and _num(r[1]) is not None]
        if not vals:
            return {"value": None, "change_24h": None}
        last_val = vals[-1][1]
        change = None
        if len(vals) >= 2 and vals[-2][1]:
            change = round((last_val - vals[-2][1]) / vals[-2][1] * 100, 2)
        return {"value": round(last_val, 2), "change_24h": change}

    return _safe(_fetch, {"value": None, "change_24h": None})


def _nasdaq_ndx():
    def _fetch():
        data = _get_json(NASDAQ_NDX_URL, headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        })
        pd = (data.get("data") or {}).get("primaryData") or {}
        return {
            "value": _num(pd.get("lastSalePrice")),
            "change_24h": _num(pd.get("percentageChange")),
        }
    return _safe(_fetch, {"value": None, "change_24h": None})


def build_snapshot():
    """Return full snapshot dict for all 14 instruments (with groups)."""
    prices = _coingecko_prices()
    dom = _coingecko_dominance()
    fng = _fear_greed()
    funding = _funding_rate()
    mempool = _mempool()
    orderbook = _orderbook()
    oi = _open_interest()
    mo30 = _monthly_return()
    dxy = _fred_series(FRED_SERIES["DXY"])
    vix = _fred_series(FRED_SERIES["VIX"])
    spx = _fred_series(FRED_SERIES["SPX"])
    us10y = _fred_series(FRED_SERIES["US10Y"])
    ndx = _nasdaq_ndx()

    btc = prices.get("BTCUSD", {}).get("value")
    eth = prices.get("ETHUSD", {}).get("value")
    ethbtc_val = None
    ethbtc_chg = None
    if isinstance(btc, (int, float)) and isinstance(eth, (int, float)) and btc:
        ethbtc_val = round(eth / btc, 6)
        btc_chg = prices["BTCUSD"].get("change_24h")
        eth_chg = prices["ETHUSD"].get("change_24h")
        if isinstance(btc_chg, (int, float)) and isinstance(eth_chg, (int, float)):
            ethbtc_chg = round((1 + eth_chg / 100) / (1 + btc_chg / 100) * 100 - 100, 2)

    raw = {
        "BTCUSD": prices.get("BTCUSD", {"value": None, "change_24h": None}),
        "ETHUSD": prices.get("ETHUSD", {"value": None, "change_24h": None}),
        "SOLUSD": prices.get("SOLUSD", {"value": None, "change_24h": None}),
        "ETHBTC": {"value": ethbtc_val, "change_24h": ethbtc_chg},
        "BTC.D": dom.get("BTC.D", {"value": None, "change_24h": None}),
        "USDT.D": dom.get("USDT.D", {"value": None, "change_24h": None}),
        "F&G": fng,
        "FUNDING": funding,
        "OI": oi,
        "ORDERBOOK": orderbook,
        "MEMPOOL": mempool,
        "MO30": mo30,
        "DXY": dxy,
        "NDX": ndx,
        "VIX": vix,
        "US10Y": us10y,
        "SPX": spx,
    }
    # attach group + label to every instrument
    instruments = {}
    for sym, data in raw.items():
        instruments[sym] = dict(data)
        instruments[sym]["group"] = GROUPS.get(sym, "Sonstiges")
        if sym == "F&G" and data.get("label"):
            instruments[sym]["label"] = data["label"]
    return {"instruments": instruments}


if __name__ == "__main__":
    snap = build_snapshot()
    snap["timestamp"] = "test"
    print(json.dumps(snap, indent=2, ensure_ascii=False))
