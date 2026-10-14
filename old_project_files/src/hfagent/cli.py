"""Entry point, TUI launcher, and classic line REPL for hfagent."""

from __future__ import annotations

import argparse
import queue
import select
import sys

from .agent import Agent, AgentUI
from .commands import dispatch_slash
from .config import DEFAULT_MODEL, Config, format_config_report
from .sessions import open_session, save_session
from .tools import default_registry
from .ui.console import ConsoleUI


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ihagent",
        description="IHAgent — InnovatelHub Solutions Ltd. A terminal coding agent.",
    )
    parser.add_argument("-p", "--prompt", help="run a single prompt and exit")
    parser.add_argument("--model", default=None, help=f"model id (default: {DEFAULT_MODEL})")
    parser.add_argument("--base-url", default=None, help="OpenAI-compatible API base URL")
    parser.add_argument("--max-steps", type=int, default=None, help="agent loop cap")
    parser.add_argument(
        "--temperature",
        type=float,
        default=None,
        help="sampling temperature (default 0.0 = greedy, best for tool calls)",
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="use a local mlx_lm.server (default http://127.0.0.1:1234/v1)",
    )
    parser.add_argument(
        "--resume",
        nargs="?",
        const="last",
        default=None,
        metavar="NAME",
        help="continue a saved session (default: the most recent one)",
    )
    parser.add_argument(
        "--ollama",
        nargs="?",
        const="",
        default=None,
        metavar="MODEL",
        help="use a local Ollama model (auto-detects if no name given)",
    )
    parser.add_argument(
        "--approval",
        choices=("auto", "default"),
        default=None,
        help="approval mode: 'auto' runs gated tools without asking; "
        "'default' prompts y/N (env: HFAGENT_APPROVAL)",
    )
    parser.add_argument(
        "-y", "--yolo", action="store_true", help="shorthand for --approval auto"
    )
    ui_mode = parser.add_mutually_exclusive_group()
    ui_mode.add_argument(
        "--console",
        action="store_true",
        help="classic line REPL instead of the TUI",
    )
    ui_mode.add_argument(
        "--tui",
        action="store_true",
        help="force the TUI even if stdin/stdout is not a TTY",
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    parser.add_argument(
        "--show-config",
        action="store_true",
        help="print effective settings and where each one came from, then exit",
    )
    return parser


def use_tui(
    args: argparse.Namespace,
    *,
    stdin_tty: bool | None = None,
    stdout_tty: bool | None = None,
) -> bool:
    """TUI is the default on a real terminal; -p and --console stay line-oriented."""
    if args.prompt:
        return False
    if args.console:
        return False
    if args.tui:
        return True
    in_tty = sys.stdin.isatty() if stdin_tty is None else stdin_tty
    out_tty = sys.stdout.isatty() if stdout_tty is None else stdout_tty
    return in_tty and out_tty


def make_agent(
    args: argparse.Namespace,
    ui: AgentUI | None = None,
    *,
    require_key: bool = True,
) -> tuple[Agent, AgentUI]:
    try:
        config = Config.load(
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
            approval="auto" if args.yolo else args.approval,
            temperature=args.temperature,
            local=args.local,
            ollama=args.ollama,
        )
    except ValueError as exc:  # ambiguous --ollama name
        sys.exit(str(exc))
    if args.ollama is not None and not config.model:
        sys.exit(
            "No Ollama model found.\n"
            "  - is Ollama running?  (ollama serve)\n"
            "  - is a model installed?  (ollama list)\n"
            "  - or name one explicitly:  ihagent --ollama <model>"
        )
    if require_key and not config.api_key and "huggingface.co" in config.base_url:
        sys.exit(
            "No API key found.\n"
            "  start ihagent and paste:  /model_api=...\n"
            "  or: export HF_TOKEN=hf_...\n"
            "  (create one at https://huggingface.co/settings/tokens)"
        )
    if (
        require_key
        and not config.api_key
        and "ollama.com" in config.base_url
    ):
        sys.exit(
            "No Ollama API key found.\n"
            "  start ihagent and paste:  /model_api=...\n"
            "  or put OLLAMA_API_KEY=... in .env\n"
            "  (create one at https://ollama.com/settings/keys)"
        )
    ui = ui or ConsoleUI()
    return Agent(config, default_registry(), ui), ui


def repl(agent: Agent, ui: ConsoleUI) -> None:
    chats: queue.Queue[str] = queue.Queue()
    approvals: queue.Queue[bool] = queue.Queue()
    busy = {"on": False}
    ui.eye_approvals = approvals

    def on_chat(text: str, prompt=None) -> None:
        shown = text
        body = text if prompt is None else prompt
        if busy["on"]:
            ui.console.print(f"❯ {shown}", markup=False, highlight=False)
            agent.steer(body)
            ui.on_status("noted — it will read this and continue")
            return
        if prompt is None or prompt == text:
            chats.put(text)
        else:
            chats.put((shown, body))

    def on_approval(yes: bool) -> None:
        approvals.put(bool(yes))

    from .iheye import bind_handlers, unbind_handlers

    bind_handlers(on_chat, on_approval)
    try:
        from .iheye import get_supervisor

        control = get_supervisor()
        control.model_name = agent.config.model
        control.model_base = agent.config.base_url
    except Exception:
        pass
    ui.banner(agent.config.model, agent.registry.names())
    try:
        while True:
            try:
                user_input = _next_console_line(chats)
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if user_input is None or user_input == "":
                continue
            if isinstance(user_input, str) and user_input.startswith("/"):
                result = dispatch_slash(agent, ui, user_input)
                if result == "exit":
                    break
                continue
            busy["on"] = True
            try:
                agent.run(user_input)
                _autosave(agent)
            except Exception as exc:
                from .agent import format_api_error

                ui.on_error(
                    format_api_error(
                        exc,
                        base_url=agent.config.base_url,
                        model=agent.config.model,
                        key_var=agent.config.api_key_var,
                    )
                )
            finally:
                busy["on"] = False
    finally:
        unbind_handlers()
        ui.eye_approvals = None


def _next_console_line(chats: queue.Queue) -> str:
    """Read the prompt, or a hover-chat line that arrived while we were waiting.

    A look capture arrives as ``(shown, body)``. The console prints ``shown``
    and the agent receives ``body``, which may include the front app.
    """
    sys.stdout.write("❯ ")
    sys.stdout.flush()
    while True:
        try:
            eyed = chats.get_nowait()
        except queue.Empty:
            eyed = ""
        if isinstance(eyed, tuple):
            shown, body = eyed
        else:
            shown, body = eyed, eyed
        if shown:
            sys.stdout.write(str(shown) + "\n")
            sys.stdout.flush()
            if isinstance(body, str):
                return body.strip()
            return body
        try:
            ready, _, _ = select.select([sys.stdin], [], [], 0.2)
        except (OSError, ValueError):
            return input().strip()
        if not ready:
            continue
        line = sys.stdin.readline()
        if line == "":
            raise EOFError
        return line.strip()


def _autosave(agent: Agent) -> None:
    """Update the active session file, or create one and remember its name."""
    path = save_session(
        agent.messages, agent.config.model, name=agent.session_name
    )
    agent.session_name = path.stem


def handle_resume(agent: Agent, ui: AgentUI, name: str) -> None:
    opened = open_session(name)
    if not opened or not opened["messages"]:
        ui.on_error(f"session not found: {name}")
        return
    agent.set_messages(opened["messages"])
    agent.session_name = opened["name"]
    ui.on_status(
        f"resumed session: {opened['name']} ({len(opened['messages'])} messages)"
    )


def _start_robot_bridge() -> None:
    try:
        from .robot_bridge import start_if_enabled

        start_if_enabled()
    except Exception:
        return


def _stop_robot_bridge() -> None:
    try:
        from .robot_bridge import stop

        stop()
    except Exception:
        return


def _start_iheye() -> None:
    try:
        from .iheye import start_if_enabled

        status = start_if_enabled()
    except Exception:
        return
    if status.reason in {"missing-pyobjc", "helper-exited", "spawn-failed"} and status.message:
        print(status.message, file=sys.stderr)


def _stop_iheye() -> None:
    try:
        from .iheye import stop

        stop()
    except Exception:
        pass


def _shutdown_browser() -> None:
    try:
        from .browser import shutdown_browser

        shutdown_browser()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.version:
        from . import __version__

        print(__version__)
        return
    if args.show_config:
        config = Config.load(
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
            approval="auto" if args.yolo else args.approval,
            temperature=args.temperature,
            local=args.local,
            ollama=args.ollama,
        )
        print(format_config_report(config))
        return
    try:
        _start_robot_bridge()
        if use_tui(args):
            agent, _ = make_agent(args, ui=AgentUI(), require_key=False)
            if args.resume:
                opened = open_session(args.resume)
                if not opened or not opened["messages"]:
                    sys.exit(f"session not found: {args.resume}")
                agent.set_messages(opened["messages"])
                agent.session_name = opened["name"]
            try:
                from .ui.tui import run_tui
            except ImportError:
                sys.exit(
                    "textual is required for the TUI.\n"
                    "  pip install 'textual>=8'\n"
                    "  or run:  ihagent --console"
                )
            _start_iheye()
            run_tui(agent)
            return
        agent, ui = make_agent(args, require_key=bool(args.prompt))
        if args.resume:
            handle_resume(agent, ui, args.resume)
        _start_iheye()
        if args.prompt:
            agent.run(args.prompt)
            _autosave(agent)
            return
        if not isinstance(ui, ConsoleUI):
            ui = ConsoleUI()
            agent.ui = ui
        repl(agent, ui)
    finally:
        _stop_robot_bridge()
        _stop_iheye()
        _shutdown_browser()
