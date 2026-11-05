"""Raw inputs for the agent task: user requests ONLY, no answers. The refinery's voters decide the tool calls.

Writes data/agent/requests.jsonl: {"id", "request", "context": {...} or null, "kind"}.
Usage:  python scripts/make_agent_requests.py [n_batches]
"""
import json
import random
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "agent" / "requests.jsonl"
TOOLS = json.loads((ROOT / "data" / "agent" / "tools.json").read_text(encoding="utf-8"))["tools"]
KEY = next(l.split("=", 1)[1].strip() for l in (ROOT.parent / "camoroles" / ".env").read_text().splitlines() if l.startswith("GROQ_API_KEY="))
AREAS = ["device settings and battery", "opening, closing and managing apps", "tapping and typing inside apps",
         "messages, calls, alarms, reminders and calendar", "web search and reading pages", "files and notes",
         "remembering things about the user", "phone health checks (storage, processes, temperature)",
         "unclear requests that need a follow-up question", "small talk"]
STYLES = ["casual Indian English", "short commands", "polite sentences", "spoken style with fillers",
          "Malayalam-English mix in English letters"]
PROMPT = """List 40 different things a user might say to an AI assistant that controls their Android phone.
Topic: {area}. Style: {style}. Only the user's words, no answers. Mostly single-step requests.
Reply with ONLY JSON: {{"requests": ["...", "..."]}}"""


def ask(prompt):
    body = {"model": "openai/gpt-oss-20b", "messages": [{"role": "user", "content": prompt}], "temperature": 1.0,
            "max_tokens": 2500, "reasoning_effort": "low", "response_format": {"type": "json_object"}}
    for wait in (0, 15, 45, 90):
        time.sleep(wait)
        try:
            req = urllib.request.Request("https://api.groq.com/openai/v1/chat/completions", json.dumps(body).encode(),
                                         {"content-type": "application/json", "authorization": f"Bearer {KEY}", "user-agent": "data-refinery"})
            return json.loads(json.loads(urllib.request.urlopen(req, timeout=90).read())["choices"][0]["message"]["content"])["requests"]
        except Exception as e:
            err = e
    raise err


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    seen = set()
    if OUT.exists():
        seen = {json.loads(l)["request"].lower() for l in OUT.read_text(encoding="utf-8").splitlines()}
    rng = random.Random(len(seen))
    with OUT.open("a", encoding="utf-8") as f:
        for b in range(n):
            area = AREAS[b % len(AREAS)]
            try:
                reqs = ask(PROMPT.format(area=area, style=rng.choice(STYLES)))
            except Exception as e:
                print("batch failed:", str(e)[:80], flush=True); continue
            for r in reqs:
                r = str(r).strip()
                if 3 < len(r) < 200 and r.lower() not in seen:
                    seen.add(r.lower())
                    f.write(json.dumps({"id": len(seen), "request": r, "context": None, "kind": area}, ensure_ascii=False) + "\n")
            f.flush()                                    # every batch is saved straight away
            print(f"batch {b + 1}/{n}: {len(seen)} requests", flush=True)
