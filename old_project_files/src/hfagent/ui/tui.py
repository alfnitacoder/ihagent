"""OpenCode-style Textual TUI: chat pane, sidebar, markdown streaming."""

from __future__ import annotations

import json
import queue
import shutil
import subprocess
import sys
from functools import partial
from pathlib import Path
from typing import Any

from textual import events, on, work
from textual.actions import SkipAction
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.message import Message
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Input,
    Label,
    ListItem,
    ListView,
    Markdown,
    Static,
)

from .. import COMPANY, PRODUCT, __version__
from ..agent import Agent, AgentUI, format_api_error, is_stop_message
from ..commands import dispatch_slash, pasted_api_key
from ..config import host_label
from ..sessions import list_sessions, save_session

MAX_OBS_CHARS = 1_200
NUDGE_PREFIXES = (
    "Do not propose commands",
    "Never write tool_response",
    "You invented a result",
    "You announced:",
    "Continue the task with your tools",
    "Your previous response was empty",
    "do it yourself now with a tool call",
)


def _history_line(text: str) -> str:
    """Keep a pasted API key out of the up-arrow history."""
    if pasted_api_key(text)[0]:
        return "/model_api="
    return text


def is_internal_nudge(text: str) -> bool:
    stripped = text.strip()
    return any(p in stripped for p in NUDGE_PREFIXES)


def _norm_text(text: str) -> str:
    return " ".join(text.split())


def collapse_repeated_lines(text: str) -> str:
    """Keep the first copy when a model loops the same sentence."""
    if not text:
        return text
    out: list[str] = []
    last_norm = ""
    for line in text.splitlines():
        norm = _norm_text(line)
        if not norm:
            if out and out[-1] != "":
                out.append("")
            continue
        if norm == last_norm:
            continue
        out.append(line.rstrip())
        last_norm = norm
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out)


def _dump(arguments: dict) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, indent=2)
    except TypeError:
        return str(arguments)


def write_system_clipboard(text: str) -> bool:
    """Put text on the OS clipboard. OSC 52 alone fails in many macOS terminals."""
    data = text.encode("utf-8")
    if sys.platform == "darwin":
        commands = [["pbcopy"]]
    elif sys.platform == "win32":
        commands = [["clip"]]
    else:
        commands = [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "--clipboard", "--input"],
        ]
    for cmd in commands:
        if shutil.which(cmd[0]) is None:
            continue
        try:
            subprocess.run(cmd, input=data, check=False, timeout=2)
            return True
        except (OSError, subprocess.TimeoutExpired):
            continue
    return False


def format_action(tool_name: str, arguments: dict) -> str:
    if tool_name == "run_command" and isinstance(arguments.get("command"), str):
        bg = "  (background)" if arguments.get("background") else ""
        return f"▶ {tool_name}{bg}\n  $ {arguments['command']}"
    if tool_name == "ssh_run" and isinstance(arguments.get("host"), str):
        cmd = arguments.get("command", "")
        bg = "  (background)" if arguments.get("background") else ""
        return f"▶ {tool_name}{bg}\n  $ ssh {arguments['host']} {cmd!r}"
    return f"▶ {tool_name}\n{_dump(arguments)}"


# ------------------------------------------------------------------ messages


