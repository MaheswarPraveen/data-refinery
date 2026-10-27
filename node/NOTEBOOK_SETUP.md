# Running a node on Kaggle or Colab

Coordinator and dashboard: **https://data-refinery.primemax.workers.dev**

You also need the **join code**, kept in `data/join_code.txt` on your laptop (gitignored). Without it the coordinator refuses to register a node, so strangers can't join and pollute the data.

## Kaggle

### Before the cells
1. kaggle.com → **Create → New Notebook**.
2. Right-hand panel → **Session options**:
   - **Accelerator → GPU T4 x2**
   - **Internet → On**. This needs your phone number verified in Kaggle settings; without internet Ollama can't download anything.

### Cell 1: install Ollama (about 1 minute)
Ollama is the program that runs Llama on the GPU.
```python
!apt-get -qq install -y zstd pciutils > /dev/null
!curl -fsSL https://ollama.com/install.sh | sh
```

### Cell 2: start Ollama and download the 3 Llama models (about 3–5 minutes, ~7 GB)
```python
import subprocess, time
subprocess.Popen(["ollama", "serve"], stdout=open("ollama.log", "w"), stderr=subprocess.STDOUT)
time.sleep(5)
!ollama pull llama3.2:1b
!ollama pull llama3.2:3b
!ollama pull llama3.1:8b
```

### Cell 3: download the node script and start working
```python
!wget -q https://data-refinery.primemax.workers.dev/node.py -O node.py
!python -u node.py --url https://data-refinery.primemax.workers.dev --name kaggle-1 --join-code YOUR_JOIN_CODE
```
It prints one line per job, for example `#12 job 40 llama3.2:3b/teacher -> 18 (2.3s)`. Within seconds the node shows up on the dashboard.

**To stop it:** press the ■ stop button on the cell.

**Use a different name for each machine:** `kaggle-1`, `kaggle-2`, `colab-1`. The name decides the node's identity: `<name>.node.json` stores its secret, so rerunning Cell 3 rejoins as the same node.

### Run it without keeping the browser open
Use **Save Version → Save & Run All (Commit)**. Kaggle then runs the whole notebook in the background for up to 12 hours, even with the tab closed. It uses your GPU quota the whole time.

## Colab
**Runtime → Change runtime type → T4 GPU**, then the same 3 cells with `--name colab-1`. Colab has no background mode: keep the tab open, and it may disconnect after a few hours.

## Good to know
- **Kaggle GPU quota is about 30 hours per week per account,** shared by all your notebooks.
- If a session dies, nothing is lost. Unfinished jobs go back to the pool after 5 minutes, and rerunning the cells rejoins as the same node.
- `--weights 1,1,0.3` means the 8B model is picked about 1 time in 8. 8B is also the model we fine-tune, so its votes are kept rare.
- To check the GPU is being used, run `!nvidia-smi` in a new cell while the node runs. An `ollama` process should be using memory.
