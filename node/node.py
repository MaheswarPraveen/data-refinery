"""A real refinery node: asks the coordinator for jobs and solves them with Llama via Ollama.

Each job gets a fresh random (model, harness) pair. Models are weighted so the 8B
(also our fine-tune target) votes less often: keeps the weak-to-strong story honest.

Usage (inside Kaggle/Colab, after Ollama is running):
  python node.py --url https://data-refinery.<you>.workers.dev --name kaggle-1
"""
import argparse
import hashlib
import hmac
import json
import random
import re
import time
import urllib.error
import urllib.request
from pathlib import Path

# Same task, five ways of instructing it. Different instructions make different mistakes,
# which is what makes consensus meaningful.
HARNESSES = {
    "direct": "Solve this math word problem. Keep the reasoning short.",
    "step_by_step": "Solve this math word problem. Think step by step, writing each calculation.",
    "equations_first": "Solve this math word problem. First write the equations that describe it, then solve them.",
    "check_backwards": "Solve this math word problem, then check your answer by working backwards from it. Fix it if the check fails.",
    "teacher": "You are a patient maths teacher. Explain how to solve this word problem to a student, then give the result.",
}
# Plain text, not forced JSON: grammar-constrained output made 1B/3B models emit junk
# ("aepb", "}") or ramble until timeout. Same fix that made the judge reliable.
FORMAT = "Show your working, then end with one final line exactly like this: ANSWER: <number>"
ANSWER_RE = re.compile(r"ANSWER:\s*\$?\s*(-?[\d,]*\.?\d+)", re.I)
NUMBER_RE = re.compile(r"-?\d[\d,]*\.?\d*")


def parse(text):
    """Number from the last 'ANSWER:' line; else the last number written; else None (vote is skipped)."""
    hits = ANSWER_RE.findall(text) or NUMBER_RE.findall(text)
    return hits[-1].rstrip(".") if hits else None


# ---- news task: label a market headline (sentiment + category). Prices are never shown. ----
NEWS_HARNESSES = {
    "direct": "Label this news headline about an Indian listed company.",
    "trader": "You are a skeptical equity trader in India. Judge what this headline really says about the company, ignoring hype.",
    "analyst": "You are a sell-side equity analyst. Classify this headline the way you would in a research note.",
    "plain_reader": "Read this headline the way an ordinary investor would, and label it.",
    "checklist": "Label this headline by checking, in order: is it about this company itself, is it a fact or an opinion, is it good or bad for the company.",
}
NEWS_GUIDE = (
    "SENTIMENT for this company: positive (good news for the company), negative (bad news), "
    "neutral (factual, mixed, or not really about the company).\n"
    "CATEGORY, pick one: earnings (results, profit, revenue), analyst (ratings, targets, recommendations), "
    "price_move (share price recap, gainers/losers, market update), deal_order (orders, contracts, partnerships, "
    "acquisitions), corporate_action (dividend, bonus, split, buyback, fundraise, listing), regulatory_legal "
    "(regulator, court, tax, approvals, penalties), management (appointments, resignations, insider or promoter "
    "activity), listicle_promo (lists of stocks to buy, promotional or SEO pages), other."
)
NEWS_FORMAT = "Explain briefly, then end with exactly two lines:\nSENTIMENT: <positive|negative|neutral>\nCATEGORY: <one category>"
NEWS_LABELS = (
    ("positive", "negative", "neutral"),
    ("earnings", "analyst", "price_move", "deal_order", "corporate_action", "regulatory_legal", "management", "listicle_promo", "other"),
)


def parse_news(text):
    """'sentiment|category' from the last SENTIMENT:/CATEGORY: lines, or None if either is missing/invalid."""
    out = []
    for key, labels in zip(("SENTIMENT", "CATEGORY"), NEWS_LABELS):
        hits = re.findall(rf"{key}\s*:\s*\**\s*([A-Za-z_ \-/]+)", text, re.I)
        if not hits:
            return None
        word = re.sub(r"[\s\-/]+", "_", hits[-1].strip().lower()).strip("_")
        match = next((l for l in labels if word.startswith(l) or l.startswith(word)), None)
        if not match:
            return None
        out.append(match)
    return "|".join(out)


