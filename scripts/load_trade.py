"""Load the trading task: overnight Nifty 50 headlines -> BUY / SELL / SKIP, scored with fake money later.

Source: news-agent/data/open_nifty50.json. Headlines are grouped by the trading day after they came out,
with 5-minute candles from 09:15 and daily returns (incl. ^NSEI = Nifty). For each headline we store
(server-side only, never sent to nodes):
  r20   first 20 minutes: buy at the 09:15 open, sell at the 09:30 candle's close (raw %)
  ex1/ex3/ex7  return over 1/3/7 trading days from that day's open... approximated with daily close-to-close
               returns compounded from the news day, minus Nifty over the same days (excess %)
Usage:  python -I scripts/load_trade.py
"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT.parent / "news-agent" / "data" / "open_nifty50.json"
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (ROOT / "data" / "supabase_rq_key.txt").read_text().strip()
FIRST_ID = 300_001
NIFTY = "^NSEI"


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


o = json.load(open(SRC, encoding="utf-8"))
days, returns, candles = o["days"], o["returns"], o["candles"]
idx = {d: i for i, d in enumerate(days)}


def compound(symbol, start, n):
    """Compounded % return of symbol over n trading days starting at day index start, or None."""
    if start + n > len(days):
        return None
    g = 1.0
    for d in days[start:start + n]:
        r = returns.get(d, {}).get(symbol)
        if r is None:
            return None
        g *= 1 + r / 100
    return round((g - 1) * 100, 3)


def first20(day, symbol):
    bars = candles.get(day, {}).get(symbol)
    if not bars:
        return None
    by_time = {b[0]: b for b in bars}
    if "09:15" not in by_time or "09:30" not in by_time:
        return None
    return round((by_time["09:30"][4] / by_time["09:15"][1] - 1) * 100, 3)


rows, skipped = [], 0
for day in sorted(o["news"]):
    if day not in idx:
        skipped += len(o["news"][day]); continue
    i = idx[day]
    for h in o["news"][day]:
        s = h["symbol"]
        meta = {"symbol": s, "company": h["company"], "industry": h.get("industry"), "date": day,
                "ts": h.get("ts"), "source": h.get("source"), "r20": first20(day, s)}
        for n in (1, 3, 7):
            a, b = compound(s, i, n), compound(NIFTY, i, n)
            meta[f"ex{n}"] = None if a is None or b is None else round(a - b, 3)
        if meta["r20"] is None and meta["ex1"] is None:
            skipped += 1; continue
        rows.append((FIRST_ID + len(rows), h["title"], meta))

print(f"{len(rows)} headlines, {skipped} skipped (no prices)")
print("with 20-min:", sum(r[2]['r20'] is not None for r in rows), "| with 7-day:", sum(r[2]['ex7'] is not None for r in rows))
sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, meta, need) VALUES ($1, $2, '', 0, 'trade', $3::jsonb, 5) "
       "ON CONFLICT (id) DO NOTHING")
for st in range(0, len(rows), 150):
    rq([{"sql": sql, "params": [r[0], r[1], json.dumps(r[2])]} for r in rows[st:st + 150]])
print(rq([{"sql": "SELECT COUNT(*) AS n FROM items WHERE task = 'trade'", "params": []}])[0]["results"])
