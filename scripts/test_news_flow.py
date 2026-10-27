"""End-to-end test of the news task + hint judge against the live coordinator, with fake nodes.

4 fake nodes label headlines with a mix of keyword rules and randomness, so some items reach
agreement and others go to the hint round. Prints every hint the nodes receive (to check nothing
leaks the answer). Clean up afterwards with the SQL in the run notes.
Usage:  python -I scripts/test_news_flow.py <jobs per node>
"""
import hashlib
import hmac
import importlib.util
import random
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("node", ROOT / "node" / "node.py")
node = importlib.util.module_from_spec(spec)
spec.loader.exec_module(node)
URL = "https://data-refinery.primemax.workers.dev"
CODE = (ROOT / "data" / "join_code.txt").read_text().strip()
JOBS = int(sys.argv[1]) if len(sys.argv) > 1 else 25
lock = threading.Lock()


def fake_label(title, rnd):
    t = title.lower()
    if "profit" in t and any(w in t for w in ("rise", "jump", "surge", "soar")):
        return "positive|earnings"
    if "share price today" in t:
        return rnd.choice(["neutral|price_move", "neutral|price_move", "positive|price_move"])
    return f"{rnd.choice(['neutral', 'neutral', 'positive', 'negative'])}|{rnd.choice(['other', 'other', 'analyst', 'listicle_promo'])}"


def run(name):
    rnd = random.Random(name)
    me = node.call(f"{URL}/api/register", {"name": name, "join_code": CODE})
    done = 0
    while done < JOBS:
        job = node.call(f"{URL}/api/job", {"node_id": me["node_id"], "tasks": ["news"]})
        if job.get("wait"):
            time.sleep(3)
            continue
        model, harness = node.pick_pair(["llama3.2:3b"], {"llama3.2:3b": 1}, job["used_pairs"], node.NEWS_HARNESSES)
        answer = fake_label(job["question"], rnd)
        if job.get("hint"):
            with lock:
                print(f"\n[{name}] HINT on job {job['job_id']}: {job['question'][:70]}\n   -> {job['hint'][:300]}")
        time.sleep(0.9)  # above the 3B speed floor (750 ms)
        sig = hmac.new(me["secret"].encode(), f"{job['job_id']}|{model}|{harness}|{answer}".encode(), hashlib.sha256).hexdigest()
        res = node.call(f"{URL}/api/submit", {"node_id": me["node_id"], "job_id": job["job_id"], "model": model,
                                               "harness": harness, "answer": answer, "reasoning": "fake", "sig": sig})
        if res.get("error"):
            with lock:
                print(f"[{name}] submit error: {res}")
        done += 1


threads = [threading.Thread(target=run, args=(f"test-news-{i}",)) for i in range(1, 5)]
for th in threads:
    th.start()
for th in threads:
    th.join()
print("\nfake nodes done")
