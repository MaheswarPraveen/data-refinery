"""Judge node: writes hints for problems the voters could not agree on, with Meta's Muse Glimmer.

It never sees the votes' answers as instructions to repeat: the coordinator builds the prompt and
blanks out any candidate answer from the hint before voters see it.
Usage:  python judge.py --url https://data-refinery.<you>.workers.dev --name judge-glimmer --join-code X
"""
import argparse
import hashlib
import hmac
import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path


def post(url, body, timeout=60):
    req = urllib.request.Request(url, json.dumps(body).encode(),
                                 {"content-type": "application/json", "user-agent": "data-refinery-node/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def call(url, body, timeout=60):
    """POST that never gives up (network drops, coordinator pauses)."""
    while True:
        try:
            return post(url, body, timeout)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403, 409, 422):
                return {"error": e.code, "detail": e.read().decode(errors="replace")}
            time.sleep(30 if e.code == 503 else 10)
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(10)


def identity(args):
    path = Path(f"{args.name}.node.json")
    if path.exists():
        return json.loads(path.read_text())
    me = call(f"{args.url}/api/register", {"name": args.name, "join_code": args.join_code})
    if "error" in me:
        raise SystemExit(f"register failed: {me}")
    path.write_text(json.dumps(me))
    return me


def chat(ollama, model, prompt, think_off=True):
    body = {"model": model, "stream": False, "options": {"temperature": 0.2, "num_predict": 1200},
            "messages": [{"role": "user", "content": prompt}]}
    if think_off:
        body["think"] = False
    try:
        res = post(f"{ollama}/api/chat", body, timeout=300)
    except urllib.error.HTTPError as e:
        if e.code not in (400, 500) or not think_off:
            raise
        body.pop("think")
        res = post(f"{ollama}/api/chat", body, timeout=300)
    msg = res.get("message", {})
    return re.sub(r"<think>.*?</think>", "", msg.get("content") or "", flags=re.S).strip(), (msg.get("thinking") or "").strip()


def ask(ollama, model, prompt):
    """Muse Glimmer is a reasoning model: it can spend the whole budget thinking and return no answer.
    Try a plain request, then the /no_think tag, then fall back to the end of its reasoning
    (the coordinator still blanks out any candidate answer before voters see the hint)."""
    content, thinking = chat(ollama, model, prompt)
    if len(content) >= 10:
        return content
    content, thinking2 = chat(ollama, model, "/no_think\n" + prompt)
    if len(content) >= 10:
        return content
    tail = (thinking2 or thinking)[-600:]
    return tail[tail.find(". ") + 2:] if ". " in tail else tail


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--name", default="judge-glimmer")
    ap.add_argument("--join-code", default="")
    ap.add_argument("--ollama", default="http://127.0.0.1:11434")
    ap.add_argument("--model", default="muse-glimmer")
    ap.add_argument("--label", default="Muse Glimmer 30B (judge node)")
    args = ap.parse_args()
    url = args.url.rstrip("/")
    me = identity(args)
    print(f"[{args.name}] judge {me['node_id'][:8]} | {args.model}", flush=True)
    done = 0
    while True:
        job = call(f"{url}/api/hint/job", {"node_id": me["node_id"]})
        if "error" in job:
            raise SystemExit(f"coordinator refused: {job}")
        if job.get("wait"):
            time.sleep(job.get("retry_after", 15))
            continue
        t = time.time()
        try:
            hint = ask(args.ollama, args.model, job["prompt"])
        except Exception as e:  # the lease expires and the API fallback can take it
            print(f"[{args.name}] item {job['item_id']} failed: {e}", flush=True)
            continue
        sig = hmac.new(me["secret"].encode(), f"{job['item_id']}|{hint}".encode(), hashlib.sha256).hexdigest()
        res = call(f"{url}/api/hint/submit", {"node_id": me["node_id"], "item_id": job["item_id"],
                                               "hint": hint, "model": args.label, "sig": sig})
        done += 1
        print(f"[{args.name}] #{done} item {job['item_id']} hint in {time.time() - t:.1f}s {res.get('error', '')}", flush=True)


if __name__ == "__main__":
    main()
