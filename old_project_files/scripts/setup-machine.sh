#!/usr/bin/env bash
# Install hfagent on a new machine: Python venv, Ollama, and a local model.
#
# Usage (from the copied repo, or via this script's path):
#   ./scripts/setup-machine.sh
#   ./scripts/setup-machine.sh --model qwen2.5-coder:7b
#   ./scripts/setup-machine.sh --skip-ollama          # venv only
#   ./scripts/setup-machine.sh --skip-model           # Ollama + venv, no pull
#   ./scripts/setup-machine.sh --dev                  # also install pytest
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
MODEL="${HFAGENT_SETUP_MODEL:-qwen2.5-coder:7b}"
SKIP_OLLAMA=0
SKIP_MODEL=0
DEV=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --model)
      MODEL="${2:?--model needs a name, e.g. qwen2.5-coder:7b}"
      shift 2
      ;;
    --skip-ollama) SKIP_OLLAMA=1; shift ;;
    --skip-model) SKIP_MODEL=1; shift ;;
    --dev) DEV=1; shift ;;
    -h|--help)
      cat <<'HELP'
Install hfagent on a new machine: Python venv, Ollama, and a local model.

  ./scripts/setup-machine.sh
  ./scripts/setup-machine.sh --model qwen2.5-coder:7b
  ./scripts/setup-machine.sh --skip-ollama     # venv only
  ./scripts/setup-machine.sh --skip-model      # Ollama + venv, no pull
  ./scripts/setup-machine.sh --dev             # also install pytest
HELP
      exit 0
      ;;
    *)
      echo "unknown flag: $1 (try --help)" >&2
      exit 2
      ;;
  esac
done

log() { printf '\n==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

need_python() {
  local py=""
  for cand in python3.12 python3.11 python3.10 python3; do
    if command -v "$cand" >/dev/null 2>&1; then
      py="$cand"
      break
    fi
  done
  [[ -n "$py" ]] || die "Python 3.10+ is required. Install python3 and re-run."
  local ver
  ver="$("$py" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
  "$py" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)' \
    || die "Python $ver is too old (need 3.10+)."
  echo "$py"
}

install_ollama() {
  if command -v ollama >/dev/null 2>&1; then
    log "Ollama already installed: $(command -v ollama)"
    return
  fi
  case "$(uname -s)" in
    Darwin|Linux)
      log "Installing Ollama"
      curl -fsSL https://ollama.com/install.sh | sh
      ;;
    MINGW*|MSYS*|CYGWIN*)
      die "On Windows use PowerShell:  powershell -ExecutionPolicy Bypass -File scripts/setup-machine.ps1"
      ;;
    *)
      die "Install Ollama by hand on this OS, then re-run. https://ollama.com/download"
      ;;
  esac
  command -v ollama >/dev/null 2>&1 || die "Ollama install finished but 'ollama' is not on PATH."
}

ensure_ollama_running() {
  if curl -fsS --max-time 2 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    return
  fi
  log "Starting Ollama server"
  if [[ "$(uname -s)" == "Darwin" ]] && [[ -x /Applications/Ollama.app/Contents/MacOS/Ollama ]]; then
    open -a Ollama || true
  fi
  nohup ollama serve >/tmp/hfagent-ollama-serve.log 2>&1 &
  local i
  for i in $(seq 1 30); do
    if curl -fsS --max-time 1 http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  die "Ollama did not start. Check /tmp/hfagent-ollama-serve.log"
}

pull_model() {
  if ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$MODEL"; then
    log "Model already present: $MODEL"
    return
  fi
  log "Pulling Ollama model $MODEL (this can take a while)"
  ollama pull "$MODEL"
}

setup_venv() {
  local py="$1"
  log "Creating venv with $py at $ROOT/.venv"
  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    "$py" -m venv "$ROOT/.venv"
  fi
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip
  if [[ "$DEV" -eq 1 ]]; then
    "$ROOT/.venv/bin/pip" install -e "$ROOT[dev]"
  else
    "$ROOT/.venv/bin/pip" install -e "$ROOT"
  fi
}

[[ -f "$ROOT/pyproject.toml" ]] || die "Run this from a copy of the hfagent repo (missing pyproject.toml)."

PY="$(need_python)"
log "Using $PY ($("$PY" --version 2>&1))"

if [[ "$SKIP_OLLAMA" -eq 0 ]]; then
  install_ollama
  ensure_ollama_running
  if [[ "$SKIP_MODEL" -eq 0 ]]; then
    pull_model
  fi
else
  log "Skipping Ollama (--skip-ollama)"
fi

setup_venv "$PY"

cat <<EOF

Setup complete.

Run the agent (offline, local model):
  $ROOT/.venv/bin/hfagent --ollama $MODEL

Classic line REPL:
  $ROOT/.venv/bin/hfagent --console --ollama $MODEL

Cloud instead of Ollama:
  export HF_TOKEN=hf_...
  $ROOT/.venv/bin/hfagent
EOF
