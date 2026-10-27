"""Download GSM8K, pick N problems, mark ~10% as honeypots, write the D1 seed SQL.

Usage: python -I scripts/make_seed.py [N]
Writes data/seed.sql (for the coordinator) and data/test.jsonl (the exam, used in Step 6).
"""
import json
import random
import re
import sys
import urllib.request
from pathlib import Path

BASE = "https://raw.githubusercontent.com/openai/grade-school-math/master/grade_school_math/data/"
ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 1500
HONEYPOT_SHARE = 0.10


def fetch(name):
    path = DATA / name
    if not path.exists():
        print(f"downloading {name} ...")
        urllib.request.urlretrieve(BASE + name, path)
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def normalize(raw):
    # Same rules as normalize() in coordinator/src/index.js, so gold and votes compare equally.
    s = re.sub(r"[,$\s]", "", str(raw).strip()).rstrip(".")
    try:
        n = float(s)
        return str(int(n)) if n.is_integer() else str(round(n, 4))
    except ValueError:
        return s.lower()


def sql(s):
    return "'" + s.replace("'", "''") + "'"


def main():
    DATA.mkdir(exist_ok=True)
    train = fetch("train.jsonl")
    fetch("test.jsonl")

    rng = random.Random(42)  # fixed seed: the same 1,500 items every time
    picked = rng.sample(train, N)
    honeypots = set(rng.sample(range(N), int(N * HONEYPOT_SHARE)))

    lines = ["DELETE FROM votes; DELETE FROM assignments; DELETE FROM judgements; DELETE FROM items;"]
    for i, row in enumerate(picked, start=1):
        gold = normalize(row["answer"].split("####")[-1])
        lines.append(
            f"INSERT INTO items (id, question, gold, is_honeypot) VALUES ({i}, {sql(row['question'])}, {sql(gold)}, {int(i - 1 in honeypots)});"
        )
    (DATA / "seed.sql").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote data/seed.sql: {N} items, {len(honeypots)} honeypots")


if __name__ == "__main__":
    main()
