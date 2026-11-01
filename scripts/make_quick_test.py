"""Writes data/quick_test_1b.ipynb (or, with "news", data/quick_news_1b.ipynb): ~10-minute check of base Llama 3.2 1B vs our fine-tuned 1B (adapter saved in Drive)
on SVAMP, a maths word-problem test set neither model was trained or tuned on.

Usage:  python scripts/make_quick_test.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOIN = (ROOT / "data" / "join_code.txt").read_text().strip()
import sys
NEWS = len(sys.argv) > 1 and sys.argv[1] == "news"

body = r'''
import os, re, json, time, requests, torch
from google.colab import drive
drive.mount("/content/drive")
URL, JOIN_CODE = "https://data-refinery.primemax.workers.dev", "__JOIN__"
BASE = "unsloth/Llama-3.2-1B-Instruct"
RUN = "/content/drive/MyDrive/data-refinery/dpo_run_1b"
ADAPTER = next((f"{RUN}/{n}" for n in ("sft_dpo", "self_train", "sft") if os.path.exists(f"{RUN}/{n}/adapter_config.json")), None)
assert ADAPTER, "fine-tuned adapter not found in Drive (data-refinery/dpo_run_1b)"
print("fine-tuned adapter:", ADAPTER)
SYSTEM = "Solve the maths word problem step by step. End with a final line: #### <number>"

from datasets import load_dataset
test = [(f"{r['Body'].strip()} {r['Question'].strip()}", float(r["Answer"])) for r in load_dataset("ChilleD/SVAMP", split="test")]
print(len(test), "SVAMP test problems (not used in any training)")

# live progress for the website (/live): stage, problems done, scores so far
LIVE = {"stage": "loading", "done": 0, "total": len(test), "scores": {}, "t0": time.time()}
def push():
    try:
        requests.post(f"{URL}/api/ft/telemetry", timeout=15, json={"join_code": JOIN_CODE, "run": "quick_svamp", "data": {
            "model": BASE, "test": "SVAMP", "stage": LIVE["stage"], "done": LIVE["done"], "total": LIVE["total"],
            "scores": LIVE["scores"], "elapsed_min": (time.time() - LIVE["t0"]) / 60}})
    except Exception as e: print("live:", e)
push(); print("live: https://data-refinery.primemax.workers.dev/live")

from unsloth import FastLanguageModel
def number(t):
    m = re.findall(r"####\s*\$?(-?[\d,]*\.?\d+)", t) or re.findall(r"(-?[\d,]*\.?\d+)", t)
    try: return float(m[-1].replace(",", "")) if m else None
    except ValueError: return None

def score(path, name):
    model, tok = FastLanguageModel.from_pretrained(path, max_seq_length=1024, dtype=torch.bfloat16, load_in_4bit=False)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    ok, answers, t0 = 0, [], time.time()
    LIVE.update(stage=f"testing {name}", done=0); push()
    for i in range(0, len(test), 128):
        batch = test[i:i + 128]
        prompts = [tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}], tokenize=False, add_generation_prompt=True) for q, _ in batch]
        enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=300, do_sample=False, pad_token_id=tok.pad_token_id)
        for (q, gold), t in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            p = number(t); good = p is not None and abs(p - gold) < 1e-6
            ok += good; answers.append({"q": q, "gold": gold, "pred": p, "ok": bool(good), "out": t})
        LIVE["done"] = len(answers); LIVE["scores"][name + "_running"] = ok / len(answers); push()
    print(f"{name:10s} {ok}/{len(test)} = {ok / len(test):.1%}  ({time.time() - t0:.0f}s)")
    try:
        requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": f"svamp_{name}", "correct": ok,
                      "total": len(test), "meta": {"model": BASE, "test": "SVAMP", "adapter": path, "answers": answers}})
    except Exception as e: print("could not post:", e)
    LIVE["scores"].pop(name + "_running", None); LIVE["scores"][name] = ok / len(test); push()
    del model; torch.cuda.empty_cache()
    return ok / len(test)

b = score(BASE, "base")
f = score(ADAPTER, "finetuned")
LIVE["stage"] = "finished"; push()
print(f"\nSVAMP: base {b:.1%} -> fine-tuned {f:.1%}  ({(f - b) * 100:+.1f} points)")
'''.replace("__JOIN__", JOIN)

guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
           + "\n".join(("    " + l) if l.strip() else l for l in body.strip("\n").splitlines())
           + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway.')"
           + "\nfinally:\n    time.sleep(15)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
nb = {"cells": [{"cell_type": "markdown", "metadata": {}, "source": "# Quick test · base vs fine-tuned Llama 3.2 1B on SVAMP\n**Runtime → L4 GPU**, run the cell, approve Drive. About 10 minutes; the runtime deletes itself after."},
                {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}],
      "metadata": {"accelerator": "GPU", "colab": {"gpuType": "L4", "provenance": []}, "kernelspec": {"display_name": "Python 3", "name": "python3"}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / "quick_test_1b.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)

# ---- news variant: base vs fine-tuned on Financial PhraseBank (human-labelled financial sentences, never trained on)
if NEWS:
    nbody = r'''
import os, re, json, time, random, requests, torch
from google.colab import drive
drive.mount("/content/drive")
URL, JOIN_CODE = "https://data-refinery.primemax.workers.dev", "__JOIN__"
BASE = "unsloth/Llama-3.2-1B-Instruct"
RUN = "/content/drive/MyDrive/data-refinery/dpo_run_1b"
ADAPTER = next((f"{RUN}/{n}" for n in ("self_train", "sft_dpo", "sft") if os.path.exists(f"{RUN}/{n}/adapter_config.json")), None)
assert ADAPTER, "fine-tuned adapter not found in Drive (data-refinery/dpo_run_1b)"
print("fine-tuned adapter:", ADAPTER)
SYSTEM = "Classify the financial headline's sentiment for the company's stock. Answer with one word: positive, negative or neutral."
from datasets import load_dataset
rows = [(r["text"], ["negative", "neutral", "positive"][r["label"]]) for r in load_dataset("mteb/FinancialPhrasebankClassification", split="train")]
random.Random(7).shuffle(rows); test = rows[:300]
print(len(test), "Financial PhraseBank sentences (human-labelled, never used in training)")

LIVE = {"stage": "loading", "done": 0, "total": len(test), "scores": {}, "t0": time.time()}
def push():
    try:
        requests.post(f"{URL}/api/ft/telemetry", timeout=15, json={"join_code": JOIN_CODE, "run": "quick_svamp", "data": {
            "model": BASE, "test": "Financial PhraseBank (news sentiment)", "stage": LIVE["stage"], "done": LIVE["done"], "total": LIVE["total"],
            "scores": LIVE["scores"], "elapsed_min": (time.time() - LIVE["t0"]) / 60}})
    except Exception as e: print("live:", e)
push(); print("live: https://data-refinery.primemax.workers.dev/live")

from unsloth import FastLanguageModel
def score(path, name):
    model, tok = FastLanguageModel.from_pretrained(path, max_seq_length=512, dtype=torch.bfloat16, load_in_4bit=False)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    ok, answers = 0, []
    LIVE.update(stage=f"testing {name}", done=0); push()
    for i in range(0, len(test), 64):
        batch = test[i:i + 64]
        prompts = [tok.apply_chat_template([{"role": "system", "content": SYSTEM}, {"role": "user", "content": h}], tokenize=False, add_generation_prompt=True) for h, _ in batch]
        enc = tok(prompts, return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
        for (h, gold), t in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            w = next((x for x in ("positive", "negative", "neutral") if x in t.lower()), None)
            ok += w == gold; answers.append({"text": h, "gold": gold, "pred": w, "raw": t})
        LIVE["done"] = len(answers); LIVE["scores"][name + "_running"] = ok / len(answers); push()
    print(f"{name:10s} {ok}/{len(test)} = {ok / len(test):.1%}")
    try:
        requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": f"phrasebank_{name}", "correct": ok,
                      "total": len(test), "meta": {"model": BASE, "test": "Financial PhraseBank", "adapter": path, "answers": answers}})
    except Exception as e: print("could not post:", e)
    LIVE["scores"].pop(name + "_running", None); LIVE["scores"][name] = ok / len(test); push()
    del model; torch.cuda.empty_cache()
    return ok / len(test)

b = score(BASE, "base")
f = score(ADAPTER, "finetuned")
LIVE["stage"] = "finished"; push()
print(f"\nFinancial PhraseBank: base {b:.1%} -> fine-tuned {f:.1%}  ({(f - b) * 100:+.1f} points)")
'''.replace("__JOIN__", JOIN)
    guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
               + "\n".join(("    " + l) if l.strip() else l for l in nbody.strip("\n").splitlines())
               + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway.')"
               + "\nfinally:\n    time.sleep(15)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
    nb["cells"] = [{"cell_type": "markdown", "metadata": {}, "source": "# Quick test · base vs fine-tuned Llama 3.2 1B on Financial PhraseBank\n**Runtime → L4 GPU**, run the cell, approve Drive. About 5 minutes; watch at /live."},
                   {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}]
    out = ROOT / "data" / "quick_news_1b.ipynb"
    out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
    print("wrote", out)
