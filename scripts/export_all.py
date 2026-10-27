"""Export everything the fine-tune needs from Supabase (via the guarded rq() function).

Writes to data/:
  items.jsonl  one line per item: question, gold, status, final_answer, resolved_by, is_honeypot
  votes.jsonl  one line per vote: item, node, model, harness, answer, reasoning, server_ms
These two files are enough to build every training set (consensus, raw, single-model, random, gold).
Usage:  python -I scripts/export_all.py
"""
import json
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"
KEY = (DATA / "supabase_rq_key.txt").read_text().strip()


def rq(sql, params=()):
    body = json.dumps({"k": KEY, "stmts": [{"sql": sql, "params": list(params)}]}).encode()
    req = urllib.request.Request(URL, body, {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.loads(r.read())[0]["results"]


def dump(name, sql, page=1000):
    rows, last = [], 0
    while True:  # keyset pages, so no single response gets huge
        chunk = rq(sql + " WHERE id > $1 ORDER BY id LIMIT $2", (last, page))
        if not chunk:
            break
        rows += chunk
        last = chunk[-1]["id"]
    with open(DATA / name, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(rows))


dump("items.jsonl", "SELECT id, question, gold, is_honeypot, status, final_answer, score, resolved_by FROM items")
dump("votes.jsonl", "SELECT id, item_id, node_id, model, harness, answer, reasoning, server_ms, too_fast FROM votes")
