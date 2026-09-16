"""Entry point and REPL for hfagent."""

from __future__ import annotations

import argparse
import sys

from .agent import Agent
from .config import DEFAULT_MODEL, Config
from .sessions import list_sessions, load_session, save_session
from .tools import default_registry
from .ui.console import ConsoleUI

HELP = """\
/help             show this help
/tools            list available tools
/models           list installed Ollama models (--ollama only)
/model <id>       switch model (e.g. Qwen/Qwen3-32B)
/sessions         list saved sessions
/resume [name]    load a saved session ('last' if no name given)
/clear            reset the conversation
/exit, /quit      leave the agent
"""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hfagent",
        description="A terminal coding agent powered by Hugging Face Inference.",
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
        "-y", "--yolo", action="store_true", help="auto-approve write/execute tools"
    )
    parser.add_argument("--version", action="store_true", help="print version and exit")
    return parser


def make_agent(args: argparse.Namespace) -> tuple[Agent, ConsoleUI]:
    try:
        config = Config.load(
            model=args.model,
            base_url=args.base_url,
            max_steps=args.max_steps,
            auto_approve=args.yolo,
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
            "  - or name one explicitly:  hfagent --ollama <model>"
        )
    if not config.api_key and "huggingface.co" in config.base_url:
        sys.exit(
            "No API key found.\n"
            "  export HF_TOKEN=hf_...\n"
            "  (create one at https://huggingface.co/settings/tokens)"
        )
    ui = ConsoleUI()
    return Agent(config, default_registry(), ui), ui


def repl(agent: Agent, ui: ConsoleUI) -> None:
    ui.banner(agent.config.model, agent.registry.names())
    while True:
        try:
            user_input = input("❯ ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not user_input:
            continue
        if user_input.startswith("/"):
            command, *rest = user_input.split(maxsplit=1)
            if command in ("/exit", "/quit"):
                break
            elif command == "/help":
                print(HELP)
            elif command == "/models":
                if "11434" in agent.config.base_url:
                    from .config import _ollama_model_names

                    for name in _ollama_model_names():
                        print(("→ " if name == agent.config.model else "  ") + name)
                else:
                    print("/models only works with --ollama")
            elif command == "/tools":
                ui.on_status(", ".join(agent.registry.names()))
            elif command == "/clear":
                agent.reset()
                ui.on_status("conversation cleared")
            elif command == "/sessions":
                for i, s in enumerate(list_sessions()):
                    print(f"{s['name']}  [{s['model'].split('/')[-1]}]  {s['preview']}")
            elif command == "/resume":
                name = rest[0] if rest else "last"
                handle_resume(agent, ui, name)
            elif command == "/model":
                if rest:
                    agent.config.model = rest[0]
                ui.on_status(f"model: {agent.config.model}")
            else:
                print(f"unknown command {command}\n{HELP}")
            continue
        try:
            agent.run(user_input)
            save_session(agent.messages, agent.config.model)
        except Exception as exc:
            ui.on_error(f"{type(exc).__name__}: {exc}")


def handle_resume(agent: Agent, ui: ConsoleUI, name: str) -> None:
    messages = load_session(name)
    if not messages:
        ui.on_error(f"session not found: {name}")
        return
    agent.set_messages(messages)
    ui.on_status(f"resumed session: {name} ({len(messages)} messages)")


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.version:
        from . import __version__

        print(__version__)
        return
    agent, ui = make_agent(args)
    if args.resume:
        handle_resume(agent, ui, args.resume)
    if args.prompt:
        agent.run(args.prompt)
        save_session(agent.messages, agent.config.model)
        return
    repl(agent, ui)
