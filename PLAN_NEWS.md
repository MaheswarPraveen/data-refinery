# Second refinery task: Indian market headlines (sentiment + category)

Runs **after** the GSM8K refinery run finishes. GSM8K stays the main proof (weak-to-strong with exact answers).
This task is the real-world demo: headlines no Llama can have memorised (Apr–Oct 2026, after every cutoff).

## Data
`news-agent/data/ranks_600_650.json`: 1,872 headlines, 50 Indian small caps, 13 Apr – 9 Oct 2026.
Each has the stock's return vs Nifty after 1/3/7 trading days (`excess.d1/d3/d7`).
Prices are **never shown to nodes**; they're only used to score the labels afterwards.

## What nodes label (two fields, voted separately)
| Field | Options |
|---|---|
| sentiment | `positive` · `negative` · `neutral` for this company |
| category | `earnings` · `analyst` · `price_move` · `deal_order` · `corporate_action` · `regulatory_legal` · `management` · `listicle_promo` · `other` |

The node answers in plain text ending with two lines, `SENTIMENT: …` and `CATEGORY: …` (the same lesson as GSM8K: plain text beats forced JSON for small models).

## Consensus (field by field)
- Each field is decided on its own: 3 votes agree on sentiment → sentiment kept, even if category split.
- 2 of 3 agree → the Llama 3.3 70B judge picks from the two candidates.
- No majority → that field is marked `unclear` (a useful label in itself: ambiguous headline).

## Honeypots (anti-cheat needs a known answer)
~100 headlines whose answer is unambiguous, checked by hand before the run: clear earnings beats and misses,
order wins, "Top gainers" recaps. Same quarantine rule as GSM8K (below 30% after 5).

## Scoring against real prices (the headline result)
For each labelling source (consensus, each single model, random labels):
1. Positive-sentiment headlines: average 3-day return vs Nifty. Same for negative.
   Report the spread (positive minus negative) with a bootstrap confidence interval.
2. Hit rate: positive → `up`, negative → `down`, on non-neutral headlines.
3. By category: which kinds of headline actually move prices (does the "skip price recaps" rule from the news agent hold?).

Expected, honestly: small effects. Earlier trading runs on this data never beat random by a meaningful margin.
The claim is not "this predicts stocks" but "consensus labels are cleaner and line up better with prices than single-model labels".

## Code changes
- Coordinator: `task` column on items (`gsm8k` | `news`); the picker filters by task; field-by-field consensus for `news`.
  Running totals and dashboard sections are per task.
- Node: a news harness set (direct, skeptical trader, analyst, plain reader, checklist) and a parser for the two lines.
  The node picks the harness set from the job's task.
- Loader: `scripts/load_news.py` puts the 1,872 headlines and ~100 honeypots into Supabase.
- Analysis notebook: scoring against prices, charts for the demo.

## Timeline
| When | What |
|---|---|
| GSM8K run done (≈ today) | export consensus data, start the comparison datasets |
| Oct 28 | build the news task + local test with fake nodes |
| Oct 28–29 | news run on Kaggle (1,872 × 3 ≈ 5,600 votes ≈ 8 h) |
| Oct 30 | scoring vs prices, charts |
| Nov 2–3 | QLoRA fine-tunes for GSM8K (unchanged) |
| Nov 4 night | submit |

## Open choice
Optional extra: a small fine-tune (Llama 3.2 1B) on consensus vs single-model sentiment labels, scored by price spread.
Only if time allows after the GSM8K fine-tunes.
