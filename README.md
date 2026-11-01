<p align="center">
  <img src="assets/logo-banner.png" alt="Data Refinery" width="640">
</p>

<p align="center">
  <img src="assets/readme/hero.svg" alt="Raw data in. Refined data out." width="100%">
</p>

<p align="center">
  <a href="https://data-refinery.primemax.workers.dev"><b>Live dashboard</b></a> ·
  <a href="https://data-refinery.primemax.workers.dev/audit">Case study</a> ·
  <a href="https://data-refinery.primemax.workers.dev/future">Decentralisation</a>
</p>

<br>

<p align="center">
  <img src="assets/readme/result.svg" alt="Wrong labels fell from 35.5% to 1.3%; 78 of 93 hidden errors fixed" width="100%">
</p>

<p align="center">
  <img src="assets/readme/compare.svg" alt="One model alone vs the refinery" width="100%">
</p>

## A little refined data, a better model

<p align="center">
  <img src="assets/readme/finetune.svg" alt="Llama 3.2 1B base vs fine-tuned on refined data: news 3.7 to 50.3, financial news 26.3 to 69.0, negation 79.0 to 85.5, maths 33.7 to 35.0" width="100%">
</p>

Llama 3.2 1B was fine-tuned on **1,331 refined headline labels and 592 refined maths answers** (plus 260 right-vs-wrong pairs), about 4 minutes of training, then tested on questions it never saw:

| Test (unseen) | Base | Fine-tuned | Gain |
|---|---|---|---|
| News sentiment (finance tweets) | 3.7% | **50.3%** | **+46.7** |
| Financial news (Financial PhraseBank) | 26.3% | **69.0%** | **+42.7** |
| Negation (trick sentences like "not bad at all") | 79.0% | **85.5%** | **+6.5** |
| Maths (GSM-Symbolic) | 33.7% | **35.0%** | **+1.3** |

**Six unseen sentiment datasets** (trained only on refined financial headlines):

| Dataset | Base | Fine-tuned | Gain |
|---|---|---|---|
| Yelp (business reviews) | 73.5% | **90.5%** | +17.0 |
| Amazon (product reviews) | 60.0% | **87.0%** | +27.0 |
| SST-2 (movie reviews) | 34.0% | **86.5%** | +52.5 |
| IMDB (movie reviews) | 53.5% | **81.5%** | +28.0 |
| Financial PhraseBank (news) | 26.3% | **69.0%** | +42.7 |
| TweetEval (everyday tweets) | 26.0% | **52.0%** | +26.0 |
| **Average of the six** | **45.6%** | **77.7%** | **+32.2** |

### Why refined data beats regular data

| | |
|---|---|
| **+3.6 points** | refined vs raw data, same model and problems (Llama 3.1 8B: 71.6% vs 68.0%) |
| **+4.0 points** | refined vs GSM8K's own human-written answers (71.6% vs 67.6%) |
| **35.5% → 1.3%** | wrong labels before and after refining (Error Detection Test) |

Regular datasets carry hidden mistakes, and models learn them. The refinery removes them, and every answer's working is now step-checked: each calculation is recomputed before it enters the dataset.

## How it works

<p align="center">
  <img src="assets/readme/architecture.svg" alt="Architecture: nodes, coordinator, vote ledger, judge and refined dataset" width="100%">
</p>

| **01 · 5 independent votes** | **02 · 4–5 agree → accepted** | **03 · 3 agree → hint, re-vote** | **04 · ≤2 agree → rejected** |
|:---|:---|:---|:---|
| No node sees another's answer | Strong consensus is kept | Meta Muse Glimmer hints, never answers | Unsure items never enter the data |

<p align="center">
  <img src="assets/readme/agreement.svg" alt="More agreement, more accuracy" width="100%">
</p>

<details>
<summary><b>What happens to bad votes</b></summary>

- **Never become data:** a disagreeing answer is outvoted; only the consensus answer is written to the dataset
- **Kept as evidence:** every vote stays in the ledger and in each item's exported record
- **Count against the node:** no credit for outvoted answers; repeated failures on test items trigger quarantine
- **Useful later:** wrong answers next to the accepted one can form *rejected vs chosen* pairs for preference training

</details>

<details>
<summary><b>Anti-cheating and integrity</b></summary>

