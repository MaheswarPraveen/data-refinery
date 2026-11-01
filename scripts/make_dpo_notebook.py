"""Writes data/refinery_dpo_A100.ipynb: Llama 3.1 8B, base vs refined (SFT) vs + wrong answers (DPO) vs + self-training.

The DPO step uses the refinery's own votes as preference pairs: "chosen" = an answer that agreed with the consensus,
"rejected" = an outvoted wrong answer to the same problem (Self-Consistency Preference Optimization, Meta 2024).
Tested on GSM-Symbolic (Apple 2024): GSM8K problems rewritten with new names and numbers, so nothing is memorised.
Every trained adapter and every answer is saved to Google Drive before the runtime deletes itself.

Usage:  python scripts/make_dpo_notebook.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JOIN = (ROOT / "data" / "join_code.txt").read_text().strip()
import sys
SIZE = sys.argv[1] if len(sys.argv) > 1 else "8b"       # "8b" (default) or "1b"
CFG = {"8b": dict(model="unsloth/Meta-Llama-3.1-8B-Instruct", four_bit="True", tag="", save="dpo_run", bs="32", tbs="4", acc="4", title="Llama 3.1 8B", time="2.5–3 hours"),
       "1b": dict(model="unsloth/Llama-3.2-1B-Instruct", four_bit="False", tag="1b_", save="dpo_run_1b", bs="64", tbs="8", acc="2", title="Llama 3.2 1B", time="about 1 hour"),
       # stronger 1B run, refinery data only: every refined answer and pair, more epochs, higher learning rates,
       # and two self-training rounds where the consensus marks the model's own mistakes
       "1b2": dict(model="unsloth/Llama-3.2-1B-Instruct", four_bit="False", tag="1b2_", save="dpo_run_1b_v2", tbs="8", acc="2",
                   title="Llama 3.2 1B (stronger training)",
                   split="False", sft_epochs="3", sft_lr="1e-4", dpo_epochs="2", dpo_lr="2e-5", self_rounds="2", raw_arm="False", base_known="(101 / 300, 11 / 300)", bs="128", gen_tokens="300", self_n="600", packing="True", time="about 35–40 minutes")}[SIZE]
DEFAULTS = dict(gen_tokens="400", self_n="0", packing="False", base_known="None", raw_arm="False", split="True", sft_epochs="1", sft_lr="5e-5", dpo_epochs="1", dpo_lr="5e-6", self_rounds="1")
CFG = {**DEFAULTS, **CFG}

body = r'''
import os, json, re, random, time, requests, torch
from google.colab import drive
drive.mount("/content/drive")
SAVE = "/content/drive/MyDrive/data-refinery/__SAVE__"
os.makedirs(SAVE, exist_ok=True)
assert os.path.isdir(SAVE), "Google Drive not mounted"
print("saving everything to", SAVE)

URL = "https://data-refinery.primemax.workers.dev"
JOIN_CODE = "__JOIN__"
MODEL = "__MODEL__"
TAG = "__TAG__"            # prefix for result names, so runs of different sizes don't overwrite each other
MAX_LEN, N_TEST, SEED = 1024, 300, 3407
SYSTEM = "Solve the maths word problem step by step. End with a final line: #### <number>"

from datasets import load_dataset, Dataset

def with_answer(text, ans):         # every training answer ends the same way
    text = re.sub(r"\n*\s*(ANSWER|Answer|answer)\s*:.*$", "", (text or "").strip()).strip()
    return f"{text}\n#### {ans}"

sft_rows = [json.loads(l) for l in requests.get(f"{URL}/api/ft/data", timeout=120).text.splitlines() if l.strip()]
sft_rows = [r for r in sft_rows if r.get("refined")]
pairs = [json.loads(l) for l in requests.get(f"{URL}/api/ft/pairs", timeout=120).text.splitlines() if l.strip()]
# half the problems keep their labels; the other half are "unlabelled": the model solves them itself later,
# and the network's consensus (never the official key) tells it whether it was right
random.Random(SEED).shuffle(sft_rows)
SPLIT, SELF_ROUNDS, SELF_N = __SPLIT__, __SELF_ROUNDS__, __SELF_N__
if SPLIT:
    half = len(sft_rows) // 2
    unlab = sft_rows[half:]; sft_rows = sft_rows[:half]
    lab_q = {r["question"] for r in sft_rows}
    pairs = [p for p in pairs if p["question"] in lab_q]
else:
    # every refined answer and pair is used for training; self-training then re-solves the same refined problems
    # and learns from the answers the consensus marks wrong
    unlab = list(sft_rows)
print(f"labelled: {len(sft_rows)} refined answers, {len(pairs)} right-vs-wrong pairs · unlabelled: {len(unlab)} problems")

# unseen test: GSM-Symbolic (new names and numbers); GSM8K test as a fallback if it can't be loaded
try:
    ds = load_dataset("json", data_files="https://huggingface.co/datasets/apple/GSM-Symbolic/resolve/main/main/test.jsonl", split="train"); TEST_NAME = "GSM-Symbolic"
except Exception as e:
    print("GSM-Symbolic not available, using GSM8K test:", e)
    ds = load_dataset("openai/gsm8k", "main", split="test"); TEST_NAME = "GSM8K test"
test = [(t["question"], t["answer"].split("####")[-1].strip().replace(",", "")) for t in ds]
random.Random(SEED).shuffle(test)
test = test[:N_TEST]
print(f"{len(test)} {TEST_NAME} problems")

# news: the network's refined headline labels for training; a human-labelled public set for testing
NEWS_SYSTEM = "Classify the financial headline's sentiment for the company's stock. Answer with one word: positive, negative or neutral."
news_rows = [json.loads(l) for l in requests.get(f"{URL}/api/ft/news", timeout=120).text.splitlines() if l.strip()]
news_rows = [r for r in news_rows if r["sentiment"] in ("positive", "negative", "neutral")]
try:
    nd = load_dataset("zeroshot/twitter-financial-news-sentiment", split="validation")
    news_test = [(t["text"], ["negative", "positive", "neutral"][t["label"]]) for t in nd]   # 0 bearish, 1 bullish, 2 neutral
    random.Random(SEED).shuffle(news_test); news_test = news_test[:N_TEST]
except Exception as e:
    print("news test set not available, skipping the news test:", e); news_test = []
print(f"{len(news_rows)} refined headline labels · {len(news_test)} human-labelled test headlines")

from unsloth import FastLanguageModel, PatchDPOTrainer
PatchDPOTrainer()

def load():
    # 4-bit weights (QLoRA) so the 8B model trains on a 24 GB L4
    model, tok = FastLanguageModel.from_pretrained(MODEL, max_seq_length=MAX_LEN, dtype=torch.bfloat16, load_in_4bit=__FOURBIT__)
    if tok.pad_token is None: tok.pad_token = tok.eos_token
    return model, tok

def chat(tok, q, a=None, system=SYSTEM):
    m = [{"role": "system", "content": system}, {"role": "user", "content": q}]
    if a is None: return tok.apply_chat_template(m, tokenize=False, add_generation_prompt=True)
    return tok.apply_chat_template(m + [{"role": "assistant", "content": a}], tokenize=False)

def number(text):
    m = re.findall(r"####\s*\$?(-?[\d,]*\.?\d+)", text) or re.findall(r"(-?[\d,]*\.?\d+)", text)
    if not m: return None
    try: return float(m[-1].replace(",", ""))
    except ValueError: return None

def news_eval(model, tok, name, bs=32):
    if not news_test: return None
    correct, answers = 0, []
    for i in range(0, len(news_test), bs):
        batch = news_test[i:i + bs]
        enc = tok([chat(tok, h, system=NEWS_SYSTEM) for h, _ in batch], return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=6, do_sample=False, pad_token_id=tok.pad_token_id)
        for (h, gold), t in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            w = next((x for x in ("positive", "negative", "neutral") if x in t.lower()), None)
            correct += w == gold; answers.append({"headline": h, "gold": gold, "pred": w})
    print(f"{name + ' news':16s} {correct}/{len(news_test)} = {correct / len(news_test):.1%}")
    put_json(answers, f"news_answers_{name}.json")
    try:
        requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": TAG + name + "_news", "correct": correct,
            "total": len(news_test), "train_n": 0 if name == "base" else len(news_rows), "meta": {"model": MODEL, "test": "twitter-financial-news-sentiment"}})
    except Exception as e: print("could not post news result:", e)
    return correct / len(news_test)

def evaluate(model, tok, name, bs=__BS__):
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    NEWS[name] = news_eval(model, tok, name)
    correct, answers, t0 = 0, [], time.time()
    for i in range(0, len(test), bs):
        batch = test[i:i + bs]
        enc = tok([chat(tok, q) for q, _ in batch], return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=__GEN_TOKENS__, do_sample=False, pad_token_id=tok.pad_token_id)
        for (q, gold), t in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            p = number(t); ok = p is not None and abs(p - float(gold)) < 1e-6
            correct += ok; answers.append({"q": q, "gold": gold, "pred": p, "ok": bool(ok), "out": t})
    print(f"{name:16s} {correct}/{len(test)} = {correct / len(test):.1%}  ({time.time() - t0:.0f}s)")
    put_json(answers, f"answers_{name}.json")          # every answer kept for review
    try:
        requests.post(f"{URL}/api/ft/result", timeout=60, json={"join_code": JOIN_CODE, "variant": TAG + name, "correct": correct, "total": len(test),
            "train_n": {"base": 0, "sft": len(sft_rows), "sft_dpo": len(pairs), "self_train": len(unlab)}.get(name), "meta": {"model": MODEL, "test": TEST_NAME, "answers": answers}})
    except Exception as e: print("could not post result:", e)
    return correct / len(test)

def to_drive(write, what):
    # Google Drive can drop its connection on long runs. A failed save must never stop the run:
    # remount and retry once, else keep a local copy and carry on (scores still reach the dashboard).
    for attempt in (1, 2):
        try: write(SAVE); print("saved:", what); return
        except Exception as e:
            print(f"Drive save failed ({what}): {e}")
            try: drive.mount("/content/drive", force_remount=True)
            except Exception: pass
    try: os.makedirs("/content/local_save", exist_ok=True); write("/content/local_save"); print("kept locally instead:", what)
    except Exception as e: print("could not save", what, e)

def put_json(obj, fname):
    to_drive(lambda root: json.dump(obj, open(f"{root}/{fname}", "w")), fname)

def save(model, tok, name):
    to_drive(lambda root: (model.save_pretrained(f"{root}/{name}"), tok.save_pretrained(f"{root}/{name}")), f"adapter {name}")

R, NEWS = {}, {}

# live telemetry for the website (/training): a background thread posts stage, GPU, loss curve and scores every 30 s
import threading, subprocess
ST = {"stage": "loading", "t0": time.time(), "hist": []}
def _telemetry():
    while True:
        try:
            q = subprocess.run(["nvidia-smi", "--query-gpu=utilization.gpu,memory.used,memory.total", "--format=csv,noheader,nounits"],
                               capture_output=True, text=True).stdout.strip().split(", ")
            u, m, tot = float(q[0]), float(q[1]) / 1024, float(q[2]) / 1024
            mins = (time.time() - ST["t0"]) / 60
            ST["hist"] = (ST["hist"] + [[round(mins, 2), u, round(m, 2)]])[-240:]
            loss = None
            for nm, label in (("st", "self-training"), ("dt", "+ wrong answers"), ("tr", "refined")):
                t = globals().get(nm)
                if t is not None and getattr(t, "state", None) and t.state.log_history:
                    pts = [[h["step"], h["loss"]] for h in t.state.log_history if "loss" in h]
                    if pts: loss = {"stage": label, "pts": pts}; break
            requests.post(f"{URL}/api/ft/telemetry", timeout=20, json={"join_code": JOIN_CODE, "run": TAG + "run", "data": {
                "model": MODEL, "stage": ST["stage"], "elapsed_min": mins, "gpu": {"util": u, "mem": m, "total": tot},
                "gpu_hist": ST["hist"], "loss": loss, "R": R, "NEWS": NEWS}})
        except Exception as e:
            print("telemetry:", e)
        time.sleep(30)
threading.Thread(target=_telemetry, daemon=True).start()
print("live telemetry: https://data-refinery.primemax.workers.dev/training")

# 1) base model, no training
BASE_KNOWN = __BASE_KNOWN__     # (maths, news) from an earlier run on the same test, same greedy decoding
if BASE_KNOWN:
    R["base"], NEWS["base"] = BASE_KNOWN
    print(f"base (from the earlier run, same test): maths {R['base']:.1%} · news {NEWS['base']:.1%}")
else:
    ST["stage"] = "testing base"
    model, tok = load()
    R["base"] = evaluate(model, tok, "base")
    del model; torch.cuda.empty_cache()

from trl import SFTTrainer, SFTConfig
def lora(m):
    return FastLanguageModel.get_peft_model(m, r=16, lora_alpha=16, lora_dropout=0, bias="none",
        target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
        use_gradient_checkpointing="unsloth", random_state=SEED)
def sft_args(out):
    c = dict(dataset_text_field="text", packing=__PACKING__, per_device_train_batch_size=__TBS__, gradient_accumulation_steps=__ACC__,
        num_train_epochs=__SFT_EPOCHS__, learning_rate=__SFT_LR__, lr_scheduler_type="linear", warmup_ratio=0.05, bf16=True, logging_steps=20,
        optim="adamw_8bit", seed=SEED, output_dir=out, report_to="none", save_strategy="no")
    try: return SFTConfig(max_seq_length=MAX_LEN, **c)
    except TypeError: return SFTConfig(max_length=MAX_LEN, **c)

# 1b) control: the same fresh model trained the same way on RAW single votes (one random vote per problem,
#     unfiltered), so the gap between "raw" and "refined" is what the refinery's voting adds
if __RAW_ARM__:
    ST["stage"] = "training: raw single votes"
    model, tok = load(); model = lora(model); tok.padding_side = "right"
    raw_rows = [r for r in sft_rows if r.get("raw") and r.get("raw_answer")]
    rds = Dataset.from_list([{"text": chat(tok, r["question"], with_answer(r["raw"], r["raw_answer"]))} for r in raw_rows]).shuffle(seed=SEED)
    try: rt = SFTTrainer(model=model, tokenizer=tok, train_dataset=rds, args=sft_args("out_raw"))
    except TypeError: rt = SFTTrainer(model=model, processing_class=tok, train_dataset=rds, args=sft_args("out_raw"))
    rt.train(); ST["stage"] = "testing raw"
    wrong_raw = sum(1 for r in raw_rows if str(r["raw_answer"]) != str(r["final_answer"]))
    print(f"raw arm: {len(raw_rows)} single votes, {wrong_raw} of them disagree with the consensus")
    R["raw"] = evaluate(model, tok, "raw")
    del model, rt; torch.cuda.empty_cache()

# 2) SFT on refined answers
model, tok = load()
model = FastLanguageModel.get_peft_model(model, r=16, lora_alpha=16, lora_dropout=0, bias="none",
    target_modules=["q_proj", "k_proj", "v_proj", "o_proj", "gate_proj", "up_proj", "down_proj"],
    use_gradient_checkpointing="unsloth", random_state=SEED)
tok.padding_side = "right"
ds = Dataset.from_list([{"text": chat(tok, r["question"], with_answer(r["refined"], r["final_answer"]))} for r in sft_rows]
                     + [{"text": chat(tok, r["headline"], r["sentiment"], system=NEWS_SYSTEM)} for r in news_rows]).shuffle(seed=SEED)
common = dict(dataset_text_field="text", packing=__PACKING__, per_device_train_batch_size=__TBS__, gradient_accumulation_steps=__ACC__,
    num_train_epochs=__SFT_EPOCHS__, learning_rate=__SFT_LR__, lr_scheduler_type="linear", warmup_ratio=0.05, bf16=True, logging_steps=20,
    optim="adamw_8bit", seed=SEED, output_dir="out_sft", report_to="none", save_strategy="no")
try: cfg = SFTConfig(max_seq_length=MAX_LEN, **common)
except TypeError: cfg = SFTConfig(max_length=MAX_LEN, **common)
try: tr = SFTTrainer(model=model, tokenizer=tok, train_dataset=ds, args=cfg)
except TypeError: tr = SFTTrainer(model=model, processing_class=tok, train_dataset=ds, args=cfg)
ST["stage"] = "training: refined"
t0 = time.time(); tr.train(); ST["stage"] = "testing refined"; print(f"SFT trained in {time.time() - t0:.0f}s")
save(model, tok, "sft")
R["sft"] = evaluate(model, tok, "sft")

# 3) DPO on top of the SFT model: right answers (chosen) vs outvoted wrong answers (rejected)
from trl import DPOTrainer, DPOConfig
FastLanguageModel.for_training(model); tok.padding_side = "right"
pd = Dataset.from_list([{"prompt": chat(tok, p["question"]),
                         "chosen": with_answer(p["chosen"], p["final_answer"]) + tok.eos_token,
                         "rejected": with_answer(p["rejected"], p["rejected_answer"]) + tok.eos_token} for p in pairs]).shuffle(seed=SEED)
dcommon = dict(beta=0.1, per_device_train_batch_size=2, gradient_accumulation_steps=8, num_train_epochs=__DPO_EPOCHS__, learning_rate=__DPO_LR__,
    lr_scheduler_type="linear", warmup_ratio=0.1, bf16=True, logging_steps=10, optim="adamw_8bit", seed=SEED,
    output_dir="out_dpo", report_to="none", save_strategy="no", max_length=MAX_LEN, max_prompt_length=384)
dcfg = DPOConfig(**dcommon)
try: dt = DPOTrainer(model=model, ref_model=None, args=dcfg, train_dataset=pd, tokenizer=tok)
except TypeError: dt = DPOTrainer(model=model, ref_model=None, args=dcfg, train_dataset=pd, processing_class=tok)
ST["stage"] = "training: + wrong answers"
t0 = time.time(); dt.train(); ST["stage"] = "testing + wrong answers"; print(f"DPO trained in {time.time() - t0:.0f}s")
save(model, tok, "sft_dpo")
R["sft_dpo"] = evaluate(model, tok, "sft_dpo")

# 4) self-training on the unlabelled half: the model solves each problem, the consensus checks it,
#    and every wrong answer it wrote becomes a "rejected" example next to the refined one
for rnd in range(1, SELF_ROUNDS + 1):
    name = "self_train" if rnd == 1 else f"self_train{rnd}"
    ST["stage"] = f"self-training round {rnd}: solving"
    FastLanguageModel.for_inference(model); tok.padding_side = "left"
    own_pairs, own_right = [], 0
    # each round solves a different slice of the refined problems (SELF_N per round), so rounds stay short
    pool = unlab if SELF_N <= 0 else [unlab[(j + (rnd - 1) * SELF_N) % len(unlab)] for j in range(min(SELF_N, len(unlab)))]
    for i in range(0, len(pool), __BS__):
        batch = pool[i:i + __BS__]
        enc = tok([chat(tok, r["question"]) for r in batch], return_tensors="pt", padding=True).to("cuda")
        with torch.no_grad():
            out = model.generate(**enc, max_new_tokens=__GEN_TOKENS__, do_sample=True, temperature=0.7, pad_token_id=tok.pad_token_id)
        for r, t in zip(batch, tok.batch_decode(out[:, enc["input_ids"].shape[1]:], skip_special_tokens=True)):
            p = number(t)
            try: right = p is not None and abs(p - float(r["final_answer"])) < 1e-6
            except ValueError: right = False
            if right: own_right += 1
            else: own_pairs.append({"prompt": chat(tok, r["question"]), "chosen": with_answer(r["refined"], r["final_answer"]) + tok.eos_token,
                                    "rejected": t.strip() + tok.eos_token})
    print(f"round {rnd} self-check on {len(pool)} refined problems: {own_right} right, {len(own_pairs)} wrong -> taught from its own mistakes")
    put_json(own_pairs, f"{name}_pairs.json")
    if not own_pairs:
        break
    FastLanguageModel.for_training(model); tok.padding_side = "right"
    # each DPO run leaves its frozen reference copy (adapter "ref") on the model; drop it so the
    # next DPO trainer can make a fresh reference from the current model instead of crashing
    if "ref" in getattr(model, "peft_config", {}):
        model.set_adapter("default"); model.delete_adapter("ref")
    sargs = DPOConfig(**{**dcommon, "num_train_epochs": 1, "output_dir": f"out_{name}"})
    try: st = DPOTrainer(model=model, ref_model=None, args=sargs, train_dataset=Dataset.from_list(own_pairs).shuffle(seed=SEED), tokenizer=tok)
    except TypeError: st = DPOTrainer(model=model, ref_model=None, args=sargs, train_dataset=Dataset.from_list(own_pairs).shuffle(seed=SEED), processing_class=tok)
    ST["stage"] = f"self-training round {rnd}: training"
    t0 = time.time(); st.train(); ST["stage"] = f"testing self-training round {rnd}"; print(f"{name} done in {time.time() - t0:.0f}s")
    save(model, tok, name)
    R[name] = evaluate(model, tok, name)

print(f"\n{TEST_NAME} accuracy, Llama 3.1 8B Instruct")
for k, v in R.items(): print(f"  {k:10s} maths {v:.1%}" + (f" · news {NEWS[k]:.1%}" if NEWS.get(k) is not None else ""))
put_json({"maths": R, "news": NEWS}, "summary.json")
ST["stage"] = "finished"; time.sleep(35)        # let the last telemetry snapshot reach the website
print("Adapters and answers are in Google Drive. Shutting the runtime down to save compute units...")
'''.replace("__JOIN__", JOIN).replace("__MODEL__", CFG["model"]).replace("__TAG__", CFG["tag"]).replace("__SAVE__", CFG["save"]).replace("__FOURBIT__", CFG["four_bit"]).replace("__BS__", CFG["bs"]).replace("__TBS__", CFG["tbs"]).replace("__ACC__", CFG["acc"]).replace("__SPLIT__", CFG["split"]).replace("__RAW_ARM__", CFG["raw_arm"]).replace("__BASE_KNOWN__", CFG["base_known"]).replace("__GEN_TOKENS__", CFG["gen_tokens"]).replace("__SELF_N__", CFG["self_n"]).replace("__PACKING__", CFG["packing"]).replace("__SELF_ROUNDS__", CFG["self_rounds"]).replace("__SFT_EPOCHS__", CFG["sft_epochs"]).replace("__SFT_LR__", CFG["sft_lr"]).replace("__DPO_EPOCHS__", CFG["dpo_epochs"]).replace("__DPO_LR__", CFG["dpo_lr"])

guarded = ("!pip install -q unsloth > /dev/null 2>&1\n\nimport time, traceback\ntry:\n"
           + "\n".join(("    " + l) if l.strip() else l for l in body.strip("\n").splitlines())
           + "\nexcept BaseException:\n    traceback.print_exc()\n    print('Run failed; shutting the runtime down anyway to save compute units.')"
           + "\nfinally:\n    time.sleep(20)\n    from google.colab import runtime\n    runtime.unassign()   # deletes the runtime: nothing keeps billing")
nb = {"cells": [
    {"cell_type": "markdown", "metadata": {}, "source": f"# Data Refinery · fine-tune on refined maths + news, {CFG['title']}, Colab L4\n"
     "**Runtime → Change runtime type → L4 GPU**, then run the cell. Approve the Google Drive popup at the start. "
     f"About {CFG['time']}. Adapters and every answer are saved to `MyDrive/data-refinery/{CFG['save']}`; the runtime then deletes itself."},
    {"cell_type": "code", "metadata": {}, "execution_count": None, "outputs": [], "source": guarded}],
    "metadata": {"accelerator": "GPU", "colab": {"gpuType": "L4", "provenance": []},
                 "kernelspec": {"display_name": "Python 3", "name": "python3"}, "language_info": {"name": "python"}},
    "nbformat": 4, "nbformat_minor": 0}
out = ROOT / "data" / ("refinery_finetune_L4.ipynb" if SIZE == "8b" else f"refinery_finetune_{SIZE}_L4.ipynb")
out.write_text(json.dumps(nb, indent=1), encoding="utf-8")
print("wrote", out)
