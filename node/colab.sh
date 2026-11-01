#!/bin/bash
# Mixed-family run on one big GPU (Colab A100): all 5 nodes.
#   !curl -s https://data-refinery.primemax.workers.dev/colab.sh | bash -s <unused-prefix> <join-code> [task] [a100|l4]
# On an L4 (24 GB) each node runs 2 workers and Ollama keeps fewer models loaded at once.
# A node = one person's computer: 2 small models and a random one answers each vote.
# Nodes never share a model, so the 5 votes on a problem (nodes 1-5) always come from 5 different families.
# Each node runs several workers under one identity to keep the GPU busy.
CODE=$2
TASK=${3:-gsm8k_mixed}
GPU=${4:-a100}
if [ "$GPU" = "l4" ]; then WORKERS=2; LOADED=7; else WORKERS=4; LOADED=10; fi
URL=https://data-refinery.primemax.workers.dev
# 5 nodes x 2 fast models = 10 families (all on the A100: no slow GPU holding problems back).
NODE1="llama3.2:3b,phi4-mini"                                              # Meta, Microsoft
NODE2="gemma4:e4b,granite3.3:2b"                                            # Google, IBM
NODE3="ministral-3:3b,hf.co/nvidia/NVIDIA-Nemotron-3-Nano-4B-GGUF:Q4_K_M"   # Mistral, NVIDIA
NODE4="hf.co/ggml-org/SmolLM3-3B-GGUF:Q4_K_M,falcon3:3b"  # Hugging Face, TII (Apriel dropped: its llamafied GGUF scored ~30%, a broken conversion)
NODE5="hf.co/zai-org/glm-edge-4b-chat-gguf:Q4_K_M,qwen3.5:4b"              # Zhipu, Alibaba
ALL="$NODE1,$NODE2,$NODE3,$NODE4,$NODE5"
cd /content 2>/dev/null || cd ~
if [ -z "$CODE" ]; then echo "usage: bash -s <prefix> <join-code> [task]"; exit 1; fi

echo "== 1/4 Ollama install"
if ! command -v ollama >/dev/null; then
  apt-get -qq install -y zstd pciutils >/dev/null 2>&1
  curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1
fi
ollama --version || { echo "Ollama install failed"; exit 1; }

echo "== 2/4 Ollama (10 models, $((5 * WORKERS)) requests at once)"
pkill -f "node.py" 2>/dev/null; pkill -f "ollama serve" 2>/dev/null; sleep 2
# Small context + compressed KV cache so all models and their parallel slots fit at once (no swapping).
OLLAMA_CONTEXT_LENGTH=4096 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=$((5 * WORKERS)) \
  OLLAMA_MAX_LOADED_MODELS=$LOADED OLLAMA_KEEP_ALIVE=-1 nohup ollama serve >ollama.log 2>&1 &
sleep 5

echo "== 3/4 models (first time ~5 min)"
for m in ${ALL//,/ }; do
  ollama pull "$m" >/dev/null 2>&1 && echo "  ok  $m" || { echo "  FAILED $m"; exit 1; }
done
nvidia-smi --query-gpu=name,memory.total --format=csv,noheader
for m in ${ALL//,/ }; do  # warm every model into GPU memory
  curl -s localhost:11434/api/generate -d "{\"model\":\"$m\",\"prompt\":\"hi\",\"stream\":false,\"options\":{\"num_predict\":1}}" >/dev/null
done
ollama ps

echo "== 4/4 nodes"
curl -fsSL "$URL/node.py" -o node.py || { echo "could not download node.py"; exit 1; }
worker() { nohup bash -c "while true; do python -u node.py --url '$URL' --name '$1' --join-code '$CODE' --models '$2' --weights 1,1 --tasks '$TASK'; echo '[restart in 30s]'; sleep 30; done" >"$1-w$3.log" 2>&1 & }
# One identity per node: the first worker registers, the others reuse its saved identity.
start_node() {
  worker "$1" "$2" 1
  for _ in $(seq 1 30); do [ -f "$1.node.json" ] && break; sleep 1; done
  for w in $(seq 2 $WORKERS); do worker "$1" "$2" "$w"; done
}
start_node node-1 "$NODE1"
start_node node-2 "$NODE2"
start_node node-3 "$NODE3"
start_node node-4 "$NODE4"
start_node node-5 "$NODE5"
sleep 3
echo "Running nodes 1-5 ($WORKERS workers each) on $TASK. Dashboard: $URL/gsm8k-mixed  (stop: Runtime > Disconnect and delete runtime)"
tail -f node-*-w1.log
