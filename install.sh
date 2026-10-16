#!/usr/bin/env bash
# Install IHAgent on macOS and Linux.
#
# From a clone of this repository:
#   ./install.sh
#   ./install.sh --uninstall
#
# One line (clones or updates the repo, then installs):
#   curl -fsSL https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.sh | bash
#   curl -fsSL https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.sh | bash -s -- --uninstall
#
# Python 3.10–3.13. Python 3.12 is preferred. A newer interpreter warns and
# still runs, because some wheels (Playwright, and PyObjC on macOS) may be missing.
#
# IHAGENT_HOME overrides the clone directory used when this script is not
# already inside a checkout (default: ~/ihagent).

# Streamed on stdin (curl | bash), bash has not read the whole file yet.
# Save the rest and re-exec so git and pip do not consume the script.
if [[ ! -f "${BASH_SOURCE[0]:-}" ]]; then
  tmp="$(mktemp "${TMPDIR:-/tmp}/ihagent-install.XXXXXX")" || exit 1
  cat > "$tmp"
  chmod +x "$tmp"
  bash "$tmp" "$@"
  status=$?
  rm -f "$tmp"
  exit "$status"
fi

set -euo pipefail

REPO_URL="https://github.com/alfnitacoder/ihagent.git"
REPO_BRANCH="master"

ROOT=""
PY=""

log() { printf '\n==> %s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
Install IHAgent on macOS or Linux.

  ./install.sh
  ./install.sh --uninstall

From the network (clones or updates ~/ihagent, unless IHAGENT_HOME is set,
or ~/.local/bin/ihagent already points at a checkout):

  curl -fsSL https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.sh | bash
  curl -fsSL https://raw.githubusercontent.com/alfnitacoder/ihagent/master/install.sh | bash -s -- --uninstall

Python 3.10–3.13 is required. Python 3.12 is preferred. Newer Pythons warn
and still run; some wheels may be missing.
EOF
}

UNINSTALL=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) UNINSTALL=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown flag: $1 (try --help)" ;;
  esac
done

case "$(uname -s)" in
  Darwin|Linux) ;;
  *) die "This installer is for macOS and Linux. On Windows, run install.ps1." ;;
esac

[[ -n "${HOME:-}" ]] || die "HOME is not set."

on_path() {
  local dir="$1" entry rest="${PATH:-}"
  while [[ -n "$rest" ]]; do
    entry="${rest%%:*}"
    if [[ "$entry" == "$dir" ]]; then
      return 0
    fi
    if [[ "$rest" == *:* ]]; then
      rest="${rest#*:}"
    else
      break
    fi
  done
  return 1
}

root_from_link() {
  local link="$HOME/.local/bin/ihagent" target
  [[ -L "$link" ]] || return 1
  target="$(readlink "$link")" || return 1
  case "$target" in
    */.venv/bin/ihagent)
      printf '%s\n' "${target%/.venv/bin/ihagent}"
      ;;
    *)
      return 1
      ;;
  esac
}

# In a checkout, install there and leave local edits alone.
# Piped (or a lone copy of this file): update the checkout the ihagent
# link already uses, otherwise clone into IHAGENT_HOME or ~/ihagent.
resolve_root() {
  local allow_clone="$1" script_dir dest=""
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  if [[ -f "$script_dir/ihagent/pyproject.toml" ]]; then
    ROOT="$script_dir"
    return 0
  fi
  if dest="$(root_from_link)"; then
    :
  fi
  if [[ -z "$dest" ]]; then
    dest="${IHAGENT_HOME:-$HOME/ihagent}"
  fi
  if [[ -f "$dest/ihagent/pyproject.toml" && -d "$dest/.git" ]]; then
    if [[ "$allow_clone" -eq 1 ]]; then
      log "Updating $dest"
      if ! git -C "$dest" pull --ff-only; then
        warn "git pull failed. Installing the copy already in $dest."
      fi
    fi
  elif [[ -f "$dest/ihagent/pyproject.toml" ]]; then
    log "Using existing checkout at $dest"
  elif [[ -e "$dest" ]]; then
    die "$dest exists but is not an IHAgent checkout. Set IHAGENT_HOME, or move that directory aside."
  elif [[ "$allow_clone" -eq 1 ]]; then
    command -v git >/dev/null 2>&1 || die "git is required to clone IHAgent. Install git and re-run."
    log "Cloning $REPO_URL into $dest"
    git clone --branch "$REPO_BRANCH" "$REPO_URL" "$dest" </dev/null
  else
    ROOT="$dest"
    return 0
  fi
  ROOT="$(cd "$dest" && pwd)"
}

pick_python() {
  local cand newer="" ver
  local cands=(python3.12 python3.13 python3.11 python3.10 python3 python)
  for cand in "${cands[@]}"; do
    command -v "$cand" >/dev/null 2>&1 || continue
    if "$cand" -c 'import sys; raise SystemExit(0 if (3, 10) <= sys.version_info[:2] <= (3, 13) else 1)'; then
      PY="$cand"
      return 0
    fi
    if [[ -z "$newer" ]] && "$cand" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] > (3, 13) else 1)'; then
      newer="$cand"
    fi
  done
  if [[ -n "$newer" ]]; then
    ver="$("$newer" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
    warn "Python $ver is newer than 3.13. Some wheels may be missing. Python 3.12 is the safest choice."
    PY="$newer"
    return 0
  fi
  die "Python 3.10–3.13 is required. Install Python 3.12 and re-run."
}