class StatusMsg(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class DeltaMsg(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class DoneMsg(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ActionMsg(Message):
    def __init__(self, tool_name: str, arguments: dict) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.arguments = arguments


class ObsMsg(Message):
    def __init__(self, tool_name: str, result: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.result = result


class ErrorMsg(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class SystemMsg(Message):
    def __init__(self, text: str) -> None:
        super().__init__()
        self.text = text


class ApprovalMsg(Message):
    def __init__(
        self, tool_name: str, preview: str, reply: queue.Queue[bool]
    ) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.preview = preview
        self.reply = reply


class TurnDone(Message):
    pass


class ModelsReady(Message):
    def __init__(self, names: list[str]) -> None:
        super().__init__()
        self.names = names


class RuntimeMsg(Message):
    def __init__(self, device: str, prompt_tokens: int, completion_tokens: int) -> None:
        super().__init__()
        self.device = device
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


class TuiBridge(AgentUI):
    """AgentUI that posts thread-safe messages onto the Textual app."""

    def __init__(self, app: "HfAgentApp") -> None:
        self.app = app

    def on_status(self, text: str) -> None:
        self.app.post_message(StatusMsg(text))

    def on_assistant_delta(self, text: str) -> None:
        self.app.post_message(DeltaMsg(text))

    def on_assistant_done(self, text: str) -> None:
        self.app.post_message(DoneMsg(text))

    def on_action(self, tool_name: str, arguments: dict) -> None:
        self.app.post_message(ActionMsg(tool_name, arguments))

    def on_observation(self, tool_name: str, result: str) -> None:
        self.app.post_message(ObsMsg(tool_name, result))

    def on_error(self, message: str) -> None:
        self.app.post_message(ErrorMsg(message))

    def on_system(self, text: str) -> None:
        self.app.post_message(SystemMsg(text))

    def on_runtime(self, device: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.app.post_message(RuntimeMsg(device, prompt_tokens, completion_tokens))

    def approve(
        self, tool_name: str, arguments: dict, preview: str | None = None
    ) -> bool:
        body = preview if preview is not None else _dump(arguments)
        reply: queue.Queue[bool] = queue.Queue(maxsize=1)
        self.app.post_message(ApprovalMsg(tool_name, body, reply))
        try:
            return bool(reply.get(timeout=600))
        except queue.Empty:
            return False


class Composer(Input):
    """Single-line prompt with up/down command history."""

    BINDINGS = [
        Binding("up", "history_prev", show=False),
        Binding("down", "history_next", show=False),
    ]

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.history: list[str] = []
        self._index: int | None = None
        self._draft = ""

    def remember(self, text: str) -> None:
        if text and (not self.history or self.history[-1] != text):
            self.history.append(text)
        self._index = None
        self._draft = ""

    def action_history_prev(self) -> None:
        if not self.history:
            return
        if self._index is None:
            self._draft = self.value
            self._index = len(self.history) - 1
        elif self._index > 0:
            self._index -= 1
        self.value = self.history[self._index]
        self.cursor_position = len(self.value)

    def action_history_next(self) -> None:
        if self._index is None:
            return
        if self._index < len(self.history) - 1:
            self._index += 1
            self.value = self.history[self._index]
        else:
            self._index = None
            self.value = self._draft
        self.cursor_position = len(self.value)


class SessionRow(ListItem):
    def __init__(self, name: str, preview: str) -> None:
        label = name if not preview else f"{name}\n{preview}"
        super().__init__(Label(label, markup=False))
        self.session_name = name


class ModelRow(ListItem):
    def __init__(self, name: str, current: bool) -> None:
        mark = "→ " if current else "  "
        super().__init__(Label(mark + name, markup=False))
        self.model_name = name


class ModelPicker(ModalScreen[str | None]):
    """Click or press enter on a model. Escape closes without changing."""

    BINDINGS = [
        Binding("escape", "cancel", "Close", show=True, priority=True),
    ]

    def __init__(self, names: list[str], current: str) -> None:
        super().__init__()
        self.names = names
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label("Choose a model", id="model-title")
            yield ListView(
                *[ModelRow(name, name == self.current) for name in self.names],
                id="model-list",
            )

    @on(ListView.Selected, "#model-list")
    def on_model_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if isinstance(item, ModelRow):
            self.dismiss(item.model_name)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ApprovalModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("y", "approve", "Approve", show=True, priority=True),
        Binding("n", "deny", "Deny", show=True, priority=True),
        Binding("enter", "approve", show=False, priority=True),
        Binding("escape", "deny", show=False, priority=True),
    ]

    def __init__(self, tool_name: str, preview: str) -> None:
        super().__init__()
        self.tool_name = tool_name
        self.preview = preview

    def compose(self) -> ComposeResult:
        with Vertical(id="dialog"):
            yield Label(f"approval needed: {self.tool_name}", id="approval-title")
            yield Static(self.preview, markup=False, id="preview")
            with Horizontal(id="buttons"):
                yield Button("Approve [y]", variant="success", id="yes")
                yield Button("Deny [n]", variant="error", id="no")

    def action_approve(self) -> None:
        self.dismiss(True)

    def action_deny(self) -> None:
        self.dismiss(False)

    @on(Button.Pressed, "#yes")
    def on_yes(self) -> None:
        self.dismiss(True)

    @on(Button.Pressed, "#no")
    def on_no(self) -> None:
        self.dismiss(False)


class HfAgentApp(App):
    TITLE = PRODUCT
    CSS = """
    Screen {
        background: $background;
    }

    #header-bar {
        height: 1;
        background: $accent-darken-2;
        color: $text;
        padding: 0 1;
        text-style: bold;
    }

    #sidebar {
        width: 28;
        background: $surface;
        border-right: solid $foreground 20%;
    }

    #sidebar.hidden {
        display: none;
        width: 0;
        border: none;
    }

    .side-title {
        text-style: bold;
        color: $accent;
        padding: 1 1 0 1;
        height: auto;
    }

    #sessions {
        height: 1fr;
        background: $surface;
    }

    #sessions ListItem {
        padding: 0 1;
        height: auto;
    }

    #tools-list {
        height: auto;
        max-height: 12;
        color: $foreground 70%;
        padding: 0 1 1 1;
    }

    #chat {
        height: 1fr;
        padding: 0 1;
        scrollbar-gutter: stable;
    }

    .user {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
        height: auto;
    }

    Markdown.assistant {
        height: auto;
        margin: 0 0 1 0;
        padding: 0 1 0 0;
    }

    .action {
        color: $secondary;
        background: $surface;
        border-left: solid $secondary;
        padding: 0 1;
        margin: 1 0 0 0;
        height: auto;
    }

    .observation {
        color: $foreground 70%;
        border-left: solid $foreground 20%;
        padding: 0 1;
        margin: 0 0 1 0;
        height: auto;
    }

    .system {
        color: $foreground 70%;
        margin: 1 0;
        height: auto;
    }

    .error {
        color: $error;
        background: $error 12%;
        border-left: solid $error;
        padding: 0 1;
        margin: 1 0;
        height: auto;
        width: 100%;
        text-wrap: wrap;
    }

    #status {
        height: 1;
        background: $surface;
        color: $foreground 70%;
        padding: 0 1;
    }

    #composer {
        dock: bottom;
        margin: 0 1 0 1;
        border: tall $accent 50%;
    }

    ModelPicker {
        align: center middle;
    }

    ModelPicker #dialog {
        width: 72;
        max-width: 100%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $accent;
        padding: 1 2;
    }

    ModelPicker #model-title {
        text-style: bold;
        color: $accent;
        height: auto;
    }

    ModelPicker #model-list {
        height: auto;
        max-height: 18;
        margin: 1 0 0 0;
    }

    ApprovalModal {
        align: center middle;
    }

    ApprovalModal #dialog {
        width: 80;
        max-width: 100%;
        height: auto;
        max-height: 80%;
        background: $surface;
        border: thick $warning;
        padding: 1 2;
    }

    ApprovalModal #approval-title {
        text-style: bold;
        color: $warning;
        height: auto;
    }

    ApprovalModal #preview {
        height: auto;
        max-height: 20;
        overflow-y: auto;
        margin: 1 0;
    }

    ApprovalModal #buttons {
        height: auto;
        align: right middle;
    }
    """

    BINDINGS = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("ctrl+b", "toggle_sidebar", "Sidebar"),
        Binding("ctrl+l", "clear_chat", "Clear"),
        Binding("f1", "show_help", "Help"),
        Binding("f2", "pick_model", "Model"),
        Binding(
            "ctrl+c,super+c",
            "copy_or_quit_hint",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+shift+c",
            "copy_selection",
            "Copy",
            show=True,
            priority=True,
        ),
    ]

    def __init__(self, agent: Agent) -> None:
        super().__init__()
        self.agent = agent
        self._busy = False
        self._stream: Any = None
        self._stream_widget: Markdown | None = None
        self._last_assistant_text = ""
        self._approval_queue: queue.Queue[bool] | None = None
        self._device = "…"
        self._prompt_tokens = 0
        self._completion_tokens = 0

    def compose(self) -> ComposeResult:
        yield Static(id="header-bar")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Label("SESSIONS", classes="side-title")
                yield ListView(id="sessions")
                yield Label("TOOLS", classes="side-title")
                yield Static(id="tools-list", markup=False)
            with Vertical(id="main"):
                yield VerticalScroll(id="chat")
                yield Static("ready", id="status", markup=False)
                yield Composer(
                    placeholder=f"Message {PRODUCT}…  /help",
                    id="composer",
                )
        yield Footer()

    async def on_mount(self) -> None:
        self.agent.ui = TuiBridge(self)
        self.refresh_header()
        self.query_one("#tools-list", Static).update(
            "\n".join(self.agent.registry.names())
        )
        await self.refresh_sessions()
        await self._mount_banner()
        await self.replay_history()
        self.query_one(Composer).focus()
        self.run_worker(self._probe_device, thread=True, exclusive=False)

    def _probe_device(self) -> None:
        from ..runtime import infer_device

        device = infer_device(self.agent.config.base_url, self.agent.config.model)
        if not device:
            return
        self.agent.device = device
        self.post_message(
            RuntimeMsg(device, self._prompt_tokens, self._completion_tokens)
        )

    def on_unmount(self) -> None:
        if self._approval_queue is not None:
            try:
                self._approval_queue.put_nowait(False)
            except queue.Full:
                pass

    def refresh_header(self) -> None:
        cfg = self.agent.config
        cwd = Path.cwd().name or str(Path.cwd())
        busy = "  ·  thinking" if self._busy else ""
        self.query_one("#header-bar", Static).update(
            f"{PRODUCT} v{__version__}   {host_label(cfg.base_url)}   {self._device}   "
            f"{self._prompt_tokens} in / {self._completion_tokens} out   "
            f"{cfg.model}  ·  {cfg.approval}  ·  {cwd}{busy}"
        )

    async def refresh_sessions(self) -> None:
        view = self.query_one("#sessions", ListView)
        await view.clear()
        rows = [
            SessionRow(s["name"], s["preview"]) for s in list_sessions()
        ]
        if rows:
            await view.extend(rows)

    async def _mount_banner(self) -> None:
        chat = self.query_one("#chat")
        if chat.children:
            return
        cfg = self.agent.config
        tools = ", ".join(self.agent.registry.names())
        key_hint = ""
        if not cfg.api_key and (
            "ollama.com" in cfg.base_url or "huggingface.co" in cfg.base_url
        ):
            key_hint = "\npaste your cloud key as /model_api=..."
        banner = (
            f"{PRODUCT} v{__version__} — {COMPANY}\n"
            f"model: {cfg.model}\n"
            f"device: {self._device}    tokens: {self._prompt_tokens} in / {self._completion_tokens} out\n"
            f"tools: {tools}\n"
            "/cloud or /local switches host · f2 chooses a model · type stop or add a note while a task runs"
            f"{key_hint}"
        )
        await chat.mount(Static(banner, markup=False, classes="system"))

    async def replay_history(self) -> None:
        pending: dict[str, str] = {}
        last_assistant = ""
        has_turn = False
        for message in self.agent.messages:
            role = message.get("role")
            if role == "system":
                continue
            if role == "user":
                text = message.get("content") or ""
                if is_internal_nudge(text):
                    continue
                has_turn = True
                last_assistant = ""
                await self.mount_user(text)
            elif role == "assistant":
                content = collapse_repeated_lines(message.get("content") or "")
                if content.strip() and _norm_text(content) != last_assistant:
                    last_assistant = _norm_text(content)
                    has_turn = True
                    await self.mount_markdown(content)
                for call in message.get("tool_calls") or []:
                    name = call.get("function", {}).get("name", "tool")
                    call_id = call.get("id", "")
                    if call_id:
                        pending[call_id] = name
                    raw = call.get("function", {}).get("arguments") or "{}"
                    try:
                        args = json.loads(raw)
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}
                    has_turn = True
                    await self.mount_action(name, args)
            elif role == "tool":
                name = pending.get(message.get("tool_call_id"), "tool")
                has_turn = True
                await self.mount_observation(name, message.get("content") or "")
        if has_turn:
            self.set_status("resumed previous session")

    def set_status(self, text: str) -> None:
        self.query_one("#status", Static).update(text)

    async def mount_user(self, text: str) -> None:
        self._last_assistant_text = ""
        chat = self.query_one("#chat")
        await chat.mount(Static(f"❯ {text}", markup=False, classes="user"))
        chat.scroll_end(animate=False)

    async def mount_markdown(self, text: str) -> None:
        collapsed = collapse_repeated_lines(text)
        if not collapsed.strip():
            return
        chat = self.query_one("#chat")
        await chat.mount(Markdown(collapsed, classes="assistant"))
        chat.scroll_end(animate=False)

    async def mount_action(self, tool_name: str, arguments: dict) -> None:
        chat = self.query_one("#chat")
        await chat.mount(
            Static(format_action(tool_name, arguments), markup=False, classes="action")
        )
        chat.scroll_end(animate=False)

    async def mount_observation(self, tool_name: str, result: str) -> None:
        snippet = (
            result if len(result) <= MAX_OBS_CHARS else result[:MAX_OBS_CHARS] + " …"
        )
        chat = self.query_one("#chat")
        await chat.mount(
            Static(
                f"■ {tool_name}\n{snippet}",
                markup=False,
                classes="observation",
            )
        )
        chat.scroll_end(animate=False)

    async def clear_transcript(self) -> None:
        self._last_assistant_text = ""
        self._stream_widget = None
        chat = self.query_one("#chat")
        await chat.remove_children()
        await self._mount_banner()

    async def _ensure_stream(self) -> None:
        if self._stream is not None:
            return
        widget = Markdown("", classes="assistant")
        chat = self.query_one("#chat")
        await chat.mount(widget)
        chat.anchor()
        self._stream_widget = widget
        self._stream = Markdown.get_stream(widget)

    async def _stop_stream(self) -> None:
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                await stream.stop()
            except Exception:
                pass
        chat = self.query_one("#chat")
        chat.anchor(False)
        chat.scroll_end(animate=False)

    def _is_duplicate_assistant(self, text: str) -> bool:
        norm = _norm_text(text)
        return bool(norm) and norm == self._last_assistant_text

    async def _finish_assistant(self, text: str) -> None:
        collapsed = collapse_repeated_lines(text)
        widget = self._stream_widget
        self._stream_widget = None
        if not collapsed.strip():
            if widget is not None and widget.parent is not None:
                await widget.remove()
            return
        if self._is_duplicate_assistant(collapsed):
            if widget is not None and widget.parent is not None:
                await widget.remove()
            self.set_status("model repeated the same plan — not shown again")
            return
        self._last_assistant_text = _norm_text(collapsed)
        if widget is not None:
            if collapsed != text:
                try:
                    await widget.update(collapsed)
                except Exception:
                    pass
        else:
            await self.mount_markdown(collapsed)

    def action_toggle_sidebar(self) -> None:
        self.query_one("#sidebar").toggle_class("hidden")

    def action_clear_chat(self) -> None:
        if self._busy:
            self.set_status("still working — wait, then clear")
            return
        self.agent.reset()
        self.run_worker(self.clear_transcript)
        self.set_status("conversation cleared")

    def copy_to_clipboard(self, text: str) -> None:
        super().copy_to_clipboard(text)
        write_system_clipboard(text)

    def _text_to_copy(self) -> str | None:
        composer = self.query_one(Composer)
        if composer.has_focus and composer.selected_text:
            return composer.selected_text
        selected = self.screen.get_selected_text()
        if selected and selected.strip():
            return selected
        return None

    def _copy_text(self, text: str) -> None:
        self.copy_to_clipboard(text)
        self.notify("Copied", timeout=2)

    def action_copy_selection(self) -> None:
        text = self._text_to_copy()
        if not text:
            self.notify("Nothing selected", timeout=2)
            raise SkipAction()
        self._copy_text(text)

    def action_copy_or_quit_hint(self) -> None:
        text = self._text_to_copy()
        if text:
            self._copy_text(text)
            return
        self.action_help_quit()

    def on_text_selected(self, _event: events.TextSelected) -> None:
        selected = self.screen.get_selected_text()
        if selected and selected.strip():
            self._copy_text(selected)

    def action_pick_model(self) -> None:
        self.set_status("loading models…")
        self.run_worker(self._load_models, thread=True, exclusive=True, group="models")

    def _load_models(self) -> None:
        from ..config import list_model_names

        names = list_model_names(self.agent.config.base_url, self.agent.config.api_key)
        self.post_message(ModelsReady(names))

    def on_models_ready(self, event: ModelsReady) -> None:
        if not event.names:
            self.set_status("no models found on this host")
            return

        def chosen(name: str | None) -> None:
            if name:
                self._apply_model(name)
            else:
                self.set_status("ready")

        self.push_screen(ModelPicker(event.names, self.agent.config.model), chosen)

    def _apply_model(self, name: str) -> None:
        from ..config import save_selected_model

        self.agent.config.model = name
        self._device = "…"
        self.refresh_header()
        save_selected_model(name)
        self.set_status(f"model: {name}")
        self.run_worker(self._probe_device, thread=True, exclusive=False)

    def action_show_help(self) -> None:
        from ..commands import HELP

        self.post_message(SystemMsg(HELP))

    @on(Composer.Submitted, "#composer")
    def on_composer_submitted(self, event: Input.Submitted) -> None:
        text = event.value.strip()
        composer = self.query_one(Composer)
        if not text:
            return
        if self._busy:
            if text.startswith("/") and not is_stop_message(text):
                self.set_status("still working — type a note, or stop")
                return
            composer.remember(text)
            composer.value = ""
            self.agent.steer(text)
            self.run_worker(partial(self._show_steer, text))
            return
        composer.remember(_history_line(text))
        composer.value = ""
        if text.startswith("/"):
            self.run_worker(partial(self._handle_slash, text))
            return
        self.run_worker(partial(self._submit_turn, text))

    async def _show_steer(self, text: str) -> None:
        await self.mount_user(text)
        if is_stop_message(text):
            self.set_status("stopping after this step")
        else:
            self.set_status("noted — it will read this and continue")

    async def _submit_turn(self, text: str) -> None:
        await self.mount_user(text)
        self._start_turn(text)

    async def _handle_slash(self, text: str) -> None:
        result = dispatch_slash(self.agent, self.agent.ui, text)
        command = text.split(maxsplit=1)[0].lower()
        if command in ("/cloud", "/local", "/host"):
            self._device = "…"
            self.run_worker(self._probe_device, thread=True, exclusive=False)
        self.refresh_header()
        await self.refresh_sessions()
        if result == "exit":
            self.exit()
        elif result == "clear":
            await self.clear_transcript()
        elif result == "resume":
            await self.clear_transcript()
            await self.replay_history()

    def _start_turn(self, prompt: str) -> None:
        self._busy = True
        self.refresh_header()
        self.set_status("thinking…  (you can still type)")
        self.query_one(Composer).placeholder = "Add a note, or type stop"
        self.run_worker(
            partial(self._run_turn, prompt),
            thread=True,
            group="agent",
            exit_on_error=False,
        )

    def _run_turn(self, prompt: str) -> None:
        try:
            self.agent.run(prompt)
            save_session(self.agent.messages, self.agent.config.model)
        except Exception as exc:
            self.post_message(ErrorMsg(format_api_error(exc)))
        finally:
            self.post_message(TurnDone())

    @on(ListView.Selected, "#sessions")
    def on_session_selected(self, event: ListView.Selected) -> None:
        item = event.item
        if self._busy or not isinstance(item, SessionRow):
            return
        result = dispatch_slash(self.agent, self.agent.ui, f"/resume {item.session_name}")
        if result == "resume":
            self.run_worker(self._reload_resume)

    async def _reload_resume(self) -> None:
        await self.clear_transcript()
        await self.replay_history()

    @work
    async def request_approval(self, event: ApprovalMsg) -> None:
        self._approval_queue = event.reply
        try:
            allowed = await self.push_screen_wait(
                ApprovalModal(event.tool_name, event.preview)
            )
        except Exception:
            allowed = False
        try:
            event.reply.put_nowait(bool(allowed))
        except queue.Full:
            pass
        self._approval_queue = None

    async def on_status_msg(self, event: StatusMsg) -> None:
        self.set_status(event.text)

    def on_runtime_msg(self, event: RuntimeMsg) -> None:
        self._device = event.device or "…"
        self._prompt_tokens = event.prompt_tokens
        self._completion_tokens = event.completion_tokens
        self.refresh_header()

    async def on_delta_msg(self, event: DeltaMsg) -> None:
        if not event.text:
            return
        await self._ensure_stream()
        try:
            await self._stream.write(event.text)
        except Exception:
            pass

    async def on_done_msg(self, event: DoneMsg) -> None:
        if self._stream is not None:
            await self._stop_stream()
        await self._finish_assistant(event.text or "")

    async def on_action_msg(self, event: ActionMsg) -> None:
        await self.mount_action(event.tool_name, event.arguments)

    async def on_obs_msg(self, event: ObsMsg) -> None:
        await self.mount_observation(event.tool_name, event.result)

    async def on_error_msg(self, event: ErrorMsg) -> None:
        text = event.text.strip() or "request failed"
        label = f"error: {text}"
        chat = self.query_one("#chat")
        previous = [w for w in chat.children if "error" in w.classes]
        if previous and str(getattr(previous[-1], "content", "")) == label:
            self.set_status(label.split("\n", 1)[0][:80])
            return
        await chat.mount(Static(label, markup=False, classes="error"))
        self.set_status(label.split("\n", 1)[0][:80])
        chat.scroll_end(animate=False)

    async def on_system_msg(self, event: SystemMsg) -> None:
        if not event.text:
            return
        chat = self.query_one("#chat")
        await chat.mount(Static(event.text, markup=False, classes="system"))
        chat.scroll_end(animate=False)

    def on_approval_msg(self, event: ApprovalMsg) -> None:
        self.set_status(f"approval needed: {event.tool_name}")
        self.request_approval(event)

    async def on_turn_done(self, event: TurnDone) -> None:
        self._busy = False
        self.refresh_header()
        self.query_one(Composer).placeholder = f"Message {PRODUCT}…  /help"
        status = str(self.query_one("#status", Static).content)
        if status.startswith("thinking") or status.startswith("noted") or status.startswith("stopping") or status.startswith("got your message"):
            self.set_status("ready")
        await self.refresh_sessions()
        self.query_one(Composer).focus()


def run_tui(agent: Agent) -> None:
    app = HfAgentApp(agent)
    agent.ui = TuiBridge(app)
    app.run()
