#!/usr/bin/env python3
"""Simple server that proxies Kalshi API requests to avoid CORS issues."""

import http.server
import json
import urllib.request
import urllib.parse
import os
import time
import threading

PORT = int(os.environ.get("PORT", 8080))
KALSHI_API = "https://api.elections.kalshi.com/trade-api/v2/markets"
STATIC_DIR = os.path.dirname(os.path.abspath(__file__))

# Event tickers for all 35 senate races
EVENT_TICKERS = {
    'AK': 'SENATEAK-26', 'AL': 'SENATEAL-26', 'AR': 'SENATEAR-26',
    'CO': 'SENATECO-26', 'DE': 'SENATEDE-26', 'FL': 'SENATEFLS-26',
    'GA': 'SENATEGA-26', 'IA': 'SENATEIA-26', 'ID': 'SENATEID-26',
    'IL': 'SENATEIL-26', 'KS': 'SENATEKS-26', 'KY': 'SENATELA-26',
    'LA': 'KXSENATELA-26NOV', 'MA': 'SENATEMA-26', 'ME': 'SENATEME-26',
    'MI': 'SENATEMI-26', 'MN': 'SENATEMN-26', 'MS': 'SENATEMS-26',
    'MT': 'SENATEMT-26', 'NC': 'SENATENC-26', 'NE': 'SENATENE-26',
    'NH': 'SENATENH-26', 'NJ': 'SENATENJ-26', 'NM': 'SENATENM-26',
    'OH': 'SENATEOHS-26', 'OK': 'SENATEOK-26', 'OR': 'SENATEOR-26',
    'RI': 'SENATERI-26', 'SC': 'SENATESC-26', 'SD': 'SENATESD-26',
    'TN': 'SENATETN-26', 'TX': 'SENATETX-26', 'VA': 'SENATEVA-26',
    'WV': 'SENATEWV-26', 'WY': 'SENATEWY-26',
}

# Cache: {state: {demPrice, repPrice, fetchedAt}}
_cache = {}
_cache_lock = threading.Lock()
CACHE_TTL = 300  # 5 minutes


def fetch_one(state, event_ticker, retries=3):
    """Fetch market data for a single state with retries."""
    url = f"{KALSHI_API}?event_ticker={event_ticker}&limit=10"
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            dem = None
            rep = None
            others = []
            for m in data.get("markets", []):
                price = float(m.get("last_price_dollars") or m.get("yes_bid_dollars") or "0") * 100
                if m["ticker"].endswith("-D"):
                    dem = price
                elif m["ticker"].endswith("-R"):
                    rep = price
                else:
                    # Independent / third-party candidate
                    name = m.get("yes_sub_title") or m["ticker"].split("-")[-1]
                    others.append({"name": name, "price": price})
            if dem is not None and rep is None and not others:
                rep = 100 - dem
            if rep is not None and dem is None and not others:
                dem = 100 - rep
            result = {"state": state, "demPrice": dem, "repPrice": rep, "eventTicker": event_ticker}
            if others:
                result["others"] = others
            return result
        except Exception as e:
            if attempt < retries - 1:
                time.sleep(0.5 * (attempt + 1))
            else:
                print(f"  Failed {state} ({event_ticker}): {e}")
                return {"state": state, "demPrice": None, "repPrice": None, "eventTicker": event_ticker}


def fetch_all_senate_data():
    """Fetch all state data server-side with pacing to avoid rate limits."""
    now = time.time()
    with _cache_lock:
        if _cache and all(now - v.get("fetchedAt", 0) < CACHE_TTL for v in _cache.values()):
            return dict(_cache)

    print("Fetching fresh data from Kalshi...")
    results = {}
    states = list(EVENT_TICKERS.items())
    batch_size = 5
    for i in range(0, len(states), batch_size):
        batch = states[i:i + batch_size]
        for state, evt in batch:
            result = fetch_one(state, evt)
            result["fetchedAt"] = time.time()
            results[state] = result
        if i + batch_size < len(states):
            time.sleep(0.4)

    with _cache_lock:
        _cache.update(results)
    print(f"Loaded {sum(1 for v in results.values() if v['demPrice'] is not None)}/{len(results)} states")
    return results


