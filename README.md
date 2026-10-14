# IHAgent

IHAgent is a terminal coding agent from InnovatelHub Solutions Ltd. The command is `ihagent` (the same program is also installed as `hfagent`). It runs a tool-calling loop — thought, action, observation — against any OpenAI-compatible chat API, and it can read, search, and edit files, run shell and SSH commands, and drive a dedicated Chrome window. On a real terminal the default interface is a Textual TUI with a chat pane and a sessions/tools sidebar. `--console` is the line REPL, and `-p` runs one prompt and exits. On macOS, a floating iheye bubble can stay above other apps while that terminal is in the background.

Package `hfagent` 0.4.0, Python 3.10+, license MIT (declared in `old_project_files/pyproject.toml`). Sources live in [`old_project_files/src/hfagent`](old_project_files/src/hfagent).

## Feature highlights

- **Tool-calling agent TUI.** Streamed markdown chat, live actions and observations, a sessions/tools sidebar, an approval modal, and input history. The header shows the host label, model, approval mode, and token counts. Device is filled in when it is known: `GPU`, `CPU`, or `GPU+CPU` for local Ollama, `GPU` for a Hugging Face Inference Endpoint, and `remote` for the router and Ollama Cloud.
- **OpenAI-compatible hosts.** The default is the Hugging Face router (`https://router.huggingface.co/v1`). The same client talks to local Ollama, [Ollama Cloud](https://ollama.com), Anthropic's OpenAI-compatible endpoint, an MLX server, or any other `/v1` base URL.
- **Self-hosted Ollama behind a tunnel.** A Cloudflare quick tunnel (or any other HTTPS front) is just another base URL. Requests send `Authorization: Bearer` with `HFAGENT_API_KEY`, so a gateway in front of Ollama can require that token. `/url` probes `GET /v1/models` before switching.
- **Context auto-summarising.** When the conversation nears the context window, older turns are summarised in-process (about four characters per token, no extra model call). The system prompt and the newest turns stay, and an assistant tool call is never split from its tool results.
- **Host control in the session.** `/url` tests and switches hosts, `/config` and `ihagent --show-config` print the effective settings and the source that won, a configured fallback host takes over on connection failures, and `/primary` switches back.
- **Reasoning display.** Text from `reasoning` / `reasoning_content`, or from `<think>` tags, is shown dimmed as thought in the TUI and the console. A reply that also has a final answer or a tool call is kept. Reasoning alone is nudged for a final answer.
- **Chrome browser tools.** A dedicated profile (`~/.hfagent/chrome-profile`), lazy launch, and y/N approval for form submits, purchases, and executable downloads. Page text is labelled untrusted.
- **iheye (macOS).** An always-on-top eye while the ihagent terminal is in the background. Hover opens a chat panel whose line is handled like the TUI composer. A finished turn can show a short summary card, and a pending y/N approval shows on that card. The pupil follows the pointer; key activity makes it glance without reading which key was pressed. `/iheye` turns the bubble, tracking, and summary card on or off.

## Quick start

From the repository root:

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -e 'old_project_files[browser,iheye]'
playwright install chrome
ihagent
```

`browser` installs Playwright. `iheye` installs PyObjC on macOS (`pyobjc-framework-Cocoa`); on other platforms that extra is empty and the overlay stays off. Tests use the `dev` extra: `pip install -e 'old_project_files[dev,browser,iheye]'`. One-shot and the line REPL:

```bash
ihagent -p "list the files here and explain what this project is"
ihagent --console
```

Hugging Face and Ollama Cloud refuse a one-shot `-p` until a key is set. The TUI and the line REPL still start, and you can paste a key with `/model_api=...` (it is saved, and it is not printed back).

```bash
export HF_TOKEN=hf_...             # https://huggingface.co/settings/tokens
# or, for Ollama Cloud:
# export OLLAMA_API_KEY=...        # https://ollama.com/settings/keys
```

Optional machine setup (from `old_project_files/`): `scripts/setup-machine.sh` creates a venv and can install Ollama; `scripts/setup-huggingface.sh` logs in to the router or, with `--gpu --yes`, a billed dedicated endpoint. Windows equivalents are the `.ps1` scripts next to them.

## Interfaces

### CLI

| Flag | Effect |
| :--- | :--- |
| `-p`, `--prompt TEXT` | Run one prompt and exit. No TUI. |
| `--model ID` | Model id. Default `Qwen/Qwen2.5-Coder-32B-Instruct`. |
| `--base-url URL` | OpenAI-compatible base URL. |
| `--max-steps N` | Agent-loop cap. Default 25. |
| `--temperature F` | Sampling temperature. Default `0.0` (greedy). |
| `--ollama [MODEL]` | Local Ollama at `http://127.0.0.1:11434/v1`. No name uses the most recently modified installed model. |
| `--local` | MLX server at `http://127.0.0.1:1234/v1` (override the URL with `--base-url`). |
| `--resume [NAME]` | Continue a saved session. No name means the most recent. |
| `--approval auto\|default` | `auto` runs gated tools without asking. `default` prompts y/N. |
| `-y`, `--yolo` | Same as `--approval auto`. |
| `--tui` / `--console` | Force the TUI, or the line REPL. The TUI is the default when stdin and stdout are TTYs. |
| `--show-config` | Print resolved settings and exit. Secrets are masked. |
| `--version` | Print `0.4.0` and exit. |

### TUI keys

| Key | Effect |
| :--- | :--- |
| `f1` | `/help` |
| `f2` | Model picker for the current host |
| `ctrl+b` | Toggle the sessions/tools sidebar |
| `ctrl+l` | Clear the conversation |
| `ctrl+q` | Quit |
| `ctrl+shift+c` | Copy the selection |
| `up` / `down` | Input history |
| `y` / `n` | Approve or deny a gated tool (`enter` approves, `escape` denies) |

Click a session in the sidebar to resume it. While a turn is running, a typed note is picked up on the next step. The word `stop` cancels the turn.

## Hosts

| How you select it | Base URL | Key that is sent |
| :--- | :--- | :--- |
| Default | `https://router.huggingface.co/v1` | `HFAGENT_API_KEY`, else `HF_TOKEN`, else `HF_API_KEY` |
| `--ollama`, `/local` | `http://127.0.0.1:11434/v1` | `HFAGENT_API_KEY`, else `HF_TOKEN`, else `HF_API_KEY` |
| `/cloud` | `https://ollama.com/v1` | `HFAGENT_API_KEY`, else `OLLAMA_API_KEY`, else `HF_TOKEN`, else `HF_API_KEY` |
| `/claude` | `https://api.anthropic.com/v1` | `HFAGENT_API_KEY`, else `ANTHROPIC_API_KEY` |
| `--local` | `http://127.0.0.1:1234/v1` | `HFAGENT_API_KEY`, else `HF_TOKEN`, else `HF_API_KEY` |
| `/url`, `HF_BASE_URL` | Any OpenAI-compatible `/v1` | `HFAGENT_API_KEY` if set, otherwise `HF_TOKEN` / `HF_API_KEY` |

`HFAGENT_API_KEY`, when set, is the key for every host and is sent as `Authorization: Bearer`. Anthropic's models probe also sends `x-api-key` and `anthropic-version: 2023-06-01`. Local Ollama does not require a key; a leftover `HF_TOKEN` is still attached. A key pasted with `/model_api=` on a `:11434` URL is stored as `OLLAMA_API_KEY`, which the next launch reads for `ollama.com` and not for the local port. `/claude` uses `claude-sonnet-5-5` when the current model id does not contain `claude`. If the live models list cannot be fetched, the picker falls back to the ids in `ANTHROPIC_FALLBACK_MODELS`.

`/cloud` and `/local` remember the model you were using (`HFAGENT_CLOUD_MODEL`, `HFAGENT_LOCAL_MODEL`) and the new base URL. Inside the REPL, `/models` lists what that host returns (`→` marks the active id). `/model` accepts an exact name, a unique substring, or the list number.

### Tunnel or other custom host

```bash
export HFAGENT_API_KEY='gateway-token'          # Bearer token for this host
export HF_BASE_URL='https://<tunnel-host>/v1'
export HFAGENT_MODEL='<model-tag>'
export HFAGENT_CONTEXT_TOKENS=65536
export HFAGENT_FALLBACK_BASE_URL='https://ollama.com/v1'
export HFAGENT_FALLBACK_MODEL='<fallback-model>'
# export HFAGENT_FALLBACK_API_KEY='...'          # only when the fallback needs its own key
ihagent
```

`/url https://<tunnel-host>` normalises the URL, `GET`s `/v1/models` with the current key, and on success switches the live client. The URL is written to `~/.hfagent/.env`. If the project `.env` already sets `HF_BASE_URL`, that line is updated too, because the project file would otherwise win on the next launch.

Set `HFAGENT_API_KEY` for a tunnel. If it is unset and `HF_TOKEN` is present, that Hugging Face token is what a custom host receives.

### Fallback

If the primary host fails at the connection — DNS, TLS, timeout, connect error, Cloudflare 530, or HTTP 502/503/504 — and `HFAGENT_FALLBACK_BASE_URL` is set, the session switches and says so. Auth failures (401/403) stay on the primary. `/primary` probes the original host and switches back unless that probe is itself a connection failure. The fallback model defaults to the primary model. `HFAGENT_FALLBACK_API_KEY` is used only for the fallback; otherwise the key is chosen the same way as for that host.

### Local models

```bash
ollama pull qwen2.5-coder:7b
ihagent --ollama                       # most recently modified installed model
ihagent --ollama qwen2.5-coder:7b      # exact or unique substring
```

`old_project_files/scripts/setup-machine.sh` can install Ollama, pull a model, and create `.venv` (`--model`, `--skip-ollama`, `--skip-model`, `--dev`). Some GGUF chat templates emit tool calls as text. IHAgent parses `<tool_call>{...}</tool_call>`, `<tool>...</tool>`, `<function=name>` blocks, fenced JSON with `name` / `arguments`, and a bare `{"name": ...}` object. Fabricated `<tool_response>` blocks are stripped and the model is nudged back to its tools.

Apple Silicon MLX, from `old_project_files/`:

```bash
python -m mlx_lm convert \
  --hf-path Qwen/Qwen2.5-0.5B-Instruct --quantize \
  --mlx-path ~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit
./scripts/serve-local.sh               # OpenAI-compatible server on :1234
ihagent --local
```

`HFAGENT_LOCAL_MODEL` is the path used with `--local` (default `~/.cache/huggingface/mlx/qwen2.5-0.5b-4bit`) and, separately, the model name remembered for `/local`. Temperature stays at `0.0` unless you pass `--temperature`.

## Context and reasoning

`HFAGENT_CONTEXT_TOKENS` is the window used for compaction (default 65536). A reserve of one eighth of that window, capped at 8192 tokens, is left for the reply. The estimate is `ceil(characters / 4)`. When older turns drop, the status line is:

```text
context full — summarized N older messages (~T tokens, limit L)
```

The inserted summary is marked `summarized to fit the context window` and is treated as memory, not as a new request. If the API still returns a context-length error, IHAgent compacts again and retries once (`context limit hit — compacting history and retrying`).

Qwen3 via Ollama and similar models may put chain-of-thought in `reasoning` or `reasoning_content`, or wrap it in `<think>` while leaving `content` empty. That text is dimmed. It is not the answer.

## Browser

Browser tools use a Chrome profile that belongs to IHAgent, at `~/.hfagent/chrome-profile`. The default Chrome profile is refused. Logins completed in this window stay in the IHAgent profile. The window stays closed until the first browser tool runs, and it closes when you quit or run `/browser close`.

Launch order is installed Google Chrome (`channel=chrome`, then the Chrome executable path), then Playwright's Chromium. The window is headed unless `HFAGENT_BROWSER_HEADLESS` is `1`, `true`, `yes`, or `on`. On Linux the launch adds `--disable-dev-shm-usage`.

`/browser` prints whether Chrome is running, the profile path, and the open tabs, and it does not start the browser. `browser_read` returns compact page text or an accessibility snapshot with `[eN]` refs, truncated at `HFAGENT_BROWSER_MAX_CHARS` (default 6000, clamped to 500–12000). Each read is prefixed so instructions inside the page are not treated as your instructions.

Downloads and screenshots go to `~/Downloads/ihagent`. The tool reply includes the saved path. Form submits, purchases, and executable downloads (`.dmg`, `.pkg`, `.exe`, and the other suffixes in `EXECUTABLE_SUFFIXES`) use the same y/N prompt as `run_command`. Other downloads do not.

If Playwright is missing, the tool returns:

```text
Error: browser tools need Playwright and Google Chrome.
Install with: pip install 'hfagent[browser]' && playwright install chrome
```

## iheye

On macOS, ihagent starts a small always-on-top **iheye** bubble in a helper process (`python -m hfagent.iheye`). The bubble hides while the terminal that is running ihagent is focused, and it shows when you switch to another app. Clicking the bubble brings that terminal forward. You can drag it. The position is saved in `~/.hfagent/iheye-position.json` and restored next launch. The first launch sits at the top-right of the screen. The helper uses a borderless, non-activating floating panel on all Spaces, watches the parent pid, and exits when ihagent exits.

Terminal.app and iTerm2 hide the bubble only when the ihagent window or tab is the focused one (matched by tty). Warp, VS Code, Cursor, Ghostty, kitty, WezTerm, Alacritty, and Hyper hide it whenever that app is frontmost.

The eye follows the agent: idle is calm, thinking moves the pupil, a y/N approval pulses amber (`waiting`), and a finished turn pulses green (`done`).

Hover the eye and a panel opens under it (`Message ihagent`). Enter sends the line. The TUI and the line REPL treat it like a line you typed: the transcript shows it, a new turn starts when the agent is idle, and a line sent mid-task is picked up on the next step (`stop` still cancels). The reply streams back into the panel, trimmed to the newest lines (about six lines, 360 characters). The panel stays open while the pointer is over the eye or the panel, or while the text field is focused. Esc closes it and keeps it closed until the pointer leaves and comes back. Moving the pointer away closes it after about 0.35 seconds. After you send, focus returns to the app you were using when the helper had become frontmost.

When a turn finishes and the bubble is visible, a card under the eye shows the first lines of the answer (about four lines, 220 characters, no extra model call). It hides after 8 seconds. Hovering the card pauses that timer. Clicking the card opens the ihagent terminal. If a tool is waiting on y/N, the card shows the tool name, a short preview, and **y** / **N** buttons that answer the same prompt as the TUI. Turning summaries off hides that completion card. The approval card still shows.

The pupil follows the pointer anywhere on the screen (`NSEvent.mouseLocation`). That needs no extra permission. A key-down in another app makes the eye glance and blink for a short moment. The handler records only that a key went down. It does not read which key, and it does not write keystrokes to `~/.hfagent/iheye.log`. Idle animation ticks slowly; thinking, approval, and nearby pointer motion redraw faster.

The overlay is on by default on macOS and off elsewhere. It starts with the TUI, the line REPL, and `-p`. `--show-config` and `--version` do not start it. `ihagent --show-config` and `/config` include `iheye`, `iheye_track`, `iheye_summary`, and whether PyObjC is installed. `~/.hfagent/iheye.log` is replaced on each launch.

```bash
pip install -e 'old_project_files[browser,iheye]'
ihagent
/iheye
```

| Command | Effect |
| :--- | :--- |
| `/iheye` | Status: `iheye on · <app> · <state> · track on\|off · summary on\|off`, or why it is not running. |
| `/iheye on` | Show the bubble and save `HFAGENT_IHEYE=1` in `~/.hfagent/.env`. |
| `/iheye off` | Hide it and save `HFAGENT_IHEYE=0`. |
| `/iheye track` | Report whether the pupil follows the pointer. |
| `/iheye track on\|off` | Toggle pointer and keyboard glances. Saved as `HFAGENT_IHEYE_TRACK`. |
| `/iheye summary` | Report the completion card. |
| `/iheye summary on\|off` | Toggle the completion card. Saved as `HFAGENT_IHEYE_SUMMARY`. The y/N card stays. |

`1`, `true`, `yes`, and `on` enable a flag. `0`, `false`, `no`, and `off` disable it. Unset `HFAGENT_IHEYE` means on for macOS only. Unset `HFAGENT_IHEYE_TRACK` and `HFAGENT_IHEYE_SUMMARY` mean on. An unrecognised value turns the bubble off, and leaves tracking and the summary card on.

### Permissions

Pointer tracking needs no special permission. Keyboard glances use a global key-down monitor. If macOS Accessibility permission is missing, glances are skipped, the pupil still follows the pointer, and this hint is printed to stderr and to `~/.hfagent/iheye.log`:

```text
iheye: keyboard glances need Accessibility permission (System Settings → Privacy & Security → Accessibility). Enable the Python that runs ihagent. Mouse tracking works without it.
```

Enable the Python interpreter that runs ihagent (the venv's `python`, or Terminal, if that is what macOS lists).

## Configuration

The first source that sets a value wins. `ihagent --show-config` and `/config` print the effective value and that source. Secrets are masked as the first and last four characters (`****` when the value is eight characters or shorter).

| Priority | Source |
| :--- | :--- |
| 1 | Command-line flags (`--model`, `--base-url`, `--max-steps`, `--temperature`, `--approval` / `--yolo`, `--local`, `--ollama`) |
| 2 | Process environment, except a value that is identical to `~/.hfagent/hf.env` (that one is treated as the file, so it does not hide a project or user env file) |
| 3 | Project `.env` (`./.env`, or `../.env` when the current directory has none) |
| 4 | `~/.hfagent/.env` |
| 5 | `~/.hfagent/hf.env` (also what `scripts/setup-huggingface.sh` writes) |
| 6 | Built-in defaults |

`/url` writes `HF_BASE_URL` to `~/.hfagent/.env` and, when the project `.env` already has that key, updates the project line too. `/model`, `/cloud`, `/local`, `/claude`, and `/model_api=` write the project `.env`.

| Variable | Default | Meaning |
| :--- | :--- | :--- |
| `HFAGENT_API_KEY` | unset | Bearer token for every base URL. Wins over the provider keys below. |
| `HF_TOKEN`, `HF_API_KEY` | unset | Hugging Face token. Also used for a custom host when `HFAGENT_API_KEY` is unset. |
| `OLLAMA_API_KEY` | unset | Ollama Cloud (`https://ollama.com`). Then `HF_TOKEN`, then `HF_API_KEY`. |
| `ANTHROPIC_API_KEY` | unset | Key for `https://api.anthropic.com`. |
| `HF_BASE_URL` | `https://router.huggingface.co/v1` | OpenAI-compatible base URL. |
| `HFAGENT_MODEL` | `Qwen/Qwen2.5-Coder-32B-Instruct` | Model id. |
| `HFAGENT_LOCAL_MODEL` | MLX path above; empty for Ollama | `--local` model path, and the model name `/local` restores. |
| `HFAGENT_CLOUD_MODEL` | unset | Model name `/cloud` restores. |
| `HFAGENT_CONTEXT_TOKENS` | `65536` | Context window for auto-summarising. |
| `HFAGENT_MAX_STEPS` | `25` | Agent-loop iteration cap. |
| `HFAGENT_APPROVAL` | `default` | `auto` or `default`. |
| `HFAGENT_YOLO` | unset | `1` selects approval `auto` when `HFAGENT_APPROVAL` is unset. |
| `HFAGENT_FALLBACK_BASE_URL` | unset | Host used when the primary cannot be connected to. |
| `HFAGENT_FALLBACK_MODEL` | primary model | Model id on the fallback host. |
| `HFAGENT_FALLBACK_API_KEY` | key for that host | Optional key used only for the fallback. |
| `HFAGENT_MEMORY_PATH` | `~/.hfagent/memory` | Directory that holds `memory.md`. |
| `HFAGENT_BROWSER_HEADLESS` | headed | `1` / `true` / `yes` / `on` hides the window. |
| `HFAGENT_BROWSER_PROFILE` | `~/.hfagent/chrome-profile` | User-data dir. The default Chrome profile is refused. |
| `HFAGENT_BROWSER_DOWNLOAD_DIR` | `~/Downloads/ihagent` | Downloads and screenshots. |
| `HFAGENT_BROWSER_CHANNEL` | auto (`chrome`, else Chromium) | Playwright channel, for example `chrome`. |
| `HFAGENT_BROWSER_EXECUTABLE` | auto | Chrome binary path. Skips channel detection. |
| `HFAGENT_BROWSER_TIMEOUT_MS` | `30000` | Navigation and action timeout (minimum 1000). |
| `HFAGENT_BROWSER_MAX_CHARS` | `6000` | Cap for `browser_read` and snapshots (500–12000). |
| `HFAGENT_IHEYE` | on for macOS, off elsewhere | `0` / `off` disables the bubble. `/iheye on` and `/iheye off` persist it in `~/.hfagent/.env`. |
| `HFAGENT_IHEYE_TRACK` | on | `0` / `off` stops the pupil following the pointer and keyboard glances. `/iheye track on` and `/iheye track off` persist it. |
| `HFAGENT_IHEYE_SUMMARY` | on | `0` / `off` hides the completion card. `/iheye summary on` and `/iheye summary off` persist it. The y/N card still shows. |

There is no environment variable for temperature. Pass `--temperature`.

## Slash commands

| Command | Effect |
| :--- | :--- |
| `/help` | Show commands. |
| `/tools` | List registered tools. |
| `/models` | List models on the current host. |
| `/model <name\|#>` | Switch model by exact name, unique substring, or 1-based list number. Saved to the project `.env`. |
| `/model_api=<key>` | Save an API key for this host. Also accepted as `/model_api <key>` and `/apikey`. The key is not shown. |
| `/approval [auto\|default]` | Show or switch approval mode. |
| `/cloud` | Ollama Cloud, remembered for the next launch. |
| `/local` | Ollama on this machine, remembered for the next launch. |
| `/claude` | Anthropic. Needs `ANTHROPIC_API_KEY` or `HFAGENT_API_KEY`. |
| `/host <cloud\|local\|claude>` | Same switches. With no argument, print the current host. |
| `/url <base_url>` | `GET /v1/models`, then switch and save. |
| `/primary` | Probe the original host and switch back, unless that probe is a connection failure. |
| `/config` | Effective settings and the source of each. |
| `/browser [close]` | Chrome status, or close the window. |
| `/iheye [on\|off]` | macOS floating eye. `track on\|off` and `summary on\|off` toggle gaze and the completion card. |
| `/memory` | Show long-term memory. |
| `/forget` | Wipe long-term memory. |
| `/skills` | List coding skills. |
| `/sessions` | List saved sessions for this directory. |
| `/resume [name]` | Load a session (`last` if omitted). |
| `/delete [name\|all]` | Delete one session (`last` if omitted) or all of them. |
| `/clear` | Reset the conversation. |
| `/exit`, `/quit` | Leave. |

## Tools and approvals

| Tool | Purpose | Approval |
| :--- | :--- | :--- |
| `list_dir` | List a directory | — |
| `read_file` | Read a text file | — |
| `grep` | Regex search across files | — |
| `write_file` | Create or overwrite a file | y/N, with a diff |
| `edit_file` | Replace exact text | y/N, with a diff |
| `run_command` | Shell command on this machine | y/N |
| `ssh_run` | One command on a host from `~/.ssh/config` | y/N |
| `remember` | Append one fact to long-term memory | — |
| `web_search` | DuckDuckGo search in the IHAgent Chrome | — |
| `browser_open` | Open a URL (optional new tab) | y/N for an executable file |
| `browser_tabs` | List tabs | — |
| `browser_switch_tab` | Switch tab by index | — |
| `browser_new_tab` | Open a tab | y/N for an executable file |
| `browser_close_tab` | Close a tab | — |
| `browser_read` | Compact text or a snapshot with `[eN]` refs | — |
| `browser_click` | Click a ref or CSS selector | y/N for submit, purchase, or executable |
| `browser_type` | Fill a field | y/N when `submit=true` |
| `browser_select` | Choose a `<select>` option | — |
| `browser_press` | Press a key | y/N for Enter that submits a form or purchases |
| `browser_scroll` | Scroll the page or a ref into view | — |
| `browser_screenshot` | Save a PNG and return its path | — |
| `browser_wait` | Wait for time, text, or a selector | — |
| `browser_download` | Save a file and report the path | y/N for an executable |

`--approval auto`, `-y` / `--yolo`, and `HFAGENT_YOLO=1` skip these prompts.

`ssh_run` is one non-interactive command per call (`BatchMode=yes`, `ConnectTimeout=10`, ControlMaster multiplexing). `background: true` on `ssh_run` or `run_command` starts the command under `nohup` and returns a pid plus a log at `/tmp/hfagent-task-HHMMSS.log`. An SSH background log ends with `BG_EXIT` and `BG_DONE`. A local background command is checked with `ps -p`.

## Skills, memory, and sessions

Skills are `SKILL.md` recipes injected into the system prompt. Project overrides user overrides builtin. List them with `/skills`.

| Source | Path |
| :--- | :--- |
| Builtin | `old_project_files/src/hfagent/builtin_skills/<name>/SKILL.md` |
| User | `~/.hfagent/skills/<name>/SKILL.md` |
| Project | `./.hfagent/skills/<name>/SKILL.md` |

The `remember` tool appends to `~/.hfagent/memory/memory.md` (or `$HFAGENT_MEMORY_PATH/memory.md`). That file is injected into new sessions, except on Anthropic, where it is left out of the system prompt. `/memory` shows it and `/forget` deletes it. The tool description tells the model not to store secrets or passwords. Entries longer than 6000 characters keep the newest tail.

Each completed turn is saved under `~/.hfagent/sessions/<project>/` as `YYYYMMDD-HHMMSS.json`. Resume with `--resume` or `/resume`. `/sessions` shows the ten newest.

## Self-hosting on Kaggle

A Kaggle notebook running Ollama (or another OpenAI-compatible server) is a custom host. This repository does not ship a notebook, a tunnel token, or a fixed hostname. Keep those in your own `.env`, which is gitignored.

1. In the notebook, run Ollama (or another server) and expose its OpenAI-compatible API, normally under `/v1`.
2. If the notebook has no public port, put a tunnel or reverse proxy in front of it. A Cloudflare quick tunnel is one option. The hostname changes when the notebook or the tunnel restarts.
3. If that proxy checks a bearer token, put the token in `HFAGENT_API_KEY`. IHAgent sends `Authorization: Bearer <HFAGENT_API_KEY>` on chat requests and on `GET /v1/models`.
4. Point the client at the host with placeholders only — no real tunnel URL and no real key in the repo, in chat transcripts you commit, or in `memory.md`:

```bash
export HFAGENT_API_KEY='<gateway-token>'
export HF_BASE_URL='https://<tunnel-host>/v1'
export HFAGENT_MODEL='<model-tag-on-that-server>'
export HFAGENT_CONTEXT_TOKENS=65536
export HFAGENT_FALLBACK_BASE_URL='https://<backup-host>/v1'
export HFAGENT_FALLBACK_MODEL='<fallback-model>'
ihagent --show-config
```

5. Or switch a running session with `/url https://<tunnel-host>` after the key is set. `/config` shows the masked key and which file won.
6. When the notebook stops, the client reports DNS, timeout, tunnel (Cloudflare 530 / connect error), or gateway (HTTP 502/503/504). With `HFAGENT_FALLBACK_BASE_URL` set, the session moves to the fallback. A 401 or 403 stays on the primary so a bad token is not hidden by the fallback. `/primary` returns once the notebook answers again.
7. Match `HFAGENT_CONTEXT_TOKENS` to the model you loaded. A 64K default is too large for a smaller window and too small for a larger one.

## Security

- Writes, shell commands, and SSH commands wait for y/N unless approval is `auto`. The same prompt covers browser form submits, purchases, and executable downloads.
- The browser profile is separate from your everyday Chrome. A path under the default Chrome user-data directory is rejected.
- Page text and snapshots are prefixed as untrusted. Instructions on a page are not your instructions.
- `/config` and `--show-config` mask secrets. `/model_api=` stores the key and does not echo it.
- Do not put passwords or tokens in `remember` / `memory.md`. That file is loaded into the system prompt on non-Anthropic hosts.
- `.env` is gitignored. Tunnel hostnames and gateway tokens belong there, or in `~/.hfagent/.env`, not in the repository.
- `ssh_run` does not open an interactive remote shell. Passphrases need `ssh-agent` because `BatchMode=yes` will not prompt.
- iheye's key monitor records that a key went down. It does not receive the character, and it does not log what you type.

## Troubleshooting

| Message | What to do |
| :--- | :--- |
| `No API key found.` | For the Hugging Face router, `export HF_TOKEN=...` or start the TUI and paste `/model_api=...`. |
| `No Ollama API key found.` | For `https://ollama.com`, set `OLLAMA_API_KEY` or paste `/model_api=...`. |
| `No Ollama model found.` | `ollama serve`, then `ollama list`, or `ihagent --ollama <model>`. |
| `textual is required for the TUI.` | `pip install 'textual>=8'`, or run `ihagent --console`. |
| `Error: browser tools need Playwright and Google Chrome.` | `pip install 'hfagent[browser]' && playwright install chrome` (from this repo: `pip install -e 'old_project_files[browser]'`). |
| `Refusing to use the default Chrome profile` | Leave `HFAGENT_BROWSER_PROFILE` at `~/.hfagent/chrome-profile`. |
| `Could not launch Chrome or Chromium.` | Install Google Chrome, or set `HFAGENT_BROWSER_EXECUTABLE` / `HFAGENT_BROWSER_CHANNEL`. |
| `Cannot reach <url>: DNS failure.` | The host name does not resolve. Check `HF_BASE_URL` or the tunnel hostname. |
| `Cannot reach <url>: TLS failure.` | The certificate or handshake was rejected. |
| `Cannot reach <url>: timeout.` | The host did not respond in time. Chat requests time out after 120 seconds. `/url` probes time out after 8 seconds. |
| `Cannot reach <url>: tunnel gone (Cloudflare 530 / connect error).` | The quick tunnel is down or the origin (notebook, Ollama) is offline. Restart it, or `/url` the new hostname. |
| `Cannot reach <url>: gateway error HTTP 502` (also 503, 504) | The proxy is not passing traffic. |
| `Authentication failed for <url>: 401/403 auth` | Set the variable named in the message (`HFAGENT_API_KEY`, `HF_TOKEN`, `OLLAMA_API_KEY`, or `ANTHROPIC_API_KEY`). Fallback does not trigger on auth errors. |
| `Model not found at <url>` | The model id is wrong for this host. `/models`, then `/model`. |
| `Server error from <url>: 5xx` | The host returned a server error. |
| `This model server rejected native tool calls. Retrying in text-tool mode.` | The server has no native tools. IHAgent continues by parsing tool calls from text. |
| `context full — summarized …` | Expected on a long session. Lower or raise `HFAGENT_CONTEXT_TOKENS` to match the model. |
| `context limit hit — compacting history and retrying` | The server rejected the prompt length. Compaction runs again once. |
| `primary still unreachable: …` | `/primary` probed the original host and it is still a connection failure. |
| `session not found: <name>` | `/sessions` lists names. `last` is the newest file for this directory. |
| `usage: /url <base_url>` | `/url` needs a host. A bare hostname is accepted and `https://` and `/v1` are added. |
| `no unique model match for '…'` | `/models`, then `/model` with a number or a longer substring. |
| `stopped` | You typed `stop` while a turn was running. |
| `api key saved` | `/model_api=` accepted the key (at least 8 characters, no spaces) and wrote the project `.env`. |
| `iheye only runs on macOS` | Expected off Darwin. `/iheye on` still saves `HFAGENT_IHEYE=1` for a later Mac. |
| `iheye off (HFAGENT_IHEYE=0). /iheye on to show it` | The bubble was turned off. `/iheye on`, or unset `HFAGENT_IHEYE` on macOS. |
| `iheye needs PyObjC. pip install 'hfagent[iheye]'` | The `iheye` extra is missing. From this repo: `pip install -e 'old_project_files[browser,iheye]'`. |
| `pip install 'hfagent[iheye]'` | The helper process could not import AppKit. Same install as above. |
| `iheye helper exited` | The overlay process quit during startup. Read `~/.hfagent/iheye.log` (replaced on each launch). |
| `iheye failed to start: …` | The helper could not be spawned. The message includes the OS error. |
| `usage: /iheye on\|off \| track on\|off \| summary on\|off` | `/iheye track` and `/iheye summary` take `on` or `off`. |
| `iheye: keyboard glances need Accessibility permission …` | Pointer tracking still works. System Settings → Privacy & Security → Accessibility, and enable the Python that runs ihagent. |

Connection errors name the base URL. `ihagent --show-config` is the first check when the wrong host or the wrong key is in use: a process value copied from `source ~/.hfagent/hf.env` is attributed to that file, so a project `.env` can still override it. Export a different value when the shell should win.

## Development and testing

Tests live in `old_project_files/tests` and use pytest (`testpaths = ["tests"]` in `pyproject.toml`).

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e 'old_project_files[dev,browser]'
cd old_project_files
pytest
```

An editable install from `old_project_files/` itself is `pip install -e '.[dev,browser,iheye]'`. The suite covers the agent loop, config precedence, `/url` and fallback, context compaction, reasoning display, browser approvals, the TUI helpers, and iheye focus, hover, summary, and supervisor rules. Those iheye tests do not open AppKit. The overlay itself runs only on macOS. The suite does not launch a live model or a live browser.

Runtime dependencies are `openai`, `rich`, and `textual`. Browser tools additionally need `playwright`. The console entry points are both `hfagent.cli:main`.

## Project layout

```text
.
├── README.md                          # this file
├── package.json                       # separate PDF Sentinel web app (not ihagent)
├── src/                               # that web app
├── public/
├── .hfagent/skills/                   # project skills for this repo
└── old_project_files/
    ├── pyproject.toml                 # package hfagent; extras browser, iheye, dev
    ├── README.md                      # short pointer back here
    ├── scripts/                       # setup-machine, setup-huggingface, serve-local
    ├── tests/
    ├── skills-main/                   # vendored skill library; the loader does not read it
    └── src/hfagent/
        ├── cli.py                     # argparse, TUI vs REPL
        ├── agent.py                   # tool loop, fallback, compaction
        ├── commands.py                # slash commands
        ├── config.py                  # env precedence and host probe
        ├── context.py                 # token estimate and summary
        ├── reasoning.py               # <think> and reasoning fields
        ├── browser.py                 # dedicated Chrome
        ├── iheye/                     # macOS floating eye (helper process)
        ├── tools/                     # files, shell, ssh, memory, browser
        ├── ui/                        # Textual TUI and Rich console
        └── builtin_skills/            # SKILL.md recipes loaded by directory name
```

The webpack app at the repository root (PDF Sentinel) is independent of the agent. Run the agent with `ihagent` after the install above, not with `npm start`.
