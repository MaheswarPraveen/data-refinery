"""Load GPQA-Diamond (198 graduate-level science questions, options A-D) as task 'gpqa'.

The file (data/gpqa_diamond.parquet) is gated on Hugging Face and is NOT committed anywhere: GPQA asks
that its questions stay out of public text so they don't leak into training data.
Usage:  python -I scripts/load_gpqa.py
"""
import json
import urllib.request
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (ROOT / "data" / "supabase_rq_key.txt").read_text().strip()
FIRST_ID = 400_001


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


df = pd.read_parquet(ROOT / "data" / "gpqa_diamond.parquet")
rows = [(FIRST_ID + i, q, str(a).strip().upper()) for i, (q, a) in enumerate(zip(df.question, df.answer))]
assert all(g in "ABCD" and len(g) == 1 for _, _, g in rows), "unexpected answer format"
sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, need) VALUES ($1, $2, $3, 0, 'gpqa', 5) "
       "ON CONFLICT (id) DO NOTHING")
for st in range(0, len(rows), 100):
    rq([{"sql": sql, "params": list(r)} for r in rows[st:st + 100]])
print(rq([{"sql": "SELECT gold, COUNT(*) AS n FROM items WHERE task = 'gpqa' GROUP BY 1 ORDER BY 1", "params": []}])[0]["results"])