ensure_venv() {
  local want have venv_python="$ROOT/.venv/bin/python"
  want="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])')"
  if [[ -x "$venv_python" ]]; then
    have="$("$venv_python" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)"
    if [[ -n "$have" && "$have" != "$want" ]]; then
      log "Replacing .venv (Python $have) with Python $want"
      rm -rf "$ROOT/.venv"
    fi
  fi
  if [[ ! -x "$ROOT/.venv/bin/python" ]]; then
    log "Creating .venv with $PY (Python $want)"
    "$PY" -m venv "$ROOT/.venv" \
      || die "Could not create a virtualenv. On Debian/Ubuntu: sudo apt install python3-venv python3-pip"
  else
    log "Using existing .venv (Python $want)"
  fi
}

install_package() {
  local extras="browser"
  if [[ "$(uname -s)" == "Darwin" ]]; then
    extras="browser,iheye"
  fi
  log "Installing ihagent[$extras]"
  "$ROOT/.venv/bin/python" -m pip install --upgrade pip </dev/null
  (
    cd "$ROOT"
    "$ROOT/.venv/bin/python" -m pip install -e "ihagent[$extras]" </dev/null
  )
}

install_browser() {
  # Bundled Chromium. The agent still prefers an installed Google Chrome
  # when one is present, and falls back to this browser.
  log "Installing Playwright browser (chromium)"
  "$ROOT/.venv/bin/python" -m playwright install chromium </dev/null
}

write_user_env() {
  local dest="$HOME/.hfagent/.env"
  if [[ -f "$dest" ]]; then
    log "Leaving existing $dest unchanged"
    return 0
  fi
  mkdir -p "$HOME/.hfagent"
  cat > "$dest" <<'EOF'
# IHAgent user settings (~/.hfagent/.env).
# The installer creates this file once and never overwrites it.
# Blank values are ignored. HF_TOKEN is also read for the Hugging Face router.
# Hugging Face token: https://huggingface.co/settings/tokens
# Ollama Cloud key:   https://ollama.com/settings/keys

HF_API_KEY=
OLLAMA_API_KEY=
HF_BASE_URL=https://router.huggingface.co/v1
HFAGENT_MODEL=Qwen/Qwen2.5-Coder-32B-Instruct
EOF
  chmod 600 "$dest" || warn "Could not restrict permissions on $dest"
  log "Created $dest"
}

link_command() {
  local name="$1"
  local src="$ROOT/.venv/bin/$name"
  local dest="$HOME/.local/bin/$name"
  [[ -x "$src" ]] || die "Expected $src after install."
  mkdir -p "$HOME/.local/bin"
  if [[ -e "$dest" && ! -L "$dest" ]]; then
    warn "$dest exists and is not a symlink. Not replacing it."
    warn "Run $src, or move that file aside and re-run the installer."
    return 0
  fi
  ln -sfn "$src" "$dest"
  log "Linked $dest -> $src"
}

remove_link() {
  local name="$1" root="$2"
  local dest="$HOME/.local/bin/$name" target expect="$root/.venv/bin/$name"
  [[ -L "$dest" ]] || return 0
  target="$(readlink "$dest")" || return 0
  if [[ "$target" == "$expect" ]]; then
    rm -f "$dest"
    log "Removed $dest"
  else
    warn "Left $dest (it points at $target)."
  fi
}

do_uninstall() {
  resolve_root 0
  remove_link ihagent "$ROOT"
  remove_link hfagent "$ROOT"
  if [[ -d "$ROOT/.venv" ]]; then
    rm -rf "$ROOT/.venv"
    log "Removed $ROOT/.venv"
  else
    log "No virtualenv at $ROOT/.venv"
  fi
  printf 'Left %s in place (config, sessions, memory).\n' "$HOME/.hfagent"
}

do_install() {
  local need_path_note=0
  resolve_root 1
  [[ -f "$ROOT/ihagent/pyproject.toml" ]] || die "Missing $ROOT/ihagent/pyproject.toml."
  pick_python
  log "Using $PY ($("$PY" --version 2>&1))"
  ensure_venv
  install_package
  install_browser
  write_user_env
  link_command ihagent
  link_command hfagent
  if ! on_path "$HOME/.local/bin"; then
    need_path_note=1
  fi
  export PATH="$HOME/.local/bin:$PATH"
  hash -r 2>/dev/null || true
  log "ihagent --version"
  ihagent --version
  if [[ "$need_path_note" -eq 1 ]]; then
    cat <<EOF

~/.local/bin is not on PATH. Add this to ~/.bashrc, ~/.zshrc, or ~/.profile:

  export PATH="\$HOME/.local/bin:\$PATH"

Open a new terminal afterwards. Until then:

  $HOME/.local/bin/ihagent
EOF
  fi
  cat <<EOF

Edit ~/.hfagent/.env and set HF_API_KEY (Hugging Face) or OLLAMA_API_KEY (Ollama Cloud).
Then run:  ihagent
EOF
}

if [[ "$UNINSTALL" -eq 1 ]]; then
  do_uninstall
else
  do_install
fi
