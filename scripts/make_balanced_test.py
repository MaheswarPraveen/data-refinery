"""Writes data/balanced_retest_1b.ipynb: retrain Llama 3.2 1B on the fixed refinery data, then re-run the stress test
and a general reasoning test.

Fixed data: balanced headline labels (/api/ft/news: equal positive / negative / neutral) and step-checked maths
working (/api/ft/data: every calculation recomputed). Same gentle settings as the first 1B run (1 epoch, lr 5e-5).
Tests: the six stress checks (base results from the earlier stress run are reused: same items, same prompts) and
ARC-Easy (grade-school science reasoning, A-D) for base, the earlier fine-tune and the new one. Live at /live.

Usage:  python scripts/make_balanced_test.py
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
OLD = next((f"/content/drive/MyDrive/data-refinery/dpo_run_1b/{n}" for n in ("self_train", "sft_dpo", "sft")
            if os.path.exists(f"/content/drive/MyDrive/data-refinery/dpo_run_1b/{n}/adapter_config.json")), None)
SAVE = "/content/drive/MyDrive/data-refinery/balanced_1b"
os.makedirs(SAVE, exist_ok=True)
N = 200
from datasets import load_dataset, Dataset

LIVE = {"stage": "loading", "done": 0, "total": 0, "scores": {}, "t0": time.time()}
def push():
    try:
        requests.post(f"{URL}/api/ft/telemetry", timeout=15, json={"join_code": JOIN_CODE, "run": "quick_svamp", "data": {
            "model": BASE, "test": "balanced retrain: stress test + ARC-Easy", "stage": LIVE["stage"], "done": LIVE["done"],
            "total": LIVE["total"], "scores": LIVE["scores"], "elapsed_min": (time.time() - LIVE["t0"]) / 60}})
    except Exception as e: print("live:", e)
push(); print("live: https://data-refinery.primemax.workers.dev/live")

# ---- fixed refinery data
MATH_SYS = "Solve the maths word problem step by step. End with a final line: #### <number>"
NEWS_SYS = "Classify the financial headline's sentiment for the company's stock. Answer with one word: positive, negative or neutral."
def with_answer(text, ans):
    text = re.sub(r"\n*\s*(ANSWER|Answer|answer)\s*:.*$", "", (text or "").strip()).strip()
    return f"{text}\n#### {ans}"
maths = [json.loads(l) for l in requests.get(f"{URL}/api/ft/data", timeout=120).text.splitlines() if l.strip()]
news = [json.loads(l) for l in requests.get(f"{URL}/api/ft/news", timeout=120).text.splitlines() if l.strip()]
from collections import Counter
print(f"step-checked maths: {len(maths)} · balanced headlines: {len(news)} {dict(Counter(r['sentiment'] for r in news))}")

# ---- train (same gentle settings as the first 1B run)
from unsloth import FastLanguageModel
from trl import SFTTrainer, SFTConfig
LIVE["stage"] = "training on fixed data"; push()
model, tok = FastLanguageModel.from_pretrained(BASE, max_seq_length=1024, dtype=torch.bfloat16, load_in_4bit=False)
if tok.pad_token is None: tok.pad_token = tok.eos_token
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"], use_gradient_checkpointing="unsloth", random_state=3407)
tok.padding_side = "right"
chat = lambda sys, q, a: tok.apply_chat_template([{"role": "system", "content": sys}, {"role": "user", "content": q}, {"role": "assistant", "content": a}], tokenize=False)
ds = Dataset.from_list([{"text": chat(MATH_SYS, r["question"], with_answer(r["refined"], r["final_answer"]))} for r in maths]
                       + [{"text": chat(NEWS_SYS, r["headline"], r["sentiment"])} for r in news]).shuffle(seed=3407)
c = dict(dataset_text_field="text", packing=False, per_device_train_batch_size=8, gradient_accumulation_steps=2, num_train_epochs=1,
         learning_rate=5e-5, lr_scheduler_type="linear", warmup_ratio=0.05, bf16=True, logging_steps=20, optim="adamw_8bit",
         seed=3407, output_dir="out_bal", report_to="none", save_strategy="no")
try: cfg = SFTConfig(max_seq_length=1024, **c)
except TypeError: cfg = SFTConfig(max_length=1024, **c)
try: tr = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=cfg)
except TypeError: tr = SFTTrainer(model=model, processing_class=tok, train_dataset=ds, args=cfg)
t0 = time.time(); tr.train(); print(f"trained in {time.time() - t0:.0f}s")
try: model.save_pretrained(f"{SAVE}/sft"); tok.save_pretrained(f"{SAVE}/sft"); print("saved:", f"{SAVE}/sft")
except Exception as e: print("save failed (continuing):", e)
del tr; torch.cuda.empty_cache()

# ---- tests (same items and prompts as the stress test)
SYSTEM = "Classify the sentiment of this text. Answer with one word: positive, negative or neutral."
RTE_SYSTEM = "Does Sentence 1 entail Sentence 2? Answer only Yes or No."
ARC_SYSTEM = "Answer the multiple-choice science question. Reply with only the letter of the correct answer."
rng = random.Random(7)
sst = [(r["sentence"], ["negative", "positive"][r["label"]]) for r in load_dataset("stanfordnlp/sst2", split="validation")]
yelp = [(r["text"].replace("\\n", " ")[:1200], ["negative", "positive"][r["label"]]) for r in load_dataset("fancyzhx/yelp_polarity", split="test")]
rng.shuffle(sst); rng.shuffle(yelp)
mix = sst[:N // 2] + yelp[:N // 2]
def typo(t, r=random.Random(11)):
    out = []
    for ch in t:
        x = r.random()
        if ch.isalpha() and x < 0.033: continue
        if ch.isalpha() and x < 0.066: out.append(r.choice("abcdefghijklmnopqrstuvwxyz")); continue
        out.append(ch)
    return "".join(out)
neg_re = re.compile("(^|[^a-z])(not|never|no)([^a-z]|$)|n't", re.I)
negation = [x for x in sst + yelp[:3000] if neg_re.search(x[0])][:N]
dyn = lambda rnd: [(r["sentence"], r["gold_label"]) for r in load_dataset("tasksource/dynasent", rnd, split="test")
                   if r["gold_label"] in ("positive", "negative", "neutral")][:N]
rte = [("Sentence 1: " + r["sentence1"] + "\nSentence 2: " + r["sentence2"], ["yes", "no"][r["label"]])
       for r in load_dataset("nyu-mll/glue", "rte", split="validation")][:N]
arc_rows = [r for r in load_dataset("allenai/ai2_arc", "ARC-Easy", split="test") if r["answerKey"] in "ABCD" and len(r["choices"]["label"]) == 4]
random.Random(5).shuffle(arc_rows)
arc = [(r["question"] + "\n" + "\n".join(f"{l}. {t}" for l, t in zip(r["choices"]["label"], r["choices"]["text"])), r["answerKey"].lower()) for r in arc_rows[:N]]
CHECKS = {"fair": (mix, SYSTEM, ("positive", "negative", "neutral")), "typos": ([(typo(t), g) for t, g in mix], SYSTEM, ("positive", "negative", "neutral")),
          "negation": (negation, SYSTEM, ("positive", "negative", "neutral")), "dynasent_r1": (dyn("r1"), SYSTEM, ("positive", "negative", "neutral")),
          "dynasent_r2": (dyn("r2"), SYSTEM, ("positive", "negative", "neutral")), "rte": (rte, RTE_SYSTEM, ("yes", "no")),
          "arc": (arc, ARC_SYSTEM, ("a", "b", "c", "d"))}

def run(model, tok, who, names):
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    res = {}
    for cname in names:
        test, system, words = CHECKS[cname]
        ok = 0
        for i in range(0, len(test), 64):
            batch = test[i:i + 64]
            prompts = [tok.apply_chat_template([{"role": "system", "content": system}, {"role": "user", "content": t}], tokenize=False, add_generation_prompt=True) for t, _ in batch]
            enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
            for (t, gold), o in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
                o = o.lower().strip()
                w = next((x for x in words if re.search("(^|[^a-z])" + x + "([^a-z]|$)", o)), None)
                ok += w == gold
            LIVE["done"] += len(batch); push()
        res[cname] = ok / len(test)
        LIVE["scores"][f"{who} {cname}"] = res[cname]; push()
        print(f"{cname:12s} {who:10s} {ok}/{len(test)} = {ok / len(test):.1%}")
        try:
            requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": f"bal_{cname}_{who}", "correct": ok, "total": len(test), "meta": {"test": cname}})
        except Exception as e: print("could not post:", e)
    return res

LIVE["total"] = sum(len(CHECKS[k][0]) for k in CHECKS) + 2 * len(arc)
LIVE["stage"] = "testing new fine-tune"; push()
new = run(model, tok, "balanced", list(CHECKS))
del model; torch.cuda.empty_cache()
for path, who in ((BASE, "base"), (OLD, "earlier")):
    if not path: continue
    LIVE["stage"] = f"ARC-Easy: {who}"; push()
    m, t = FastLanguageModel.from_pretrained(path, max_seq_length=1024, dtype=torch.bfloat16, load_in_4bit=False)
    if t.pad_token is None: t.pad_token = t.eos_token
    new[f"arc_{who}"] = run(m, t, who, ["arc"])["arc"]
    del m; torch.cuda.empty_cache()

# base scores for the stress checks come from the earlier stress run (same items, same prompts; base had 3 examples there)
prev = {r["variant"]: r["correct"] / r["total"] for r in requests.get(f"{URL}/api/ft/results", timeout=60).json()["results"]}
LIVE["stage"] = "finished"; push()
print("\nStress checks: base (3 examples) | earlier fine-tune | balanced fine-tune")
for c in ["fair", "typos", "negation", "dynasent_r1", "dynasent_r2", "rte"]:
    b, e = prev.get(f"stress_{c}_base"), prev.get(f"stress_{c}_finetuned")
    f = lambda v: "  –  " if v is None else f"{v:.1%}"
    print(f"  {c:12s} {f(b)} | {f(e)} | {new[c]:.1%}")
print(f"\nARC-Easy (science reasoning): base {new.get('arc_base', 0):.1%} | earlier {new.get('arc_earlier', 0):.1%} | balanced {new['arc']:.1%}")
'''.replace("__JOIN__", JOIN)

guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
           + "\n".join(("    " + l) if l.strip() else l for l in body.strip("\n").splitlines())
           + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway.')"
           + "\nfinally:\n    time.sleep(15)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
nb = {"cells": [{"cell_type": "markdown", "metadata": {}, "source": "# Balanced retrain · Llama 3.2 1B on fixed refinery data, then stress test + ARC-Easy\n**Runtime → L4 GPU**, run the cell, approve Drive. About 20–25 minutes; watch at /live."},
                {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}],
      "metadata": {"accelerator": "GPU", "colab": {"gpuType": "L4", "provenance": []}, "kernelspec": {"display_name": "Python 3", "name": "python3"}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / "balanced_retest_1b.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
