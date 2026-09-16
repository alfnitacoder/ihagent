# hfagent — a terminal coding agent

`hfagent` is a minimal, opencode-style CLI coding agent powered by Hugging Face
Inference (OpenAI-compatible router). It is a hands-on companion to the
[Hugging Face AI Agents Course](https://huggingface.co/learn/agents-course): it
implements the course's core loop — **Thought → Action → Observation** — as a
real tool-calling agent that can read, search, edit files and run commands in
your project.

## Architecture

```
┌────────────┐   streamed   ┌───────────────┐   tool_call    ┌────────────────┐
│  terminal  │◄─────────────│   agent.py    │───────────────►│   tools/*      │
│  (rich)    │   text       │  agent loop   │  observation   │  read / write  │
└────────────┘              └───────┬───────┘◄───────────────│  grep / shell  │
                                    │ messages               └────────────────┘
                                    ▼
                     https://router.huggingface.co/v1
                     (Qwen, Llama, … or any OpenAI-compatible API)
```

### Course concepts → implementation

| Course concept | In hfagent |
| :--- | :--- |
| **Thought** | streamed assistant text before/with a tool call |
| **Action** | `tool_calls` — JSON function calling |
| **Observation** | `role: "tool"` messages appended back into the conversation |
| **Tools (python functions)** | `src/hfagent/tools/*`, each exposing a JSON-schema spec |
| **Max iterations guard** | `--max-steps` (default 25) |

## Quickstart

```bash
cd ~/hf
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
export HF_TOKEN=hf_...        # https://huggingface.co/settings/tokens
.venv/bin/hfagent
```

One-shot prompt from any project directory:

```bash
.venv/bin/hfagent -p "list the files here and explain what this project is"
```

## REPL commands

| Command | Effect |
| :--- | :--- |
| `/help` | show commands |
| `/tools` | list registered tools |
| `/model <id>` | switch model, e.g. `Qwen/Qwen3-32B` |
| `/sessions` | list saved sessions |
| `/resume [name]` | load a saved session |
| `/clear` | reset the conversation |
| `/exit` | quit |

## Tools & permissions

| Tool | Purpose | Approval |
| :--- | :--- | :--- |
| `list_dir` | list a directory | — |
| `read_file` | read a text file | — |
| `grep` | regex search across files | — |
| `write_file` | create/overwrite a file | **y/N + diff** |
| `edit_file` | replace exact text in a file | **y/N + diff** |
| `run_command` | shell command (build, tests, git) | **y/N** |
| `ssh_run` | one command on an SSH host (aliases from ~/.ssh/config) | **y/N** |

Approval prompts protect against unintended writes and command execution.
Skip them at your own risk with `--yolo` (or `HFAGENT_YOLO=1`).

## Configuration

| Env var | Default | Meaning |
| :--- | :--- | :--- |
| `HF_TOKEN` | — | Hugging Face token (required for the HF router) |
| `HFAGENT_LOCAL_MODEL` | `~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit` | model path used with `--local` |
| `HF_BASE_URL` | `https://router.huggingface.co/v1` | any OpenAI-compatible endpoint |
| `HFAGENT_MODEL` | `Qwen/Qwen2.5-Coder-32B-Instruct` | model id |
| `HFAGENT_MAX_STEPS` | `25` | agent loop iteration cap |

## Local models — run fully offline

### Ollama (recommended)

Use the official Qwen2.5-Coder model — its tool calling is reliable:

```bash
ollama pull qwen2.5-coder:7b     # ~4.7 GB
.venv/bin/hfagent --ollama       # auto-detects the newest installed model
```

hfagent auto-detects installed Ollama models (newest first) — GGUFs pulled
from the HF Hub (`hf.co/...`, e.g. 'abliterated' variants) also work, but
their converted templates tend to fabricate results instead of calling
tools; hfagent strips faked `<tool_response>` blocks and nudges the model
back to its tools automatically.

Switch models any time — names resolve by exact or unique-substring match:

```bash
.venv/bin/hfagent --ollama qwen2.5-coder:7b      # official
.venv/bin/hfagent --ollama qwen2.5-coder-abliterated
```

Inside the REPL, `/models` lists what is installed (→ marks the active one),
`/model <id>` switches mid-session. Pin a default with
`HFAGENT_LOCAL_MODEL` in your shell profile.

```bash
.venv/bin/hfagent --ollama          # first installed model
.venv/bin/hfagent --ollama <model>  # or name one explicitly
```

Some GGUF chat templates don't produce structured tool calls; hfagent parses
them from text automatically (`<tool_call>{...}</tool_call>`, `<tool>...</tool>`,
and fenced ```json blocks with `name`/`arguments`).

### Apple Silicon / MLX

Convert any model you downloaded to MLX 4-bit and serve it:

```bash
.venv/bin/python -m mlx_lm convert \
  --hf-path Qwen/Qwen2.5-0.5B-Instruct --quantize \
  --mlx-path ~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit
./scripts/serve-local.sh          # OpenAI-compatible server on :1234
```

Then just use the `--local` flag — no token, no cloud:

```bash
.venv/bin/hfagent --local
```

Tips for small local models:
- hfagent defaults to `temperature 0.0` (greedy) — small models emit far more
  reliable tool calls that way; raise it with `--temperature` if you want variety.
- Tiny models (0.5B) need terse tool descriptions; they skip tools when the
  prompt is too wordy. A 7B-class model (e.g. Qwen2.5-Coder-7B) is the practical
  minimum for reliable agent loops. Any OpenAI-compatible server also works via
  `--base-url`: vLLM, llama.cpp, mlx_lm.server.

## Remote hosts (SSH)

`ssh_run` executes single commands on hosts defined in `~/.ssh/config`:

```
❯ check disk space on wantok20
→ action: ssh_run  $ ssh wantok20 'df -h'
```

It is non-interactive by design (one command per call, `BatchMode=yes`,
`ConnectTimeout=10`) — interactive sessions would hang the agent. Keep your
keys in `ssh-agent` if they have passphrases.

## Sessions

Every turn auto-saves to `~/.hfagent/sessions/<project>/`. Resume where you
left off:

```bash
.venv/bin/hfagent --ollama --resume       # continue the most recent session
```

or interactively with `/sessions` and `/resume <name>`.

## Roadmap

- **v0.3** — opencode-style TUI (panes, markdown streaming), LSP-ish project context
- **v0.4** — sub-agents, smolagents interop, benchmark hooks for the course challenge
