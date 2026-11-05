"""Loads raw agent requests (data/agent/requests.jsonl, no answers) into the refinery as task 'agent'.

Each request gets 5 independent votes (one tool call each); the network's consensus becomes the label.
Safe to rerun as more requests are generated: existing ids are skipped.
Usage:  python scripts/load_agent.py
"""
import json
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (ROOT / "data" / "supabase_rq_key.txt").read_text().strip()
FIRST_ID = 800_000


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())


rows = [json.loads(l) for l in (ROOT / "data" / "agent" / "requests.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
sql = ("INSERT INTO items (id, question, gold, is_honeypot, task, need, meta) VALUES ($1, $2, '', 0, 'agent', 5, $3::jsonb) "
       "ON CONFLICT (id) DO NOTHING")
stmts = [{"sql": sql, "params": [FIRST_ID + r["id"], r["request"], json.dumps({"kind": r.get("kind"), "context": r.get("context")})]}
         for r in rows]
for i in range(0, len(stmts), 100):
    rq(stmts[i:i + 100])
print(rq([{"sql": "SELECT status, COUNT(*) AS n FROM items WHERE task = 'agent' GROUP BY 1", "params": []}])[0]["results"])
