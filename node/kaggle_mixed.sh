#!/bin/bash
# Mixed-family run on Kaggle T4 x2: nodes 4-5, one per GPU.
#   !curl -s https://data-refinery.primemax.workers.dev/kaggle_mixed.sh | bash -s <join-code> [task]
# A node = one person's computer: 3 small models and a random one answers each vote. Nodes 1-3 run on
# Colab (colab.sh); no two nodes share a model, so a problem's 5 votes come from 5 different families.
CODE=$1
TASK=${2:-gsm8k_mixed}
WORKERS=4
URL=https://data-refinery.primemax.workers.dev
NODE4="hf.co/ggml-org/SmolLM3-3B-GGUF:Q4_K_M,hf.co/bartowski/Spark-X2.6-4B-GGUF:Q4_K_M,hf.co/openbmb/MiniCPM5-2B-GGUF:Q4_K_M"  # Hugging Face, Spark-X, OpenBMB
NODE5="hf.co/zai-org/glm-edge-4b-chat-gguf:Q4_K_M,hf.co/mradermacher/Apriel-5B-Instruct-llamafied-GGUF:Q4_K_M,hf.co/DevQuasar/kakaocorp.kanana-1.5-2.1b-instruct-2505-GGUF:Q4_K_M"  # Zhipu, ServiceNow, Kakao (EXAONE 3.5 crashed in Ollama)
cd /kaggle/working 2>/dev/null || cd ~
if [ -z "$CODE" ]; then echo "usage: bash -s <join-code> [task]"; exit 1; fi

echo "== 1/4 Ollama install"
if ! command -v ollama >/dev/null; then
  apt-get -qq install -y zstd pciutils >/dev/null 2>&1
  curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1
fi
ollama --version || { echo "Ollama install failed. Is Internet on in Session options?"; exit 1; }

echo "== 2/4 one Ollama per GPU"
pkill -f "node.py" 2>/dev/null; pkill -f "ollama serve" 2>/dev/null; sleep 2
export OLLAMA_CONTEXT_LENGTH=4096 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=$WORKERS OLLAMA_MAX_LOADED_MODELS=3 OLLAMA_KEEP_ALIVE=-1
CUDA_VISIBLE_DEVICES=0 OLLAMA_HOST=127.0.0.1:11434 nohup ollama serve >ollama0.log 2>&1 &
CUDA_VISIBLE_DEVICES=1 OLLAMA_HOST=127.0.0.1:11435 nohup ollama serve >ollama1.log 2>&1 &
sleep 5

echo "== 3/4 models (first time ~3-5 min)"
for m in ${NODE4//,/ }; do OLLAMA_HOST=127.0.0.1:11434 ollama pull "$m" >/dev/null 2>&1 && echo "  ok  $m" || { echo "  FAILED $m"; exit 1; }; done
for m in ${NODE5//,/ }; do OLLAMA_HOST=127.0.0.1:11435 ollama pull "$m" >/dev/null 2>&1 && echo "  ok  $m" || { echo "  FAILED $m"; exit 1; }; done
nvidia-smi --query-gpu=index,name,memory.total --format=csv,noheader

echo "== 4/4 nodes"
curl -fsSL "$URL/node.py" -o node.py || { echo "could not download node.py"; exit 1; }
worker() { nohup bash -c "while true; do python -u node.py --url '$URL' --name '$1' --join-code '$CODE' --models '$2' --weights 1,1,1 --tasks '$TASK' --ollama 'http://127.0.0.1:$3'; echo '[restart in 30s]'; sleep 30; done" >"$1-w$4.log" 2>&1 & }
start_node() {  # one identity per node: first worker registers, the rest reuse it
  worker "$1" "$2" "$3" 1
  for _ in $(seq 1 30); do [ -f "$1.node.json" ] && break; sleep 1; done
  for w in $(seq 2 $WORKERS); do worker "$1" "$2" "$3" "$w"; done
}
start_node node-4 "$NODE4" 11434
start_node node-5 "$NODE5" 11435
sleep 3
echo "Running nodes 4-5 ($WORKERS workers each) on $TASK. Dashboard: $URL/gsm8k-mixed  (stop = stop the session)"
tail -f node-4-w1.log node-5-w1.log
