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
| `/clear` | reset the conversation |
| `/exit` | quit |

## Tools & permissions

| Tool | Purpose | Approval |
| :--- | :--- | :--- |
| `list_dir` | list a directory | — |
| `read_file` | read a text file | — |
| `grep` | regex search across files | — |
| `write_file` | create/overwrite a file | **y/N** |
| `run_command` | shell command (build, tests, git) | **y/N** |

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

hfagent auto-detects installed Ollama models — GGUFs pulled from the HF Hub
(`hf.co/...`) work too:

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

## Roadmap

- **v0.2** — markdown streaming renderer, session save/resume, per-tool config
- **v0.3** — opencode-style TUI (panes, diff preview before approve)
- **v0.4** — sub-agents, smolagents interop, benchmark hooks for the course challenge
