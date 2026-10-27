"""Load the news task into Supabase: 1,872 Indian small-cap headlines (Apr-Oct 2026) + honeypots.

Each headline becomes an item with task='news'. Nodes see only the headline, company and industry;
the measured returns (excess vs Nifty after 1/3/7 trading days) stay server-side in meta and are only
used afterwards to score the labels. Honeypots are extra COPIES of headlines whose label is
unambiguous (clear profit jump/fall, order win), so all 1,872 real headlines stay in the analysis.
Usage:  python -I scripts/load_news.py
"""
import json
import random
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NEWS = ROOT.parent / "news-agent" / "data"
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (ROOT / "data" / "supabase_rq_key.txt").read_text().strip()
FIRST_ID = 100_001  # GSM8K items use 1..1500

# Strict rules: only headlines whose label a careful reader can't dispute.
HONEYPOT_RULES = [
    ("positive|earnings", r"\b(net )?profit (jumps|surges|rises|soars|zooms|climbs|doubles)"),
    ("negative|earnings", r"\b(net )?profit (falls|declines|drops|plunges|slumps|dips)|\bloss widens|swings to (a )?loss"),
    ("positive|deal_order", r"\b(bags|wins|secures|receives) .{0,30}orders?\b"),
]
PER_RULE = 30


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


heads = json.load(open(NEWS / "ranks_600_650.json", encoding="utf-8"))
# Industry per symbol comes from the overnight-news file (the headline file doesn't carry it).
industry = {}
for day in json.load(open(NEWS / "open_ranks_600_650.json", encoding="utf-8"))["news"].values():
    for h in day:
        industry[h["symbol"]] = h.get("industry")

rows = []
for i, h in enumerate(heads):
    meta = {"symbol": h["symbol"], "company": h["company"], "industry": industry.get(h["symbol"]),
            "date": h["date"], "source": h["source"], "excess": h["excess"], "outcome": h["outcome"], "src_id": h["id"]}
    rows.append((FIRST_ID + i, h["title"], "", 0, json.dumps(meta)))

random.seed(7)
pots = []
for gold, pat in HONEYPOT_RULES:
    hits = [h for h in heads if re.search(pat, h["title"], re.I)]
    for h in random.sample(hits, min(PER_RULE, len(hits))):
        meta = {"symbol": h["symbol"], "company": h["company"], "industry": industry.get(h["symbol"]), "src_id": h["id"]}
        pots.append((h["title"], gold, json.dumps(meta)))
random.shuffle(pots)
for j, (title, gold, meta) in enumerate(pots):
    rows.append((FIRST_ID + len(heads) + j, title, gold, 1, meta))
print(f"{len(heads)} headlines, {len(pots)} honeypots")

sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, meta) VALUES ($1, $2, $3, $4, 'news', $5::jsonb) "
       "ON CONFLICT (id) DO NOTHING")
for s in range(0, len(rows), 150):
    rq([{"sql": sql, "params": list(r)} for r in rows[s:s + 150]])
print(rq([{"sql": "SELECT task, is_honeypot, COUNT(*) AS n FROM items GROUP BY task, is_honeypot ORDER BY 1, 2", "params": []}])[0]["results"])
