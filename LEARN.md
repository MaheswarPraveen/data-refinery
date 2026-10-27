# Data Refinery: how each piece works

A plain-language guide to the code, written as we build it. If you can explain this file, you can explain the project to the judges.

## The picture

```
 GSM8K problems ──► COORDINATOR (Cloudflare Worker + D1)
                      │  hands out jobs, collects signed votes,
                      │  runs consensus, keeps the provenance log
          ┌───────────┼───────────┐
       Kaggle 1    Kaggle 2     Colab        ◄── nodes: Llama via Ollama,
          └───────────┼───────────┘              random (model, harness) per job
                      ▼
       3 votes agree ──► ACCEPTED (score 1.0)
       2 agree ────────► JUDGE on Groq (Muse Spark open / Llama 3.3 70B)
                           confirms ► ACCEPTED (0.67)   disagrees ► DROPPED
       none agree ─────► DROPPED
                      ▼
       /api/export ──► refined dataset with provenance ──► fine-tune Llama 3.1 8B (Step 6)
```

## 1. Database: `coordinator/schema.sql`

D1 is SQLite running inside Cloudflare. It's free, and the coordinator remembers everything even when your laptop is off.

| Table | Question it answers |
|---|---|
| `nodes` | Who is in the network? Each node has a **secret** used to sign its votes, and a `quarantined` flag. |
| `items` | What needs refining? It holds the question, its status, and the final answer. `gold` (the official answer) is used **only** for honeypots and for measuring quality, never to decide consensus. |
| `assignments` | Who is working on what? This is a **lease**: if a node disappears (for example, Colab disconnects), the job returns to the pool after 5 minutes. |
| `votes` | What did each node answer, with which model and harness, and how long did it take? This is the **provenance log**. |
| `judgements` | What did the judge say about disputed items? |

## 2. Coordinator: `coordinator/src/index.js`

A Cloudflare Worker is a small program that runs on Cloudflare's servers whenever a request arrives. It has 6 endpoints:

| Endpoint | What it does |
|---|---|
| `POST /api/register` | A new node joins and receives a `node_id` and a `secret`. |
| `POST /api/job` | A node asks for work and gets a question, plus the list of (model, harness) pairs that already answered it, so its random pick can avoid them. |
| `POST /api/submit` | A node returns `{model, harness, answer, reasoning, sig}`. |
| `GET /api/stats` | Numbers for the dashboard. |
| `GET /api/export` | The refined dataset, one JSON line per accepted item with full provenance. |
| `POST /api/rejudge` | Retries the judge on disputed items, for example after adding the Groq key. |

### How a job is picked
The coordinator prefers items that already have 1–2 votes, so items finish one at a time instead of all staying half done. A node never gets an item it already voted on. If **fewer than 3 nodes are online**, a node may vote again on the same item with a *different* pair. That vote is marked `same_node = 1` so we can report how often it happened.

### Consensus
Answers are **normalized** first, so `"1,200"`, `"$1200"` and `"1200.0"` all become `1200`. Then:
- **3 out of 3 agree:** accepted, score 1.0
- **2 out of 3 agree:** disputed, sent to the judge
- **no agreement:** dropped

Only votes from trusted nodes that weren't too fast count.

### Anti-cheating (the "false work" problem)
Fake hardware reports can't stop a cheater, because they control their own machine. So we pay for **checked results**, not for time online:
1. **Honeypots:** 10% of items have known answers. A node can't tell which ones, because `/api/submit` never says whether an answer was right.
2. **Quarantine:** after 5 honeypots, if a node scores below 30%, it only receives honeypots from then on. It can't pollute the data and earns nothing, but it can earn its way back.
3. **Speed check:** the *coordinator* measures the time from handing out a job to receiving the answer, so the node can't fake it. An answer faster than about 250 ms per billion parameters gets `too_fast = 1` and doesn't count.
4. **Signed votes (HMAC-SHA256):** each vote carries `sig = HMAC(secret, "job|model|harness|answer")`. This proves *which node* sent it and that nobody changed it on the way. It does **not** prove the work was done. That's what honeypots and consensus are for.
5. **Credits** = agreed with accepted answers + honeypots passed. Only verified work counts.

Future work for the README: proving *which model* actually ran (TOPLOC), and collusion between fake nodes run by the same person.

