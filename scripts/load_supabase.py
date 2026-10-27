"""Load the seeded items (data/seed.sql) into the Supabase database through the guarded rq() function.

Values go as parameters, never pasted into SQL: questions contain "$0.50", which must not be read
as placeholders.  Usage:  python -I scripts/load_supabase.py
"""
import json
import re
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "data"
URL = "https://eqaucajerezzkojjwnga.supabase.co/rest/v1/rpc/rq"
ANON = "sb_publishable_yYTq3hR4IyCgaukHpEkDSA_OC1XZC45"  # public key; tables are locked, rq() needs the secret
KEY = (DATA / "supabase_rq_key.txt").read_text().strip()
ROW = re.compile(r"VALUES \((\d+), '((?:[^']|'')*)', '((?:[^']|'')*)', (\d)\);")


def rq(stmts):
    req = urllib.request.Request(URL, json.dumps({"k": KEY, "stmts": stmts}).encode(),
                                 {"content-type": "application/json", "apikey": ANON})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.loads(r.read())


rows = [(int(i), q.replace("''", "'"), g.replace("''", "'"), int(h))
        for i, q, g, h in ROW.findall((DATA / "seed.sql").read_text(encoding="utf-8"))]
print(len(rows), "items parsed")
sql = "INSERT INTO items (id, question, gold, is_honeypot) VALUES ($1, $2, $3, $4) ON CONFLICT (id) DO NOTHING"
for start in range(0, len(rows), 150):
    rq([{"sql": sql, "params": list(r)} for r in rows[start:start + 150]])
print(rq([{"sql": "SELECT COUNT(*) AS n, SUM(is_honeypot) AS hp FROM items", "params": []}]))
