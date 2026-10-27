"""A pretend node for testing the coordinator without any GPU.

It follows the real protocol (register -> job -> sign -> submit) but instead of running Llama
it "answers" from the gold key with a model-dependent chance of being right.
--cheat makes it answer instantly with random numbers, to show honeypots catching it.

Usage: python -I node/fake_node.py --url http://127.0.0.1:8787 --name kaggle-1 --jobs 50 [--cheat]
"""
import argparse
import hashlib
import hmac
import json
import random
import re
import time
import urllib.request
from pathlib import Path

MODELS = ["llama3.2:1b", "llama3.2:3b", "llama3.1:8b"]
HARNESSES = ["direct", "step_by_step", "equations_first", "check_backwards", "teacher"]
ACCURACY = {"llama3.2:1b": 0.45, "llama3.2:3b": 0.75, "llama3.1:8b": 0.82}  # rough GSM8K scores
THINK_SECONDS = {"llama3.2:1b": 0.5, "llama3.2:3b": 1.0, "llama3.1:8b": 2.2}

GOLD = {}  # question -> gold answer, test-only cheat sheet so the fake node can be "sometimes right"
for line in (Path(__file__).resolve().parent.parent / "data" / "seed.sql").read_text(encoding="utf-8").splitlines():
    m = re.match(r"INSERT INTO items \(.*?\) VALUES \(\d+, '(.*)', '(.*?)', \d\);$", line)
    if m:
        GOLD[m.group(1).replace("''", "'")] = m.group(2)


def post(url, path, body):
    req = urllib.request.Request(url + path, json.dumps(body).encode(), {"content-type": "application/json"})
    with urllib.request.urlopen(req) as r:
        return json.loads(r.read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8787")
    ap.add_argument("--name", default="fake")
    ap.add_argument("--jobs", type=int, default=50)
    ap.add_argument("--cheat", action="store_true")
    args = ap.parse_args()

    me = post(args.url, "/api/register", {"name": args.name})
    print(f"[{args.name}] registered as {me['node_id'][:8]}")
    done = 0
    while done < args.jobs:
        job = post(args.url, "/api/job", {"node_id": me["node_id"]})
        if job.get("wait"):
            time.sleep(1)
            continue

        # Random (model, harness) pair, skipping pairs that already answered this item.
        used = {tuple(p) for p in job["used_pairs"]}
        free = [(m, h) for m in MODELS for h in HARNESSES if (m, h) not in used]
        model, harness = random.choice(free)

        if args.cheat:
            answer = str(random.randint(1, 500))
        else:
            time.sleep(THINK_SECONDS[model] * random.uniform(1.0, 1.4))
            gold = GOLD.get(job["question"], "0")
            right = random.random() < ACCURACY[model]
            answer = gold if right else str(int(float(gold)) + random.choice([-10, -2, -1, 1, 2, 10]))

        msg = f"{job['job_id']}|{model}|{harness}|{answer}"
        sig = hmac.new(me["secret"].encode(), msg.encode(), hashlib.sha256).hexdigest()
        post(args.url, "/api/submit", {
            "node_id": me["node_id"], "job_id": job["job_id"], "model": model, "harness": harness,
            "answer": answer, "reasoning": f"(fake {model}/{harness})", "sig": sig,
        })
        done += 1
    print(f"[{args.name}] finished {done} jobs")


if __name__ == "__main__":
    main()