### The judge
**Llama 3.3 70B on Cloudflare Workers AI**, with **Llama 4 Scout** as backup. That keeps the whole system Meta-only.
- **No API key:** Workers AI is wired in through `[ai] binding = "AI"` in `wrangler.toml`, and the code calls `env.AI.run(model, …)`. It uses the free daily allowance on your Cloudflare account.
- It sees the problem and the candidate answers, is told "the candidates may be wrong", solves the problem itself and ends with `ANSWER: <number>`.
- If its answer matches the majority, the item is accepted with score 0.67. Otherwise it's dropped.
- If the daily allowance runs out, items stay `disputed`, and `POST /api/rejudge` finishes them later.

**Why not Groq or OpenRouter?** Your Groq key has Llama switched off and Muse Spark locked behind terms you can't accept. OpenRouter has no free Meta models.

**Lesson from testing: don't force JSON on the judge.** With JSON mode, Llama 70B wrote a short plan ("we divide 63 by 9…") and then guessed, getting 3 of 5 right. Writing in free text and ending with an `ANSWER:` line, it got **5 of 5** right, including correctly rejecting a case where 2 nodes agreed on a wrong answer (46 instead of 48). Nodes still use JSON, because small models need the structure, but the judge thinks better freely.

`wrangler dev` uses the real Workers AI even during local tests. That works because Wrangler is logged in as xczmax@gmail.com, and those calls count toward the free allowance.

## 3. Data: `scripts/make_seed.py`

This script downloads GSM8K, picks **1,500** training problems with a fixed random seed (so the selection is the same every run), marks **150** as honeypots and writes `data/seed.sql`. It also downloads `test.jsonl`, the exam used in Step 6.

## 4. Fake node: `node/fake_node.py`

It follows the real protocol (register, get a job, pick a random pair, sign, submit) but pretends to be Llama, using the answer key and a realistic accuracy per model. `--cheat` sends instant random answers.

**First test (4 nodes, 60 jobs each):**

| Node | Honeypots | Too fast | Credits | Quarantined |
|---|---|---|---|---|
| kaggle-1 | 2/3 | 0 | 17 | no |
| kaggle-2 | 2/3 | 0 | 17 | no |
| colab-1 | 1/3 | 0 | 16 | no |
| **cheater** | **0/17** | **60** | **0** | **yes** |

