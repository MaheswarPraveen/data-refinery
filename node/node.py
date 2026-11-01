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
GPQA_HARNESSES = {
    "direct": "Answer this science question. Keep the reasoning short.",
    "step_by_step": "Answer this science question. Think step by step, writing out each step.",
    "principles_first": "Answer this science question. First name the principles or formulas that apply, then use them.",
    "eliminate": "Answer this science question by ruling out wrong options one by one, giving a reason for each.",
    "professor": "You are a university professor in this field. Explain the reasoning to a student, then give the answer.",
}
# Plain text, not forced JSON: grammar-constrained output made 1B/3B models emit junk
# ("aepb", "}") or ramble until timeout. Same fix that made the judge reliable.
FORMAT = "Show brief working: a few short lines, one calculation per line, no restating the problem. Then end with one final line exactly like this: ANSWER: <number>"
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


TRADE_GUIDE = (
    "Decide what to do at the next market open, based only on this overnight headline:\n"
    "BUY = you expect the stock to beat the market over the next days; SELL = you expect it to lag or fall; "
    "SKIP = no clear edge (old news, not material, rumour, or a recap)."
)
TRADE_FORMAT = "Explain briefly, then end with exactly one line: DECISION: <BUY|SELL|SKIP>"


GPQA_FORMAT = ("This is a graduate-level science multiple-choice question. Reason step by step, concisely, "
               "then end with exactly one line: ANSWER: <A|B|C|D>")


def parse_letter(text):
    """A-D from the last ANSWER: line, or None."""
    # Lenient: "ANSWER: B", "**Answer:** (B)", "Answer**: B", "the answer is B", "Answer: Option C", "\boxed{B}".
    hits = re.findall(r"(?i:ANSWER\W{0,6}(?:IS\W{0,3})?(?:OPTION\s*)?)\(?\**([A-D])\b", text)  # capital letter only
    hits = hits or re.findall(r"boxed\{\s*\(?([A-D])\)?\s*\}", text)
    return hits[-1].upper() if hits else None


def parse_trade(text):
    """BUY / SELL / SKIP from the last DECISION: line, or None."""
    hits = re.findall(r"DECISION\s*:\s*\**\s*(BUY|SELL|SKIP)", text, re.I)
    return hits[-1].upper() if hits else None


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


def pick_pair(models, weights, used, harnesses=HARNESSES, diverse=False):
    """Random weighted pair that hasn't answered this item yet.
    diverse=True (mixed-family runs): prefer a model that hasn't voted on this item, so 3 votes
    come from 3 different families. Off by default: with unequal weights it forced the slow 8B
    onto every item that already had 1B and 3B votes."""
    used = {tuple(p) for p in used}
    allowed = models
    if diverse:
        fresh = [m for m in models if m not in {u[0] for u in used}]
        allowed = fresh or models
    pairs = [(m, h) for m in allowed for h in harnesses if (m, h) not in used]
    if pairs:
        return random.choices(pairs, weights=[weights[m] for m, _ in pairs])[0]
    return random.choice(models), random.choice(list(harnesses))


NO_THINK_OPTION = set()  # models that reject Ollama's "think" option