def post(url, body, timeout=60):
    # Cloudflare rejects urllib's default User-Agent (error 1010), so name ourselves.
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"content-type": "application/json", "user-agent": "data-refinery-node/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call(url, body, timeout=60):
    """POST that never gives up. Network drops back off up to a minute; a 503 from the coordinator
    (database unavailable, e.g. daily free limit used up) means pause for the time it asks, then retry."""
    attempt = 0
    while True:
        try:
            return post(url, body, timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 409, 422):
                return {"error": e.code, "detail": e.read().decode(errors="replace")}
            if e.code == 503:
                try:
                    wait = int(json.loads(e.read()).get("retry_after", 300))
                except Exception:
                    wait = 300
                print(f"coordinator paused (database unavailable), retrying in {wait}s", flush=True)
                time.sleep(wait)
                continue
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            err = e
        attempt += 1
        if attempt % 10 == 0:
            print(f"still can't reach {url}: {err}", flush=True)
        time.sleep(min(60, 2 ** min(attempt, 6)))


def pick_pair(models, weights, used, harnesses=HARNESSES):
    """Random weighted pair that hasn't answered this item yet.
    (It used to prefer an unused *model* first, which forced the slow 8B onto every item that
    already had 1B and 3B votes: about a third of all votes instead of ~1 in 8.)"""
    used = {tuple(p) for p in used}
    pairs = [(m, h) for m in models for h in harnesses if (m, h) not in used]
    if pairs:
        return random.choices(pairs, weights=[weights[m] for m, _ in pairs])[0]
    return random.choice(models), random.choice(list(harnesses))


def solve(ollama, model, harness, job):
    """One vote. The job says the task; a hint is present only in the re-check round after a disagreement."""
    news = job.get("task") == "news"
    if news:
        ctx = job.get("context") or {}
        system = f"{NEWS_HARNESSES[harness]}\n{NEWS_GUIDE}\n{NEWS_FORMAT}"
        user = f"Company: {ctx.get('company', '?')} ({ctx.get('industry') or 'industry unknown'})\nHeadline: {job['question']}"
    else:
        system = f"{HARNESSES[harness]} {FORMAT}"
        user = job["question"]
    if job.get("hint"):
        user += f"\n\nOther solvers disagreed on this one. A reviewer's hint (it does not give the answer): {job['hint']}"
    res = post(f"{ollama}/api/chat", {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 768},  # cap length so a rambling model can't stall the node
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }, timeout=180)
    text = res["message"]["content"]
    answer = parse_news(text) if news else parse(text)
    if answer is None:
        raise ValueError("no valid answer in reply")
    return text[:4000], answer


def identity(args):
    """Register once and remember it, so a restarted notebook rejoins as the same node."""
    path = Path(f"{args.name}.node.json")
    if path.exists():
        return json.loads(path.read_text())
    me = call(f"{args.url}/api/register", {"name": args.name, "join_code": args.join_code})
    if "error" in me:
        raise SystemExit(f"register failed: {me}")
    path.write_text(json.dumps(me))
    return me


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="coordinator URL")
    ap.add_argument("--name", required=True, help="e.g. kaggle-1")
    ap.add_argument("--join-code", default="")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--models", default="llama3.2:1b,llama3.2:3b,llama3.1:8b")
    ap.add_argument("--weights", default="1,1,0.3", help="how often each model is picked")
    ap.add_argument("--jobs", type=int, default=0, help="stop after N jobs (0 = run forever)")
    args = ap.parse_args()

    url = args.url.rstrip("/")
    models = args.models.split(",")
    weights = dict(zip(models, map(float, args.weights.split(","))))
    me = identity(args)
    print(f"[{args.name}] node {me['node_id'][:8]} | models {models}")

    done = 0
    while args.jobs == 0 or done < args.jobs:
        job = call(f"{url}/api/job", {"node_id": me["node_id"], "tasks": ["gsm8k", "news"]})
        if "error" in job:
            raise SystemExit(f"coordinator refused: {job}")
        if job.get("wait"):
            time.sleep(job.get("retry_after", 10))
            continue

        harnesses = NEWS_HARNESSES if job.get("task") == "news" else HARNESSES
        model, harness = pick_pair(models, weights, job["used_pairs"], harnesses)
        t = time.time()
        try:
            reasoning, answer = solve(args.ollama, model, harness, job)
        except Exception as e:  # bad JSON or Ollama hiccup: skip; the lease expires and someone else takes it
            print(f"[{args.name}] job {job['job_id']} failed on {model}: {e}")
            continue

        sig = hmac.new(me["secret"].encode(), f"{job['job_id']}|{model}|{harness}|{answer}".encode(), hashlib.sha256).hexdigest()
        res = call(f"{url}/api/submit", {
            "node_id": me["node_id"], "job_id": job["job_id"], "model": model, "harness": harness,
            "answer": answer, "reasoning": reasoning, "sig": sig,
        })
        done += 1
        print(f"[{args.name}] #{done} job {job['job_id']} {model}/{harness} -> {answer} ({time.time() - t:.1f}s) {res.get('error', '')}")


if __name__ == "__main__":
    main()
