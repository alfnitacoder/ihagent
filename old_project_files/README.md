# IHAgent — InnovatelHub Solutions Ltd

IHAgent is a terminal coding agent for InnovatelHub Solutions Ltd, powered by Hugging Face
Inference (OpenAI-compatible router). It is a hands-on companion to the
[Hugging Face AI Agents Course](https://huggingface.co/learn/agents-course): it
implements the course's core loop — **Thought → Action → Observation** — as a
real tool-calling agent that can read, search, edit files and run commands in
your project.

On a real terminal it opens a **TUI** (chat pane, sessions/tools sidebar, live
markdown). Use `--console` for the classic line REPL, or `-p` for one-shot.

## Architecture

```
┌──────────────── TUI (textual) ────────────────┐
│  sessions │  chat (markdown stream)           │
│  tools    │  actions / observations           │
│           │  ❯ composer                       │
└───────────┴──────────┬────────────────────────┘
                       │ AgentUI events
                       ▼
              ┌────────────────┐   tool_call    ┌────────────────┐
              │   agent.py     │───────────────►│   tools/*      │
              │  agent loop    │  observation   │  read / write  │
              └───────┬────────┘◄───────────────│  grep / shell  │
                      │ messages                └────────────────┘
                      ▼
       https://router.huggingface.co/v1  (or Ollama / MLX)
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
.venv/bin/hfagent                 # TUI (default on a TTY)
.venv/bin/hfagent --console       # classic line REPL
```

One-shot prompt from any project directory:

```bash
.venv/bin/hfagent -p "list the files here and explain what this project is"
```

## TUI keys

| Key | Effect |
| :--- | :--- |
| `ctrl+b` | toggle sessions/tools sidebar |
| `ctrl+l` | clear the conversation |
| `ctrl+q` | quit |
| `up` / `down` | input history |
| `y` / `n` | approve or deny a gated tool |
| `f1` | show /help |

Click a session in the sidebar to resume it.

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

### Hugging Face GPU (cloud)

Log in once, then use the serverless router (default) or rent a dedicated endpoint:

```bash
./scripts/setup-huggingface.sh                 # login + router
./scripts/setup-huggingface.sh --gpu --yes     # dedicated GPU (billed while up)
source ~/.hfagent/hf.env
.venv/bin/hfagent --model Qwen/Qwen2.5-Coder-7B-Instruct
```

Windows: `powershell -ExecutionPolicy Bypass -File scripts\setup-huggingface.ps1`

Pause billing with `hf endpoints pause hfagent-coder`.
| `HFAGENT_LOCAL_MODEL` | `~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit` | model path used with `--local` |
| `HF_BASE_URL` | `https://router.huggingface.co/v1` | any OpenAI-compatible endpoint |
| `HFAGENT_MODEL` | `Qwen/Qwen2.5-Coder-32B-Instruct` | model id |
| `HFAGENT_MAX_STEPS` | `25` | agent loop iteration cap |

Flags: `--tui` forces the panes UI; `--console` uses the line REPL. `-p` always
runs one prompt and exits (no TUI).

## Local models — run fully offline

### Ollama (recommended)

Use the official Qwen2.5-Coder model — its tool calling is reliable:

On a new machine (USB copy or fresh clone), one script installs Ollama, pulls
the coder model, and creates `.venv`:

```bash
chmod +x scripts/setup-machine.sh
./scripts/setup-machine.sh                  # default: qwen2.5-coder:7b
./scripts/setup-machine.sh --model qwen2.5-coder:7b
./scripts/setup-machine.sh --skip-ollama    # venv only
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1
powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -Model qwen2.5-coder:7b
powershell -ExecutionPolicy Bypass -File scripts\setup-machine.ps1 -SkipOllama
```

Then: `.venv\Scripts\hfagent.exe --ollama qwen2.5-coder:7b`

Or do the same steps by hand:

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


## Coding skills

hfagent loads Cursor-style `SKILL.md` recipes and injects them into the system
prompt so the model follows a real write/edit/verify loop instead of only
announcing plans.

| Source | Path |
| :--- | :--- |
| Builtin | shipped: `write-code`, `edit-code`, `verify-code` |
| User | `~/.hfagent/skills/<name>/SKILL.md` |
| Project | `./.hfagent/skills/<name>/SKILL.md` (overrides user/builtin) |

List them in the REPL with `/skills`. Project skills win on name clash.

## Long-term memory

The agent remembers your world across sessions. It saves durable facts via
its `remember` tool into `~/.hfagent/memory/memory.md` (global, not
per-project), and that file is injected into every new session's system
prompt:

```
❯ remember that kali219 is my Kali pentest box behind voipgw.noc
remembered: kali219 is my Kali pentest box behind voipgw.noc
❯ /exit

$ hfagent --ollama        # fresh session later
❯ what do you know about my servers?
It remembers kali219 ...             # answered from memory, no tools needed
```

Manage it with `/memory` (view) and `/forget` (wipe). Secrets and passwords
should never go in memory — the tool description says so too.

## Remote hosts (SSH)

`ssh_run` executes single commands on hosts defined in `~/.ssh/config`:

```
❯ check disk space on wantok20
→ action: ssh_run  $ ssh wantok20 'df -h'
```

It is non-interactive by design (one command per call, `BatchMode=yes`,
`ConnectTimeout=10`) — interactive sessions would hang the agent. Keep your
keys in `ssh-agent` if they have passphrases.

### Long tasks: background mode

Foreground commands die when the timeout hits. For anything long (scans,
installs, builds), use `background: true` — the task runs detached via
`nohup sh -c` and **survives SSH disconnection**:

```
❯ run a full nmap scan on kali219 in the background
→ action: ssh_run (background: true)
   started in background on kali219
   pid: 125943   log: /tmp/hfagent-task-095244.log
   check progress: ssh_run(..., 'tail -n 30 /tmp/hfagent-task-095244.log')
   check finished: ssh_run(..., 'grep BG_DONE ... && echo FINISHED || echo RUNNING')
```

The log streams the task's output live; `BG_EXIT:n` records the exit status
and `BG_DONE` marks completion — the agent polls these with short follow-up
calls (connection multiplexing makes each check ~0.5s). No cron needed: just
ask "check on the scan" whenever you want a status update.

## Sessions

Every turn auto-saves to `~/.hfagent/sessions/<project>/`. Resume where you
left off:

```bash
.venv/bin/hfagent --ollama --resume       # continue the most recent session
```

or interactively with `/sessions` and `/resume <name>`.

## Recent (v0.4.0)

OpenCode-style TUI: chat pane with live markdown streaming, sessions/tools
sidebar, approval modal, and input history. Classic REPL is `--console`.
Still includes the v0.3.7 announce-without-acting hardening for local GGUFs.

## Roadmap

- **v0.5** — per-project memory scopes, auto-summarized session notes
- **v0.5** — sub-agents, smolagents interop, benchmark hooks for the course challenge
