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
| `/url <base_url>` | test a host (`GET /v1/models`) and switch to it |
| `/config` | show effective settings and where each came from |
| `/primary` | switch back after an automatic fallback |
| `/sessions` | list saved sessions |
| `/resume [name]` | load a saved session |
| `/browser` | ihagent Chrome status, or `/browser close` |
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
| `web_search` | DuckDuckGo search in the ihagent Chrome | — |
| `browser_open` | open a URL (optional new tab) | executable file: **y/N** |
| `browser_tabs` | list tabs | — |
| `browser_switch_tab` | switch tab by index | — |
| `browser_new_tab` | open a tab | executable file: **y/N** |
| `browser_close_tab` | close a tab | — |
| `browser_read` | compact text or accessibility snapshot with `[eN]` refs | — |
| `browser_click` | click a ref or CSS selector | form submit, purchase, executable: **y/N** |
| `browser_type` | fill a form field | `submit=true`: **y/N** |
| `browser_select` | choose a `<select>` option | — |
| `browser_press` | press a key | Enter that submits a form: **y/N** |
| `browser_scroll` | scroll the page or a ref into view | — |
| `browser_screenshot` | save a PNG and return its path | — |
| `browser_wait` | wait for time, text, or a selector | — |
| `browser_download` | save a file and report the path | executable: **y/N** |

Approval prompts protect against unintended writes and command execution.
Skip them at your own risk with `--yolo` (or `HFAGENT_YOLO=1`).

## Browser

Browser tools drive a **dedicated Chrome profile** at `~/.hfagent/chrome-profile`.
That is not your everyday Chrome profile, so ihagent never reads or writes your
personal cookies. Logins you complete in this window are saved in the ihagent
profile and are still there next launch. The window stays closed until the
first browser tool runs, and it closes when you quit ihagent or run
`/browser close`.

On macOS the driver prefers the installed Google Chrome (`channel=chrome`,
then the Chrome executable path) and falls back to Playwright's bundled
Chromium. The window is headed unless you opt into headless.

```bash
pip install 'hfagent[browser]' && playwright install chrome
ihagent
```

Then ask it to search or open a page. `/browser` shows whether Chrome is
running, the profile path, and the open tabs. Page text and snapshots are
truncated (default 6000 characters) so a 64K context is not filled by one
page. Instructions inside a web page are not treated as your instructions.

Form submits, purchases, and downloads of executables (`.dmg`, `.pkg`,
`.exe`, and similar) use the same y/N approval prompt as `run_command`.
Other files download without a prompt. Downloads and screenshots are saved
under `~/Downloads/ihagent` unless you change the directory. The tool reply
includes the saved path.

If Playwright is not installed, the tool returns an install hint instead of
crashing:

```text
pip install 'hfagent[browser]' && playwright install chrome
```

| Env var | Default | Meaning |
| :--- | :--- | :--- |
| `HFAGENT_BROWSER_HEADLESS` | `false` (headed) | `1` / `true` hides the window |
| `HFAGENT_BROWSER_PROFILE` | `~/.hfagent/chrome-profile` | dedicated user-data dir; the default Chrome profile is refused |
| `HFAGENT_BROWSER_DOWNLOAD_DIR` | `~/Downloads/ihagent` | where downloads and screenshots are saved |
| `HFAGENT_BROWSER_CHANNEL` | auto (`chrome`, else Chromium) | Playwright channel, e.g. `chrome` |
| `HFAGENT_BROWSER_EXECUTABLE` | auto | path to a Chrome binary; skips channel detection |
| `HFAGENT_BROWSER_TIMEOUT_MS` | `30000` | navigation and action timeout |
| `HFAGENT_BROWSER_MAX_CHARS` | `6000` | cap for `browser_read` and snapshots |

## Configuration

Settings come from one ordered list. The first source that sets a value wins.
`ihagent --show-config` and `/config` print the effective value and the source,
with secrets masked (`sk-s…cret`).

| Priority | Source |
| :--- | :--- |
| 1 | Command-line flags (`--model`, `--base-url`, `--max-steps`, …) |
| 2 | Process environment (variables you exported in this shell) |
| 3 | Project `.env` (`./.env`, or `../.env` if the current directory has none) |
| 4 | `~/.hfagent/.env` |
| 5 | `~/.hfagent/hf.env` |
| 6 | Built-in defaults |

`~/.hfagent/hf.env` is what `scripts/setup-huggingface.sh` writes, and shells
often `source` it. A process value that is identical to that file is treated
as the file, so it does not hide a newer model in the project `.env` or in
`~/.hfagent/.env`. Export a different value when the shell should win.

