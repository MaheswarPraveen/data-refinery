"""Writes data/review_test_1b.ipynb: base vs fine-tuned Llama 3.2 1B on real people's reviews (IMDB movies, Yelp
businesses, Amazon products). Neither model trained on them. Live progress at /live.

Usage:  python scripts/make_review_test.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOIN = (ROOT / "data" / "join_code.txt").read_text().strip()

body = r'''
import os, json, time, random, requests, torch
from google.colab import drive
drive.mount("/content/drive")
URL, JOIN_CODE = "https://data-refinery.primemax.workers.dev", "__JOIN__"
BASE = "unsloth/Llama-3.2-1B-Instruct"
RUN = "/content/drive/MyDrive/data-refinery/dpo_run_1b"
ADAPTER = next((f"{RUN}/{n}" for n in ("self_train", "sft_dpo", "sft") if os.path.exists(f"{RUN}/{n}/adapter_config.json")), None)
assert ADAPTER, "fine-tuned adapter not found in Drive (data-refinery/dpo_run_1b)"
SYSTEM = "Classify the sentiment of this text. Answer with one word: positive, negative or neutral."
N = 200
from datasets import load_dataset
def take(rows):
    random.Random(7).shuffle(rows); return rows[:N]
clip = lambda t: t.replace("<br />", " ")[:1500]          # long reviews are cut to fit the context
TESTS = {
    "imdb": take([(clip(r["text"]), ["negative", "positive"][r["label"]]) for r in load_dataset("stanfordnlp/imdb", split="test")]),
    "yelp": take([(clip(r["text"]), ["negative", "positive"][r["label"]]) for r in load_dataset("fancyzhx/yelp_polarity", split="test")]),
    "amazon": take([(clip(f"{r['title']}. {r['content']}"), ["negative", "positive"][r["label"]]) for r in load_dataset("fancyzhx/amazon_polarity", split="test")]),
}
NAMES = {"imdb": "IMDB (movie reviews)", "yelp": "Yelp (business reviews)", "amazon": "Amazon (product reviews)"}
TOTAL = sum(len(v) for v in TESTS.values())

LIVE = {"stage": "loading", "done": 0, "scores": {}, "t0": time.time()}
def push():
    try:
        requests.post(f"{URL}/api/ft/telemetry", timeout=15, json={"join_code": JOIN_CODE, "run": "quick_svamp", "data": {
            "model": BASE, "test": "IMDB + Yelp + Amazon (real people's reviews)", "stage": LIVE["stage"], "done": LIVE["done"],
            "total": TOTAL, "scores": LIVE["scores"], "elapsed_min": (time.time() - LIVE["t0"]) / 60}})
    except Exception as e: print("live:", e)
push(); print("live: https://data-refinery.primemax.workers.dev/live")

from unsloth import FastLanguageModel
RES = {}
def score(path, who):
    model, tok = FastLanguageModel.from_pretrained(path, max_seq_length=1024, dtype=torch.bfloat16, load_in_4bit=False)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    LIVE.update(stage=f"testing {who}", done=0); push()
    right_all, seen = 0, 0
    for tname, test in TESTS.items():
        ok, answers = 0, []
        for i in range(0, len(test), 64):
            batch = test[i:i + 64]
            prompts = [tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": t}], tokenize=False, add_generation_prompt=True) for t, _ in batch]
            enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
            with torch.no_grad():
                out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
            for (t, gold), o in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
                w = next((x for x in ("positive", "negative", "neutral") if x in o.lower()), None)
                ok += w == gold; answers.append({"text": t, "gold": gold, "pred": w})
            seen += len(batch); LIVE["done"] = seen
            LIVE["scores"][who + "_running"] = (right_all + ok) / seen; push()
        right_all += ok
        RES[(tname, who)] = ok / len(test)
        print(f"{NAMES[tname]:30s} {who:10s} {ok}/{len(test)} = {ok / len(test):.1%}")
        try:
            requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": f"{tname}_{who}", "correct": ok,
                          "total": len(test), "meta": {"model": BASE, "test": NAMES[tname], "adapter": path, "answers": answers}})
        except Exception as e: print("could not post:", e)
    LIVE["scores"].pop(who + "_running", None); LIVE["scores"][who] = right_all / TOTAL; push()
    del model; torch.cuda.empty_cache()

score(BASE, "base")
score(ADAPTER, "finetuned")
LIVE["stage"] = "finished"; push()
print("\nSummary (base -> fine-tuned)")
for t in TESTS:
    b, f = RES[(t, "base")], RES[(t, "finetuned")]
    print(f"  {NAMES[t]:30s} {b:.1%} -> {f:.1%}  ({(f - b) * 100:+.1f})")
'''.replace("__JOIN__", JOIN)

guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
           + "\n".join(("    " + l) if l.strip() else l for l in body.strip("\n").splitlines())
           + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway.')"
           + "\nfinally:\n    time.sleep(15)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
nb = {"cells": [{"cell_type": "markdown", "metadata": {}, "source": "# Review test · base vs fine-tuned Llama 3.2 1B on IMDB, Yelp and Amazon reviews\n**Runtime → L4 GPU**, run the cell, approve Drive. About 8–10 minutes; watch at /live."},
                {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}],
      "metadata": {"accelerator": "GPU", "colab": {"gpuType": "L4", "provenance": []}, "kernelspec": {"display_name": "Python 3", "name": "python3"}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / "review_test_1b.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