def solve(ollama, model, harness, job):
    """One vote. The job says the task; a hint is present only in the re-check round after a disagreement."""
    news = job.get("task") == "news"
    trade = job.get("task") == "trade"
    if job.get("task") == "gpqa":
        system = f"{GPQA_HARNESSES[harness]} {GPQA_FORMAT}"
        user = job["question"]
    elif trade:
        ctx = job.get("context") or {}
        system = f"{NEWS_HARNESSES[harness]}\n{TRADE_GUIDE}\n{TRADE_FORMAT}"
        user = f"Company: {ctx.get('company', '?')} ({ctx.get('industry') or 'industry unknown'}), NSE\nOvernight headline: {job['question']}"
    elif news:
        ctx = job.get("context") or {}
        system = f"{NEWS_HARNESSES[harness]}\n{NEWS_GUIDE}\n{NEWS_FORMAT}"
        user = f"Company: {ctx.get('company', '?')} ({ctx.get('industry') or 'industry unknown'})\nHeadline: {job['question']}"
    else:
        system = f"{HARNESSES[harness]} {FORMAT}"
        user = job["question"]
    # Hunyuan ignores Ollama's think option but obeys a "/no_think" tag (otherwise it reasons past the time limit).
    if "hunyuan" in model.lower():
        user = "/no_think " + user
    if job.get("hint"):
        user += f"\n\nOther solvers disagreed on this one. A reviewer's hint (it does not give the answer): {job['hint']}"
    body = {
        "model": model,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1000 if job.get("task") == "gpqa" else 512 if job.get("task") in ("gsm8k", "gsm8k_mixed") else 768},  # cap length so a rambling model can't stall the node
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
    }
    # Many small models can "think" at length first (Qwen 3.x, Gemma 4, Granite 4.x, Nemotron, Hunyuan,
    # SmolLM3...): slow, and the 768-token cap cuts them off. Ask every model for a direct answer; a model
    # that rejects the option is remembered and asked plainly from then on.
    if model not in NO_THINK_OPTION:
        body["think"] = False
    tmo = 270 if job.get("task") == "gpqa" else 180  # long science answers; still inside the 300 s lease
    try:
        res = post(f"{ollama}/api/chat", body, timeout=tmo)
    except urllib.error.HTTPError as e:
        if e.code not in (400, 500) or "think" not in body:  # some models answer the option with a 500
            raise
        NO_THINK_OPTION.add(model)
        body.pop("think")
        res = post(f"{ollama}/api/chat", body, timeout=tmo)
    # Some models still inline their thinking; drop it so only the worked answer is kept and parsed.
    text = re.sub(r"<think>.*?</think>", "", res["message"]["content"], flags=re.S).strip()
    answer = parse_letter(text) if job.get("task") == "gpqa" else parse_trade(text) if trade else parse_news(text) if news else parse(text)
    if answer is None and job.get("task") == "gpqa":
        # Reasoned but never wrote the ANSWER line (rambled or got cut off): ask once for just the letter.
        follow = dict(body, messages=body["messages"] + [{"role": "assistant", "content": text[-3000:]},
                      {"role": "user", "content": "Based on your reasoning, reply with only the letter of your final answer: A, B, C or D."}],
                      options={"temperature": 0, "num_predict": 20})
        try:
            extra = re.sub(r"<think>.*?</think>", "", post(f"{ollama}/api/chat", follow, timeout=60)["message"]["content"], flags=re.S).strip()
            m = re.search(r"\b([A-D])\b", extra)
            if m:
                answer, text = m.group(1), f"{text}\n\nANSWER: {m.group(1)}"
        except Exception:
            pass
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
    ap.add_argument("--tasks", default="gsm8k,news", help="which refinery tasks this node accepts")
    ap.add_argument("--diverse", action="store_true", help="each item gets votes from different models (mixed-family runs)")
    args = ap.parse_args()

    url = args.url.rstrip("/")
    models = args.models.split(",")
    weights = dict(zip(models, map(float, args.weights.split(","))))
    me = identity(args)
    print(f"[{args.name}] node {me['node_id'][:8]} | models {models}")

    done = 0
    while args.jobs == 0 or done < args.jobs:
        job = call(f"{url}/api/job", {"node_id": me["node_id"], "tasks": args.tasks.split(",")})
        if "error" in job:
            raise SystemExit(f"coordinator refused: {job}")
        if job.get("wait"):
            time.sleep(job.get("retry_after", 10))
            continue

        harnesses = NEWS_HARNESSES if job.get("task") in ("news", "trade") else GPQA_HARNESSES if job.get("task") == "gpqa" else HARNESSES
        model, harness = pick_pair(models, weights, job["used_pairs"], harnesses, args.diverse)
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