| Env var | Default | Meaning |
| :--- | :--- | :--- |
| `HFAGENT_API_KEY` | — | API key for **any** base URL. When set, this wins over the provider keys below |
| `HF_TOKEN` / `HF_API_KEY` | — | Hugging Face token, used when `HFAGENT_API_KEY` is unset |
| `OLLAMA_API_KEY` | — | Ollama Cloud key (`https://ollama.com`), then `HF_TOKEN` |
| `ANTHROPIC_API_KEY` | — | Anthropic key for `https://api.anthropic.com` |
| `HFAGENT_CONTEXT_TOKENS` | `65536` | Context window used for automatic compaction (~64K) |
| `HFAGENT_FALLBACK_BASE_URL` | — | Host to use when the primary cannot be connected to |
| `HFAGENT_FALLBACK_MODEL` | primary model | Model id on the fallback host |
| `HFAGENT_FALLBACK_API_KEY` | key for that host | Optional key used only for the fallback |
| `HF_BASE_URL` | `https://router.huggingface.co/v1` | any OpenAI-compatible endpoint |
| `HFAGENT_MODEL` | `Qwen/Qwen2.5-Coder-32B-Instruct` | model id |
| `HFAGENT_LOCAL_MODEL` | `~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit` | model path used with `--local` |
| `HFAGENT_MAX_STEPS` | `25` | agent loop iteration cap |
| `HFAGENT_BROWSER_HEADLESS` | headed | set `1` to hide the ihagent Chrome window |
| `HFAGENT_BROWSER_PROFILE` | `~/.hfagent/chrome-profile` | dedicated Chrome profile (never the default profile) |
| `HFAGENT_BROWSER_DOWNLOAD_DIR` | `~/Downloads/ihagent` | download and screenshot directory |
| `HFAGENT_BROWSER_CHANNEL` | auto | Playwright channel; macOS prefers `chrome` |
| `HFAGENT_BROWSER_EXECUTABLE` | auto | optional Chrome executable path |

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

Flags: `--tui` forces the panes UI; `--console` uses the line REPL. `-p` always
runs one prompt and exits (no TUI). `--show-config` prints the resolved
settings and exits.

### Custom hosts, tunnels, and context

A user-run Ollama behind a Cloudflare quick tunnel is just another
OpenAI-compatible base URL. Set `HFAGENT_API_KEY` so a stale `HF_TOKEN` in
the environment is not sent instead:

```bash
export HFAGENT_API_KEY=...          # beats HF_TOKEN / OLLAMA_API_KEY / ANTHROPIC_API_KEY
export HF_BASE_URL=https://<tunnel>.trycloudflare.com/v1
export HFAGENT_MODEL=qwen3.8-27b-uncensored-mtp
export HFAGENT_CONTEXT_TOKENS=65536 # lower this for smaller windows
export HFAGENT_FALLBACK_BASE_URL=https://ollama.com/v1
export HFAGENT_FALLBACK_MODEL=qwen3:8b
export HFAGENT_FALLBACK_API_KEY=... # optional; otherwise the key for that host
```

Inside the session, `/url https://<tunnel>.trycloudflare.com` adds `/v1` when
the path is missing, GETs `/v1/models` with the current key, and on success
switches the live client. The URL is saved to `~/.hfagent/.env`. If the
project `.env` also sets `HF_BASE_URL`, that line is updated too, because the
project file would otherwise shadow the user file on the next launch.

When the conversation approaches the context window, older turns are
summarized locally (no extra model call). The system prompt and the newest
turns stay, and an assistant tool call is never split from its tool results.
Set `HFAGENT_CONTEXT_TOKENS` to the model's window. The estimate is about
four characters per token, and some room is left for the reply.

If the primary host fails at the connection (DNS, TLS, timeout, connect
error, Cloudflare 530, or a 502/503/504 gateway) and
`HFAGENT_FALLBACK_BASE_URL` is set, the session switches to the fallback and
says so. Auth failures (401/403) stay on the primary. `/primary` switches
back when the original host answers again.

Reasoning models (Qwen3 via Ollama) may put text in `reasoning` /
`reasoning_content`, or wrap it in `<think>` tags with an empty `content`.
That text is shown dimmed as thought, in the TUI and the console, for both
streaming and non-streaming responses. A reply that also has a final answer
or a tool call is not treated as empty. Reasoning alone is nudged for a
final answer.

Connection errors name the base URL and the cause: DNS failure, TLS failure,
tunnel gone (Cloudflare 530 / connect error), timeout, 401/403 auth (with the
env var to set), 404 model not found, or 5xx.

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