15 items accepted, **all 15 correct**. 30 disputed (waiting for the judge, since there's no Groq key yet), 12 dropped.

## 5. Real node: `node/node.py`

This runs inside Kaggle or Colab next to Ollama. For every job:
1. **Asks** the coordinator for a job.
2. **Picks a random (model, harness) pair,** weighted so 8B is rare (`--weights 1,1,0.3`). It first tries a model that hasn't answered this item yet, then any unused pair.
3. **Solves** the problem through Ollama's `/api/chat` with **structured output**: we hand Ollama a JSON schema `{reasoning, answer}`, so the model *must* reply in that shape. Reasoning comes first so the model works it out before committing to an answer. If a small model writes "18 dollars", the script keeps the last number.
4. **Signs** the vote (HMAC) and **submits** it.

**The 5 harnesses** give different instructions for the same task: `direct`, `step_by_step`, `equations_first`, `check_backwards`, `teacher`. Different instructions lead to different mistakes, and that's what makes agreement meaningful.

**Built for flaky free GPUs:**
- Network calls retry with growing waits (2s, 4s, 8s … up to 60s).
- If Ollama fails on one job, the node skips it, and the lease returns the job to the pool.
- Its identity is saved in `<name>.node.json`, so a restarted notebook rejoins as the same node and keeps its reliability history.

It was tested on the laptop against `scripts/mock_ollama.py`, a pretend Ollama that answers "42 apples" after a delay sized to the model. The node parsed the reply to `42`, all 7 votes were accepted with no speed flags, and a restart reused the same identity.

How to run it on Kaggle or Colab: see `node/NOTEBOOK_SETUP.md`.

## Running it locally

```bash
cd coordinator
npx wrangler d1 execute refinery --local --config wrangler.toml --file=schema.sql
npx wrangler d1 execute refinery --local --config wrangler.toml --file=../data/seed.sql
npx wrangler dev --config wrangler.toml --port 8787
```
Always pass `--config wrangler.toml`. Otherwise Wrangler picks up the GATE site's `wrangler.jsonc` from `C:\Claude Work`.

## 6. Deployed (Oct 27)

**Live at https://data-refinery.primemax.workers.dev** on your Cloudflare account (xczmax@gmail.com).

How it was done:
1. `wrangler d1 create refinery`: makes the real cloud database (in the Asia-Pacific region). Its id goes in `wrangler.toml`.
2. `wrangler d1 execute refinery --remote --file=schema.sql`, then the same with `--file=../data/seed.sql`. `--remote` means the cloud copy, not the local test copy. It now holds 1,500 problems, 150 of them honeypots.
3. `wrangler deploy`: uploads `src/index.js` and connects it to the database (`env.DB`) and Workers AI (`env.AI`).
4. `wrangler secret put JOIN_CODE`: stores a password-like code on Cloudflare. Secrets are encrypted and never appear in code or config. The code is also saved in `data/join_code.txt`, which is gitignored.

**The join code protects two things:**
- `/api/register`: strangers can't add nodes and pollute the data.
- `/api/rejudge`: strangers can't burn the free Workers AI allowance.

To rejudge yourself: `curl -X POST https://data-refinery.primemax.workers.dev/api/rejudge -H "x-join-code: <code>"`

**Live test:** the home page responds, registering without the code is refused, registering with it works, a node gets a job, rejudging without the code is refused, and stats show 1,500 items. The test node was deleted afterwards, so the real run starts at 0 nodes.

To redeploy after code changes: `cd coordinator`, then `npx wrangler deploy --config wrangler.toml`.

## 7. Dashboard (Oct 27)

**https://data-refinery.primemax.workers.dev**. The coordinator serves it at `/` from `coordinator/src/dashboard.html`. That file is imported into the Worker as a plain string, which the `[[rules]] type = "Text"` block in `wrangler.toml` allows. It refreshes every 15 s and **pauses while the tab is hidden**.

| Section | What it shows |
|---|---|
| 5 headline tiles | Problems settled, kept for training, accuracy of kept answers against official answers, nodes online, votes per minute |
| Where every problem stands | One bar for the 1,350 real problems: kept by full agreement, kept by the judge, waiting for the judge, thrown out, still being voted on |
| Nodes | Status, votes, honeypots passed, agreed, too fast, credits, last seen |
| Model × instruction style | 3 models × 5 styles, each cell showing how often it matches the official answer |
| Judge | How many it kept, rejected or has waiting, and how often it agrees with the 2-vote majority |
| Recent activity | Who solved what, with which model and style, and how long it took |
| Rules of the network | Every parameter, with a one-line reason |

Hover over column headers, bar segments and cells for short explanations.

**Two privacy rules built into the page:**
- **Recent activity never shows answers or problem ids.** Otherwise a cheating node could read the dashboard and copy other nodes' answers.
- **Node-supplied text** such as names and model strings is escaped, and escaped twice inside tooltips, so a malicious node can't inject code into the page.

### Why "running totals" (migration `0002_running_totals.sql`)
Cloudflare's free D1 plan allows **5 million rows read per day**. Re-counting every vote for each dashboard refresh would read tens of thousands of rows each time and use up the day's allowance in an afternoon of watching. So:
- `nodes` has counters (`votes_n`, `hp_n`, `hp_ok`, `too_fast_n`, `agreed_n`), and `pair_stats` has per-model/style totals. Each vote adds +1 in the same batch that saves it.
- Stats are cached for 10 s, however many people are watching.
- Picking a job looks at only the **first 100 pending problems** instead of all 1,500, and scans everything only if those have nothing for this node.

A fresh database needs `schema.sql`, then `migrations/0002_running_totals.sql`, then `data/seed.sql`.

**Test (local, 3 honest fake nodes + 1 cheater):** 63 settled, 45 kept, 100% of kept answers correct, the judge kept 29 and rejected 6. Model accuracies were roughly 31–39% for 1B, 44–69% for 3B and 72–81% for 8B. These are fake nodes, so the numbers only show that the plumbing works. Light mode, dark mode and a 375 px phone screen were all checked with no sideways scrolling, and the colours passed the colour-blindness checks.

## Next
- First real node on Kaggle (`node/NOTEBOOK_SETUP.md`).
- Run the first real node on Kaggle.
- Dashboard page.
