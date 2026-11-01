#!/bin/bash
# Judge node on Kaggle T4 x2: Meta's open-weight Muse Glimmer 30B writes the hints.
#   !curl -s https://data-refinery.primemax.workers.dev/kaggle_judge.sh | bash -s <join-code>
# The 17 GB model is split across both T4s (32 GB). Free: no API, no card.
CODE=$1
URL=https://data-refinery.primemax.workers.dev
GGUF_URL=https://huggingface.co/meta-models/Muse-Glimmer-30B-GGUF/resolve/main/Muse-Glimmer-30B-KQuant-17GB-Q4_K_M.gguf
cd /kaggle/working 2>/dev/null || cd ~
if [ -z "$CODE" ]; then echo "usage: bash -s <join-code>"; exit 1; fi

echo "== 1/4 Ollama install"
if ! command -v ollama >/dev/null; then
  apt-get -qq install -y zstd pciutils >/dev/null 2>&1
  curl -fsSL https://ollama.com/install.sh | sh >/dev/null 2>&1
fi
ollama --version || { echo "Ollama install failed. Is Internet on in Session options?"; exit 1; }

echo "== 2/4 Ollama (both GPUs)"
pkill -f "judge.py" 2>/dev/null; pkill -f "ollama serve" 2>/dev/null; sleep 2
OLLAMA_CONTEXT_LENGTH=4096 OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0 OLLAMA_NUM_PARALLEL=2 OLLAMA_KEEP_ALIVE=-1 \
  nohup ollama serve >ollama.log 2>&1 &
sleep 5

echo "== 3/4 Muse Glimmer 30B (17 GB download, ~3-6 min)"
if ! ollama list | grep -q muse-glimmer; then
  [ -f glimmer.gguf ] || curl -fL -s -o glimmer.gguf "$GGUF_URL" || { echo "download failed"; exit 1; }
  printf 'FROM ./glimmer.gguf\n' > Modelfile
  ollama create muse-glimmer -f Modelfile >/dev/null 2>&1 || { echo "ollama create failed"; exit 1; }
fi
curl -s localhost:11434/api/generate -d '{"model":"muse-glimmer","prompt":"hi","stream":false,"options":{"num_predict":1}}' >/dev/null
ollama ps
nvidia-smi --query-gpu=index,memory.used,memory.total --format=csv,noheader

echo "== 4/4 judge node"
curl -fsSL "$URL/judge.py" -o judge.py || { echo "could not download judge.py"; exit 1; }
nohup bash -c "while true; do python -u judge.py --url '$URL' --name judge-glimmer --join-code '$CODE'; echo '[restart in 30s]'; sleep 30; done" >judge-1.log 2>&1 &
sleep 20
nohup bash -c "while true; do python -u judge.py --url '$URL' --name judge-glimmer --join-code '$CODE'; echo '[restart in 30s]'; sleep 30; done" >judge-2.log 2>&1 &
echo "Judge running (2 workers). Dashboard: $URL/gsm8k-mixed  (stop = stop the session)"
tail -f judge-1.log judge-2.log
