#!/bin/bash
# One-shot node launcher for a Kaggle T4 x2 session.
#   !curl -s https://data-refinery.primemax.workers.dev/kaggle.sh | bash -s <name-prefix> <join-code>
# Starts one Ollama per GPU and four nodes: odd-numbered nodes on GPU 0, even-numbered on GPU 1.
# Each Ollama answers 2 requests at once (OLLAMA_NUM_PARALLEL), so the two nodes on a GPU don't queue.
# Safe to rerun: it restarts Ollama and the nodes, and each node rejoins with its saved identity.
PREFIX=${1:-kaggle}
CODE=$2
URL=https://data-refinery.primemax.workers.dev
cd /kaggle/working 2>/dev/null || cd ~

if [ -z "$CODE" ]; then echo "usage: bash -s <name-prefix> <join-code>"; exit 1; fi

echo "== 1/4 Ollama install"
if ! command -v ollama >/dev/null; then
  apt-get -qq install -y zstd pciutils >/dev/null 2>&1
  curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1
fi
ollama --version || { echo "Ollama install failed. Is Internet on in Session options?"; exit 1; }

echo "== 2/4 one Ollama per GPU"
pkill -f "node.py" 2>/dev/null
pkill -f "ollama serve" 2>/dev/null
sleep 2
export OLLAMA_NUM_PARALLEL=2 OLLAMA_MAX_LOADED_MODELS=3
CUDA_VISIBLE_DEVICES=0 OLLAMA_HOST=127.0.0.1:11434 nohup ollama serve >ollama0.log 2>&1 &
CUDA_VISIBLE_DEVICES=1 OLLAMA_HOST=127.0.0.1:11435 nohup ollama serve >ollama1.log 2>&1 &
sleep 5

echo "== 3/4 models (first time ~3-5 min)"
for m in llama3.2:1b llama3.2:3b llama3.1:8b; do
  ollama pull "$m" >/dev/null 2>&1 && echo "  $m ok" || { echo "  $m FAILED"; exit 1; }
done
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

echo "== 4/4 nodes"
curl -fsSL "$URL/node.py" -o node.py || { echo "could not download node.py"; exit 1; }
# Each node runs in a restart loop, so a crash never ends the run: it rejoins with its saved identity.
start() { nohup bash -c "while true; do python -u node.py --url '$URL' --name '$1' --join-code '$CODE' --ollama 'http://127.0.0.1:$2'; echo '[restart in 30s]'; sleep 30; done" >"$1.log" 2>&1 & }
start "$PREFIX-1" 11434
start "$PREFIX-2" 11435
start "$PREFIX-3" 11434
start "$PREFIX-4" 11435
sleep 3
echo "Running. Dashboard: $URL   (stop = stop the session)"
tail -f "$PREFIX-1.log" "$PREFIX-2.log" "$PREFIX-3.log" "$PREFIX-4.log"
