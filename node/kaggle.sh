#!/bin/bash
# One-shot node launcher for a Kaggle T4 x2 session.
#   !curl -s https://data-refinery.primemax.workers.dev/kaggle.sh | bash -s <name-prefix> <join-code> [tasks]
# Starts one Ollama per GPU and four nodes: odd-numbered nodes on GPU 0, even-numbered on GPU 1.
# Each Ollama answers 2 requests at once (OLLAMA_NUM_PARALLEL), so the two nodes on a GPU don't queue.
# Safe to rerun: it restarts Ollama and the nodes, and each node rejoins with its saved identity.
PREFIX=${1:-kaggle}
CODE=$2
TASKS=${3:-gsm8k,news}
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

# Default: the Llama trio (any node may use any of them). Agent task: one model per node, five families, no 1B,
# so every item gets five votes from five different families (GPU 0: nodes 1, 3, 5; GPU 1: nodes 2, 4).
MODELS_ALL="llama3.2:1b llama3.2:3b llama3.1:8b"
NODE_MODELS=("" "" "" "" "")
case "$TASKS" in *agent*)
  NODE_MODELS=("llama3.2:3b" "ministral-3:3b" "qwen3.5:4b" "phi4-mini" "gemma4:e4b")   # Meta, Mistral, Alibaba, Microsoft, Google
  MODELS_ALL="${NODE_MODELS[*]}" ;;
esac
echo "== 3/4 models (first time ~3-5 min)"
for m in $MODELS_ALL; do
  ollama pull "$m" >/dev/null 2>&1 && echo "  $m ok" || { echo "  $m FAILED"; exit 1; }
done
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

echo "== 4/4 nodes"
curl -fsSL "$URL/node.py" -o node.py || { echo "could not download node.py"; exit 1; }
# Each node runs in a restart loop, so a crash never ends the run: it rejoins with its saved identity.
start() {   # $1 name, $2 Ollama port, $3 model list for this node (empty = node.py default)
  local M=""; [ -n "$3" ] && M="--models '$3' --weights 1"
  nohup bash -c "while true; do python -u node.py --url '$URL' --name '$1' --join-code '$CODE' --ollama 'http://127.0.0.1:$2' --tasks '$TASKS' $M; echo '[restart in 30s]'; sleep 30; done" >"$1.log" 2>&1 &
}
start "$PREFIX-1" 11434 "${NODE_MODELS[0]}"
start "$PREFIX-2" 11435 "${NODE_MODELS[1]}"
start "$PREFIX-3" 11434 "${NODE_MODELS[2]}"
start "$PREFIX-4" 11435 "${NODE_MODELS[3]}"
start "$PREFIX-5" 11434 "${NODE_MODELS[4]}"          # 5 nodes: tasks that need 5 independent votes per item
sleep 3
echo "Running. Dashboard: $URL   (stop = stop the session)"
tail -f "$PREFIX-1.log" "$PREFIX-2.log" "$PREFIX-3.log" "$PREFIX-4.log" "$PREFIX-5.log"
