"""Planted-errors test at scale: 1,000 easy GSM8K *test* problems, 200 with a deliberately wrong label.

Same method as load_audit.py (nodes solve blind; a label is flagged when the agreed answer differs from it),
but the easiest unused problems (fewest calculation steps) so the run finishes in about 15-20 minutes.
Usage:  python scripts/load_audit1000.py
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
FIRST_ID, N, N_WRONG = 700_001, 1000, 200  # then raised to 350 planted (35%) by relabelling 150 more


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


def wrong_label(gold, rng):
    """A plausible wrong answer, the kind a careless labeller or a weak model writes."""
    options = [gold + d for d in (-10, -5, -2, -1, 1, 2, 5, 10)] + [gold * 2]
    if gold % 2 == 0:
        options.append(gold // 2)
    options = [o for o in options if o != gold and o >= 0]
    return rng.choice(options)


rng = random.Random(1000)
seen = {r["question"].strip() for r in rq([{"sql": "SELECT question FROM items", "params": []}])[0]["results"]}
rows = [json.loads(l) for l in (ROOT / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines()]
picked = []
for r in sorted(rng.sample(rows, len(rows)), key=lambda r: r["answer"].count("<<")):  # easiest first
    g = re.search(r"####\s*([-\d,\.]+)", r["answer"]).group(1).replace(",", "")
    if re.fullmatch(r"-?\d+", g) and r["question"].strip() not in seen:
        picked.append((r["question"], int(g), r["answer"].count("<<")))
    if len(picked) == N:
        break
wrong_at = set(rng.sample(range(N), N_WRONG))

sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, need, meta) VALUES ($1, $2, $3, 0, 'audit', 5, $4::jsonb) "
       "ON CONFLICT (id) DO NOTHING")
stmts = []
for i, (q, g, st) in enumerate(picked):
    planted = i in wrong_at
    stmts.append({"sql": sql, "params": [FIRST_ID + i, q, str(g), json.dumps({"label": str(wrong_label(g, rng) if planted else g),
                                                                             "planted": planted, "steps": st, "run": "1000"})]})
for st in range(0, len(stmts), 100):
    rq(stmts[st:st + 100])
print(rq([{"sql": "SELECT (meta->>'planted')::bool AS planted, COUNT(*) AS n FROM items WHERE id >= $1 GROUP BY 1",
           "params": [FIRST_ID]}])[0]["results"])