- **Honeypot questions:** a few test items with known answers, hidden among real data; nodes below 30% are quarantined
- **HMAC-SHA256 signatures:** every answer is signed with the node's secret key
- **Blind voting:** nodes receive only the question, never other answers
- **Timing checks:** answers faster than the model could compute are discarded
- **One vote per node per item:** job leases and duplicate checks
- **Judges can't decide:** signed hints only; answers and option letters are scrubbed out
- **Model diversity:** ten model families, so no single model's blind spot wins
- **Credit for agreeing with consensus:** no answer key needed
- **Full audit trail:** every signed vote is stored and can be recounted

</details>

<details>
<summary><b>Infrastructure: demo today, production next</b></summary>

| | Now (hackathon demo) | Production upgrade |
|---|---|---|
| Coordinator | Cloudflare Workers, free tier | Paid Workers + Durable Objects, multiple regions |
| Database | Supabase Postgres, free tier | Managed Postgres with replicas, or a public vote ledger |
| Nodes | Free Colab and Kaggle GPUs | One-click client on volunteer PCs and partner GPUs |
| Judge | Muse Glimmer 30B on Kaggle | Dedicated judge pool, 30–70B models |

The free tiers keep the demo at zero cost. The code is the same; only the hosting plan changes.

</details>

## Research foundation

| Paper | Why it matters here |
|---|---|
| [**Weak-to-Strong Generalization**](https://arxiv.org/abs/2312.09390) · OpenAI, 2023 | A strong model can learn from weaker supervisors; consensus filtering gives it cleaner weak labels |
| [Self-Consistency](https://arxiv.org/abs/2203.11171) · Google, 2022 | Majority vote beats any single answer; we vote across different models |
| [LLMs Can Self-Improve](https://arxiv.org/abs/2210.11610) · Google, 2022 | Training on high-agreement answers improves a model |
| [More Agents Is All You Need](https://arxiv.org/abs/2402.05120) · Tencent, 2024 | Accuracy grows with the number of voters |
| [Pervasive Label Errors](https://arxiv.org/abs/2103.14749) · MIT, 2021 | Even benchmark test sets contain wrong labels |
| [GSM8K](https://arxiv.org/abs/2110.14168) · OpenAI, 2021 | The maths benchmark used for verification |
| [GPQA](https://arxiv.org/abs/2311.12022) · NYU, 2023 | Expert questions; where small models stop |

## Future: every idle GPU becomes part of the refinery

Home PCs run small voter models, larger GPUs act as judges, dataset owners fund a reward pool, and contributors are paid per idle hour for verified work.

**Roadmap:** public vote ledger → multiple coordinators → one-click idle client → reward payouts → private datasets  ·  [Try the scale and cost calculator →](https://data-refinery.primemax.workers.dev/future)

## Variant: the agentic refinery

The same refinery can run inside **one large model**, organised as an agentic system instead of a network of small ones (planned, not yet built):

- **Solver agents:** several instances of the large model, each with a different role, prompt or toolset (step-by-step, code execution, checking backwards, domain expert), answer every item independently
- **Verifier agent:** re-checks answers with tools, for example by running the arithmetic or code, before they are counted
- **Consensus rule:** the same 4-of-5 vote, hint round and rejection rule as the network, with every agent vote kept in the ledger
- **Judge agent:** writes a hint for split items, never the answer
- **Where it fits:** expert data that small models cannot handle (such as GPQA-level science), or private data that must stay on one machine

Both versions share the same rules, vote ledger and anti-cheating design. This variant is described here as part of the Data Refinery project and is covered by the same [license](LICENSE).

## Repository

```
coordinator/   Cloudflare Worker: job assignment, vote counting, hints, dashboard pages
node/          node.py (volunteer node), judge.py (judge node), Colab and Kaggle launchers
scripts/       dataset loaders, exports, README graphics (make_readme_svgs.py)
assets/        logo and README graphics
```

Run a node on Colab or any machine with Ollama and a GPU:

```bash
curl -s https://data-refinery.primemax.workers.dev/colab.sh | bash -s <name> <join-code> <task>
```

Built for the **Meta Global AI Developer Hackathon** (Indie track). Voters include Meta Llama 3.2; the judge is Meta Muse Glimmer 30B.

<p align="center">
  <img src="assets/readme/robot.svg" alt="A robot checks each item, keeps the good ones and bins the bad ones" width="100%">
</p>

## Contributors

- **N P Maheswar**: idea, design, experiments
- Built with the help of **Claude** (Anthropic), an AI coding assistant

## License

See [LICENSE](LICENSE).
