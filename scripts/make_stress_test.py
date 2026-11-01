"""Writes data/stress_test_1b.ipynb: harder checks of base vs fine-tuned Llama 3.2 1B on sentiment.

  fair   - the base model gets 3 worked examples (few-shot) so it knows the answer format; fine-tuned gets none
  dynasent - human-written adversarial sentiment sentences (rounds 1 and 2)
  rte    - a different task (does sentence 1 imply sentence 2? yes/no): checks the fine-tune didn't break instructions
  typos  - the same reviews with typos added (about 1 letter in 15 swapped or dropped)
  negation - only sentences with "not", "n't", "never" or "no"
Data: SST-2 (movie reviews) and Yelp (business reviews), never used in training. Live progress at /live.

Usage:  python scripts/make_stress_test.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOIN = (ROOT / "data" / "join_code.txt").read_text().strip()

body = r'''
import os, re, json, time, random, requests, torch
from google.colab import drive
drive.mount("/content/drive")
URL, JOIN_CODE = "https://data-refinery.primemax.workers.dev", "__JOIN__"
BASE = "unsloth/Llama-3.2-1B-Instruct"
RUN = "/content/drive/MyDrive/data-refinery/dpo_run_1b"
ADAPTER = next((f"{RUN}/{n}" for n in ("self_train", "sft_dpo", "sft") if os.path.exists(f"{RUN}/{n}/adapter_config.json")), None)
assert ADAPTER, "fine-tuned adapter not found in Drive (data-refinery/dpo_run_1b)"
SYSTEM = "Classify the sentiment of this text. Answer with one word: positive, negative or neutral."
SHOTS = [("The food was cold and the waiter ignored us.", "negative"), ("Absolutely loved it, would come back tomorrow.", "positive"),
         ("The store opens at nine on weekdays.", "neutral")]          # written for this test, not from any dataset
N = 200
from datasets import load_dataset
rng = random.Random(7)
sst = [(r["sentence"], ["negative", "positive"][r["label"]]) for r in load_dataset("stanfordnlp/sst2", split="validation")]
yelp = [(r["text"].replace("\\n", " ")[:1200], ["negative", "positive"][r["label"]]) for r in load_dataset("fancyzhx/yelp_polarity", split="test")]
rng.shuffle(sst); rng.shuffle(yelp)
mix = sst[:N // 2] + yelp[:N // 2]

def typo(t, r=random.Random(11)):
    out = []
    for ch in t:
        x = r.random()
        if ch.isalpha() and x < 0.033: continue                          # drop a letter
        if ch.isalpha() and x < 0.066: out.append(r.choice("abcdefghijklmnopqrstuvwxyz")); continue   # swap a letter
        out.append(ch)
    return "".join(out)

neg_re = re.compile(r"\b(not|never|no)\b|n't", re.I)
negation = [x for x in sst + yelp[:3000] if neg_re.search(x[0])][:N]
# DynaSent: human-written adversarial sentences (double negation, contrast, sarcasm) built to fool sentiment models.
# tasksource/dynasent is a plain-data copy (the official repo needs a loading script newer datasets won't run).
dyn = lambda rnd: [(r["sentence"], r["gold_label"]) for r in load_dataset("tasksource/dynasent", rnd, split="test")
                   if r["gold_label"] in ("positive", "negative", "neutral")][:N]
# RTE: a different task entirely (does sentence 1 imply sentence 2?), to check the fine-tune didn't break other instructions
rte = [(f"Sentence 1: {r['sentence1']}\nSentence 2: {r['sentence2']}", ["yes", "no"][r["label"]])
       for r in load_dataset("nyu-mll/glue", "rte", split="validation")][:N]
CHECKS = {"fair": mix, "typos": [(typo(t), g) for t, g in mix], "negation": negation,
          "dynasent_r1": dyn("r1"), "dynasent_r2": dyn("r2"), "rte": rte}
NAMES = {"fair": "Fair format (base gets 3 examples)", "typos": "Typos added", "negation": "Negation sentences",
         "dynasent_r1": "DynaSent round 1 (adversarial)", "dynasent_r2": "DynaSent round 2 (adversarial)",
         "rte": "RTE logic (yes/no, other task)"}
RTE_SYSTEM = "Does Sentence 1 entail Sentence 2? Answer only Yes or No."
TOTAL = sum(len(v) for v in CHECKS.values())

LIVE = {"stage": "loading", "done": 0, "scores": {}, "t0": time.time()}
def push():
    try:
        requests.post(f"{URL}/api/ft/telemetry", timeout=15, json={"join_code": JOIN_CODE, "run": "quick_svamp", "data": {
            "model": BASE, "test": "stress test (fair format, typos, negation, DynaSent, RTE)", "stage": LIVE["stage"], "done": LIVE["done"],
            "total": TOTAL, "scores": LIVE["scores"], "elapsed_min": (time.time() - LIVE["t0"]) / 60}})
    except Exception as e: print("live:", e)
push(); print("live: https://data-refinery.primemax.workers.dev/live")

from unsloth import FastLanguageModel
RES = {}
def messages(text, fewshot, system=SYSTEM):
    m = [{"role": "system", "content": system}]
    if fewshot:
        for q, a in SHOTS: m += [{"role": "user", "content": q}, {"role": "assistant", "content": a}]
    return m + [{"role": "user", "content": text}]

def score(path, who):
    model, tok = FastLanguageModel.from_pretrained(path, max_seq_length=1024, dtype=torch.bfloat16, load_in_4bit=False)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    LIVE.update(stage=f"testing {who}", done=0); push()
    right_all, seen = 0, 0
    for cname, test in CHECKS.items():
        is_rte = cname == "rte"
        fewshot = who == "base" and not is_rte      # base gets the worked sentiment examples: its best chance (RTE: both zero-shot)
        ok, answers = 0, []
        for i in range(0, len(test), 64):
            batch = test[i:i + 64]
            prompts = [tok.apply_chat_template(messages(t, fewshot, RTE_SYSTEM if is_rte else SYSTEM), tokenize=False, add_generation_prompt=True) for t, _ in batch]
            enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
            for (t, gold), o in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
                words = ("yes", "no") if is_rte else ("positive", "negative", "neutral")
                w = next((x for x in words if re.search("(^|[^a-z])" + x + "([^a-z]|$)", o.lower())), None)
                ok += w == gold; answers.append({"text": t, "gold": gold, "pred": w})
            seen += len(batch); LIVE["done"] = seen
            LIVE["scores"][who + "_running"] = (right_all + ok) / seen; push()
        right_all += ok
        RES[(cname, who)] = (ok / len(test), sum(a["pred"] is None for a in answers))
        print(f"{NAMES[cname]:38s} {who:10s} {ok}/{len(test)} = {ok / len(test):.1%}  (no label: {RES[(cname, who)][1]})")
        try:
            requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": f"stress_{cname}_{who}", "correct": ok,
                          "total": len(test), "meta": {"model": BASE, "test": NAMES[cname], "fewshot": fewshot, "answers": answers}})
        except Exception as e: print("could not post:", e)
    LIVE["scores"].pop(who + "_running", None); LIVE["scores"][who] = right_all / TOTAL; push()
    del model; torch.cuda.empty_cache()

score(BASE, "base")
score(ADAPTER, "finetuned")
LIVE["stage"] = "finished"; push()
print("\nSummary (base with 3 examples -> fine-tuned with none)")
for c in CHECKS:
    (b, bn), (f, fn) = RES[(c, "base")], RES[(c, "finetuned")]
    print(f"  {NAMES[c]:38s} {b:.1%} -> {f:.1%}  ({(f - b) * 100:+.1f})   no label: {bn} -> {fn}")
'''.replace("__JOIN__", JOIN)

guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
           + "\n".join(("    " + l) if l.strip() else l for l in body.strip("\n").splitlines())
           + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway.')"
           + "\nfinally:\n    time.sleep(15)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
nb = {"cells": [{"cell_type": "markdown", "metadata": {}, "source": "# Stress test · base (with 3 examples) vs fine-tuned Llama 3.2 1B\n**Runtime → L4 GPU**, run the cell, approve Drive. About 12–15 minutes; watch at /live."},
                {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}],
      "metadata": {"accelerator": "GPU", "colab": {"gpuType": "L4", "provenance": []}, "kernelspec": {"display_name": "Python 3", "name": "python3"}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / "stress_test_1b.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
