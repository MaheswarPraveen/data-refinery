"""Quick-reasoning refinement run: the 500 hardest fresh GSM8K *train* problems (most solution steps), voted by
the 10 model families (gsm8k_mixed). Feeds the next fine-tune and its DPO pairs.

Skips every question already in the refinery, so nothing is voted twice. Test problems are never loaded here,
so the fine-tune test stays unseen.
Usage:  python scripts/load_quick.py
"""
import json
import random
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (ROOT / "data" / "supabase_rq_key.txt").read_text().strip()
FIRST_ID, N = 600_001, 500


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


seen = {r["question"].strip() for r in rq([{"sql": "SELECT question FROM items", "params": []}])[0]["results"]}
rows = [json.loads(l) for l in (ROOT / "data" / "train.jsonl").read_text(encoding="utf-8").splitlines()]
picked = []
steps = lambda r: r["answer"].count("<<")       # calculator steps in the official solution = reasoning depth
for r in sorted(random.Random(7).sample(rows, len(rows)), key=steps, reverse=True):
    m = re.search(r"####\s*([-\d,\.]+)", r["answer"])
    g = m and m.group(1).replace(",", "")
    if g and re.fullmatch(r"-?\d+", g) and r["question"].strip() not in seen:
        picked.append((r["question"], g, steps(r)))
    if len(picked) == N:
        break

sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, need, meta) VALUES ($1, $2, $3, 0, 'gsm8k_mixed', 5, $4::jsonb) "
       "ON CONFLICT (id) DO NOTHING")
stmts = [{"sql": sql, "params": [FIRST_ID + i, q, g, json.dumps({"run": "quick", "steps": s})]} for i, (q, g, s) in enumerate(picked)]
stmts.insert(0, {"sql": "DELETE FROM items WHERE id >= $1 AND NOT EXISTS (SELECT 1 FROM votes v WHERE v.item_id = items.id)", "params": [FIRST_ID]})
stmts.append({"sql": "UPDATE items SET status = 'parked' WHERE status = 'pending' AND id < $1", "params": [FIRST_ID]})
for st in range(0, len(stmts), 100):
    rq(stmts[st:st + 100])
print(rq([{"sql": "SELECT task, status, COUNT(*) AS n, MIN((meta->>'steps')::int) AS min_steps FROM items WHERE id >= $1 GROUP BY 1, 2", "params": [FIRST_ID]}])[0]["results"])
