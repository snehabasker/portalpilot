#!/bin/bash
# Serve the fine-tuned Qwen2-VL-2B click policy (ui-click-rl, merged checkpoint) for PortalPilot's qwen tier.
# Same engine flags as ui-click-rl/serving/scripts/wsl/serve_vllm.sh, except: listens on 0.0.0.0 so the worker
# container can reach it, one image per prompt (the grounder sends one screenshot), smaller max-num-seqs.
#   wsl -d Ubuntu -- bash scripts/serve_qwen_wsl.sh          (env: PORT=8001 GPU_UTIL=0.85 MODEL_DIR=...)
set -euo pipefail
M="${MODEL_DIR:-$HOME/work/ui_click_grpo/serving/models/merged-clean}"
export PATH="$HOME/venvs/vllm/bin:$PATH"
command -v gcc >/dev/null || export PATH="$HOME/toolchain/bin:$PATH"
export CC="${CC:-gcc}"
export VLLM_USE_FLASHINFER_SAMPLER="${VLLM_USE_FLASHINFER_SAMPLER:-0}"  # requests are greedy; sampler choice is moot
mkdir -p "$HOME/work/logs"
exec vllm serve "$M" \
  --served-model-name merged-clean --dtype bfloat16 \
  --max-model-len 4096 --max-num-seqs "${MAX_SEQS:-8}" \
  --gpu-memory-utilization "${GPU_UTIL:-0.85}" \
  --limit-mm-per-prompt '{"image":1}' --mm-processor-kwargs '{"max_pixels":1003520}' \
  --enable-prefix-caching \
  --host 0.0.0.0 --port "${PORT:-8001}"
