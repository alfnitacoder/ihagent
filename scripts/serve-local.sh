#!/bin/sh
# Start the local mlx_lm OpenAI-compatible server used by: hfagent --local
MODEL="${HFAGENT_LOCAL_MODEL:-$HOME/.cache/huggingface/mlx/qwen2.5-0.5b-4bit}"
PORT="${HFAGENT_LOCAL_PORT:-1234}"
exec "$(dirname "$0")/../.venv/bin/python" -m mlx_lm server --model "$MODEL" --port "$PORT"
