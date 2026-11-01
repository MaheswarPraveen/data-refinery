"""Planted-errors test: can the refinery find the wrong labels in a dirty dataset?

Takes 200 GSM8K *test* problems (never used in earlier runs), keeps the official answer as gold, and gives
each one a "label" as if it came from a scraped dataset: 160 correct, 40 deliberately wrong (plausible
slips: off by a step, doubled, halved, a near miss). Nodes never see the label; they solve the problem
blind, so they can't just copy it. A label is flagged when the network's agreed answer differs from it.
Usage:  python scripts/load_audit.py
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
FIRST_ID, N, N_WRONG = 500_001, 200, 40  # then trimmed to 80 good + 20 planted (first by id) for a ~15 min run


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


rng = random.Random(42)
rows = [json.loads(l) for l in (ROOT / "data" / "test.jsonl").read_text(encoding="utf-8").splitlines()]
picked = []
for r in rng.sample(rows, len(rows)):
    m = re.search(r"####\s*([-\d,\.]+)", r["answer"])
    g = m and m.group(1).replace(",", "")
    if g and re.fullmatch(r"-?\d+", g):
        picked.append((r["question"], int(g)))
    if len(picked) == N:
        break
wrong_at = set(rng.sample(range(N), N_WRONG))

sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, need, meta) VALUES ($1, $2, $3, 0, 'audit', 5, $4::jsonb) "
       "ON CONFLICT (id) DO NOTHING")
stmts = []
for i, (q, g) in enumerate(picked):
    planted = i in wrong_at
    label = wrong_label(g, rng) if planted else g
    stmts.append({"sql": sql, "params": [FIRST_ID + i, q, str(g), json.dumps({"label": str(label), "planted": planted})]})
for st in range(0, len(stmts), 100):
    rq(stmts[st:st + 100])
print(rq([{"sql": "SELECT (meta->>'planted')::bool AS planted, COUNT(*) AS n FROM items WHERE task = 'audit' GROUP BY 1", "params": []}])[0]["results"])
