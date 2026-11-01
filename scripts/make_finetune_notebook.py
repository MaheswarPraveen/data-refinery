"""Writes data/refinery_finetune_A100.ipynb: fine-tune Llama 3.1 8B on raw vs refined vs official answers, then grade all.

Usage:  python scripts/make_finetune_notebook.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

cells = []
md = lambda s: cells.append({"cell_type": "markdown", "metadata": {}, "source": s})
code = lambda s: cells.append({"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": s})

md("""# Data Refinery · fine-tune test (Llama 3.1 8B, Colab A100)
Does refined data train a better model? Four versions of Llama 3.1 8B Instruct, graded on 500 unseen GSM8K test problems:

| Version | Trained on |
|---|---|
| base | nothing |
| raw | one random vote per problem (unfiltered) |
| refined | the network's accepted answers |
| official | GSM8K's human-written solutions |

**Runtime → Change runtime type → A100 GPU**, paste the join code below, then **Runtime → Run all**. About 45–60 minutes. Scores are posted to the live dashboard automatically.""")

code("""%%capture
!pip install -q unsloth""")

code("""JOIN_CODE = ""          # paste the refinery join code here
URL = "https://data-refinery.primemax.workers.dev"
MODEL = "unsloth/Meta-Llama-3.1-8B-Instruct"
EPOCHS = 2
MAX_LEN = 1024
N_TEST = 500
SEED = 3407
assert JOIN_CODE, "paste the join code first" """)

code("""import json, re, random, time, requests, torch
from datasets import load_dataset, Dataset

# our refined data: one row per accepted maths problem (refined answer + one random raw vote)
rows = [json.loads(l) for l in requests.get(f"{URL}/api/ft/data", timeout=120).text.splitlines() if l.strip()]
gsm_train = load_dataset("openai/gsm8k", "main", split="train")
gsm_test = load_dataset("openai/gsm8k", "main", split="test")
official = {r["question"].strip(): r["answer"] for r in gsm_train}

def clean_official(a):              # drop GSM8K calculator annotations like <<2*3=6>>
    return re.sub(r"<<[^>]*>>", "", a).strip()

def with_answer(text, ans):         # every training answer ends the same way
    text = re.sub(r"\\n*\\s*(ANSWER|Answer|answer)\\s*:.*$", "", (text or "").strip()).strip()
    return f"{text}\\n#### {ans}"

rows = [r for r in rows if r["question"].strip() in official]
DATA = {
    "raw":      [(r["question"], with_answer(r["raw"], r["raw_answer"])) for r in rows],
    "refined":  [(r["question"], with_answer(r["refined"], r["final_answer"])) for r in rows],
    "official": [(r["question"], clean_official(official[r["question"].strip()])) for r in rows],
}
raw_wrong = sum(1 for r in rows if r["raw_answer"] != r["final_answer"])
print(f"{len(rows)} training problems · raw answers that disagree with the refined one: {raw_wrong}")

# held-out test: GSM8K test problems, minus the 50 used in our error-detection study
audit_q = {i["question"].strip() for i in requests.get(f"{URL}/api/audit", timeout=60).json()["items"]}
test = [(t["question"], t["answer"].split("####")[-1].strip().replace(",", "")) for t in gsm_test if t["question"].strip() not in audit_q]
random.Random(SEED).shuffle(test)
test = test[:N_TEST]
print(f"{len(test)} test problems")""")

code("""from unsloth import FastLanguageModel
SYSTEM = "Solve the maths word problem step by step. End with a final line: #### <number>"

def load():
    model, tok = FastLanguageModel.from_pretrained(MODEL, max_seq_length=MAX_LEN, dtype=torch.bfloat16, load_in_4bit=False)
    tok.padding_side = "left"
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    return model, tok

def prompt(tok, q, a=None):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": q}]
    if a is None:
        return tok.apply_chat_template(msgs, tokenize=False, add_generation_prompt=True)
    return tok.apply_chat_template(msgs + [{"role": "assistant", "content": a}], tokenize=False)

def number(text):
    m = re.findall(r"####\\s*\\$?(-?[\\d,]*\\.?\\d+)", text) or re.findall(r"(-?[\\d,]*\\.?\\d+)", text)
    if not m: return None
    try: return float(m[-1].replace(",", ""))
    except ValueError: return None

def evaluate(model, tok, name, bs=64):
    FastLanguageModel.for_inference(model)
    correct, t0 = 0, time.time()
    for i in range(0, len(test), bs):
        batch = test[i:i + bs]
        enc = tok([prompt(tok, q) for q, _ in batch], return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=320, do_sample=False, pad_token_id=tok.pad_token_id)
        texts = tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)
        for (q, gold), t in zip(batch, texts):
            p = number(t)
            correct += p is not None and abs(p - float(gold)) < 1e-6
    acc = correct / len(test)
    print(f"{name:9s} {correct}/{len(test)} = {acc:.1%}  ({time.time() - t0:.0f}s)")
    try:
        requests.post(f"{URL}/api/ft/result", json={"join_code": JOIN_CODE, "variant": name, "correct": correct, "total": len(test),
                      "train_n": 0 if name == "base" else len(rows), "meta": {"model": MODEL, "epochs": EPOCHS}}, timeout=30)
    except Exception as e:
        print("could not post result:", e)
    return acc

RESULTS = {}""")

code("""# 1) base model, no training
model, tok = load()
RESULTS["base"] = evaluate(model, tok, "base")
del model; torch.cuda.empty_cache()""")

code("""# 2) train and grade raw, refined and official, one after another (same problems, same settings)
from trl import SFTTrainer, SFTConfig

for name in ["raw", "refined", "official"]:
    model, tok = load()
    model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=SEED)
    tok.padding_side = "right"
    ds = Dataset.from_list([{"text": prompt(tok, q, a)} for q, a in DATA[name]]).shuffle(seed=SEED)
    common = dict(dataset_text_field="text", packing=False,
        per_device_train_batch_size=8, gradient_accumulation_steps=2, num_train_epochs=EPOCHS,
        learning_rate=2e-4, lr_scheduler_type="linear", warmup_ratio=0.05, bf16=True, logging_steps=20,
        optim="adamw_8bit", seed=SEED, output_dir=f"out_{name}", report_to="none", save_strategy="no")
    try: cfg = SFTConfig(max_seq_length=MAX_LEN, **common)      # older trl
    except TypeError: cfg = SFTConfig(max_length=MAX_LEN, **common)  # newer trl renamed it
    try: trainer = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=cfg)
    except TypeError: trainer = SFTTrainer(model=model, processing_class=tok, train_dataset=ds, args=cfg)
    t0 = time.time(); trainer.train(); print(f"trained {name} in {time.time() - t0:.0f}s")
    tok.padding_side = "left"
    RESULTS[name] = evaluate(model, tok, name)
    del model, trainer; torch.cuda.empty_cache()""")

code("""print("\\nGSM8K test accuracy, Llama 3.1 8B Instruct")
for k in ["base", "raw", "refined", "official"]:
    if k in RESULTS: print(f"  {k:9s} {RESULTS[k]:.1%}")
print("\\nRefined vs raw:", f"{(RESULTS['refined'] - RESULTS['raw']) * 100:+.1f} points")
print("Scores are on the dashboard. Stop the runtime now to save units: Runtime → Disconnect and delete runtime.")""")

nb = {"cells": cells, "metadata": {"accelerator": "GPU", "colab": {"gpuType": "A100", "provenance": []},
      "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}},
      "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / "refinery_finetune_A100.ipynb"
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