# Control / balance of power markets
CONTROL_EVENTS = {
    "senate": "CONTROLS-2026",
    "house": "CONTROLH-2026",
    "balance": "KXBALANCEPOWERCOMBO-27FEB",
}

_control_cache = {}
_control_cache_lock = threading.Lock()


def fetch_control_data():
    """Fetch senate control, house control, and balance of power markets."""
    now = time.time()
    with _control_cache_lock:
        if _control_cache and now - _control_cache.get("_fetchedAt", 0) < CACHE_TTL:
            return dict(_control_cache)

    print("Fetching control/balance markets...")
    results = {}
    for key, evt in CONTROL_EVENTS.items():
        url = f"{KALSHI_API}?event_ticker={evt}&limit=10"
        markets = {}
        for attempt in range(3):
            try:
                req = urllib.request.Request(url, headers={"Accept": "application/json"})
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read())
                for m in data.get("markets", []):
                    ticker = m["ticker"]
                    suffix = ticker.split("-")[-1]  # D, R, DD, RR, DR, RD
                    price = float(m.get("last_price_dollars") or "0") * 100
                    label = m.get("yes_sub_title") or suffix
                    markets[suffix] = {"price": price, "label": label, "ticker": ticker}
                break
            except Exception as e:
                if attempt < 2:
                    time.sleep(1 * (attempt + 1))
                else:
                    print(f"  Failed {key}: {e}")
        results[key] = {"markets": markets, "eventTicker": evt}
        time.sleep(0.5)

    # Featured primary races - fetch specific candidate tickers
    FEATURED_RACES = [
        {
            "key": "ca11_wiener",
            "label": "Scott Wiener, CA-11",
            "ticker": "KXCA11PRIMARY-26-SWIE",
            "series": "kxca11primary",
        },
        {
            "key": "co08_rutinel",
            "label": "Manny Rutinel, CO-08",
            "ticker": "KXCO8D-26-MRUT",
            "series": "kxco8d",
        },
        {
            "key": "ny12_bores",
            "label": "Alex Bores, NY-12",
            "ticker": "KXNY12D-26-ABOR",
            "series": "kxny12d",
        },
    ]
    featured = []
    for race in FEATURED_RACES:
        try:
            url = f"{KALSHI_API}?tickers={race['ticker']}&limit=1"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            markets = data.get("markets", [])
            price = float(markets[0].get("last_price_dollars") or "0") * 100 if markets else None
            featured.append({
                "label": race["label"],
                "price": price,
                "series": race["series"],
            })
        except Exception as e:
            print(f"  Failed featured {race['key']}: {e}")
            featured.append({"label": race["label"], "price": None, "series": race["series"]})
        time.sleep(0.3)
    results["featured"] = featured

    # CA Governor - top 5 candidates
    try:
        url = f"{KALSHI_API}?series_ticker=KXGOVCA&limit=50"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        candidates = []
        for m in data.get("markets", []):
            price = float(m.get("last_price_dollars") or "0") * 100
            candidates.append({
                "name": m.get("yes_sub_title") or m["ticker"],
                "price": price,
            })
        candidates.sort(key=lambda c: -c["price"])
        results["caGov"] = {"candidates": candidates[:5], "series": "kxgovca"}
    except Exception as e:
        print(f"  Failed CA Gov: {e}")
        results["caGov"] = {"candidates": [], "series": "kxgovca"}

    results["_fetchedAt"] = time.time()
    with _control_cache_lock:
        _control_cache.update(results)
    return results


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=STATIC_DIR, **kwargs)

    def do_GET(self):
        if self.path == "/api/senate":
            self.send_json(fetch_all_senate_data())
        elif self.path == "/api/control":
            self.send_json(fetch_control_data())
        else:
            super().do_GET()

    def send_json(self, data):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def log_message(self, format, *args):
        msg = format % args
        if "/api/" in msg or "error" in msg.lower():
            super().log_message(format, *args)


if __name__ == "__main__":
    print(f"Starting server at http://localhost:{PORT}")
    print(f"Open http://localhost:{PORT} in your browser")
    # Pre-fetch data at startup with pause between to avoid rate limits
    fetch_all_senate_data()
    time.sleep(2)
    fetch_control_data()
    server = http.server.HTTPServer(("", PORT), Handler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
