"""TUI helpers and a headless Textual smoke test."""

import asyncio
import json

from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import Static

from hfagent.agent import Agent, AgentUI
from hfagent.config import Config
from hfagent.tools import default_registry
from hfagent.agent import format_api_error
from hfagent.ui.tui import (
    ApprovalModal,
    Composer,
    ErrorMsg,
    HfAgentApp,
    collapse_repeated_lines,
    format_action,
    is_internal_nudge,
)
from tests.test_agent import Chunk, Delta, FakeClient, ToolCallDelta


async def _wait_until(pred, pilot, timeout=5.0, step=0.05):
    loops = int(timeout / step)
    for _ in range(loops):
        if pred():
            return
        await asyncio.sleep(step)
        await pilot.pause()
    raise AssertionError("timed out waiting for TUI state")


def _chat_contents(app, class_name: str) -> list[str]:
    chat = app.query_one("#chat")
    return [
        str(getattr(w, "content", ""))
        for w in chat.children
        if class_name in w.classes
    ]


def _agent(model="test-model", approval="auto"):
    return Agent(
        Config(
            api_key="none",
            base_url="http://127.0.0.1:9/v1",
            model=model,
            approval=approval,
        ),
        default_registry(),
        AgentUI(),
    )


def test_collapse_repeated_lines():
    line = (
        "I'll edit the TaskManager.js file to include the edit task "
        "functionality."
    )
    stacked = "\n\n".join([line] * 12)
    assert collapse_repeated_lines(stacked) == line
    assert collapse_repeated_lines(line) == line


def test_is_internal_nudge():
    assert is_internal_nudge("Do not propose commands in text or ask permission.")
    assert is_internal_nudge("You announced: \"I'll scan it\"")
    assert not is_internal_nudge("list the files in this repo")


def test_format_action_shell_and_ssh():
    text = format_action("run_command", {"command": "ls -la", "background": True})
    assert "run_command" in text
    assert "$ ls -la" in text
    assert "background" in text
    ssh = format_action("ssh_run", {"host": "kali219", "command": "df -h"})
    assert "kali219" in ssh
    assert "df -h" in ssh


def test_tui_composes_and_help():
    agent = Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test-model"),
        default_registry(),
        AgentUI(),
    )
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test() as pilot:
            header = app.query_one("#header-bar")
            assert "IHAgent" in str(header.content)
            assert "test-model" in str(header.content)
            assert app.query_one("#sidebar")
            assert app.query_one("#chat")
            composer = app.query_one("#composer", Composer)
            composer.value = "/help"
            await composer.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            bodies = [str(w.content) for w in app.query(".system")]
            assert any("/tools" in body for body in bodies)

    asyncio.run(scenario())


def test_tui_copies_chat_selection(monkeypatch):
    copied: list[str] = []
    monkeypatch.setattr(
        "hfagent.ui.tui.write_system_clipboard",
        lambda text: copied.append(text) or True,
    )
    app = HfAgentApp(_agent())

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.pause()
            status = app.query_one("#status", Static)
            app.screen.selections = {
                status: Selection.from_offsets(Offset(0, 0), Offset(5, 0))
            }
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.clipboard == "ready"
            assert copied == ["ready"]

            composer = app.query_one("#composer", Composer)
            composer.value = "abcdef"
            composer.selection = type(composer.selection)(0, 3)
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.clipboard == "abc"
            assert copied[-1] == "abc"

            composer.selection = type(composer.selection)(0, 0)
            app.screen.clear_selection()
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert app.clipboard == "abc"
            assert copied[-1] == "abc"

    asyncio.run(scenario())


def test_tui_note_and_stop_while_busy():
    app = HfAgentApp(_agent())

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            app._busy = True
            composer = app.query_one("#composer", Composer)
            composer.value = "check the tests too"
            await composer.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.agent._inbox.get_nowait() == "check the tests too"
            assert any("check the tests too" in body for body in _chat_contents(app, "user"))
            assert app._busy

            composer.value = "stop"
            await composer.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.agent._inbox.get_nowait() == "stop"
            assert any("stop" in body for body in _chat_contents(app, "user"))

    asyncio.run(scenario())


def test_tui_sidebar_toggle_and_tools():
    app = HfAgentApp(_agent())

    async def scenario():
        async with app.run_test(size=(120, 36)) as pilot:
            sidebar = app.query_one("#sidebar")
            assert not sidebar.has_class("hidden")
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert sidebar.has_class("hidden")
            await pilot.press("ctrl+b")
            await pilot.pause()
            assert not sidebar.has_class("hidden")
            tools = str(app.query_one("#tools-list").content)
            assert "read_file" in tools and "run_command" in tools
            composer = app.query_one("#composer", Composer)
            composer.value = "/approval auto"
            await composer.action_submit()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "auto" in str(app.query_one("#header-bar").content)

    asyncio.run(scenario())


def test_tui_fake_tool_turn_streams_and_shows_cards(tmp_path):
    (tmp_path / "hello.txt").write_text("hi")
    agent = _agent(approval="auto")
    agent.client = FakeClient(
        [
            [
                Chunk(
                    Delta(
                        tool_calls=[
                            ToolCallDelta(
                                0,
                                id="call_1",
                                name="list_dir",
                                arguments=json.dumps({"path": str(tmp_path)}),
                            )
                        ]
                    )
                )
            ],
            [
                Chunk(Delta(content="Found ")),
                Chunk(Delta(content="**hello.txt**")),
            ],
        ]
    )
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 36)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.value = "what files are here?"
            await composer.action_submit()
            await _wait_until(
                lambda: any(
                    "what files are here?" in text for text in _chat_contents(app, "user")
                ),
                pilot,
            )
            await _wait_until(
                lambda: any("list_dir" in text for text in _chat_contents(app, "action")),
                pilot,
            )
            await _wait_until(
                lambda: any(
                    "hello.txt" in text for text in _chat_contents(app, "observation")
                ),
                pilot,
            )
            await _wait_until(lambda: not app._busy, pilot)
            assert app.query("Markdown.assistant")
            assert "thinking" not in str(app.query_one("#header-bar").content)

    asyncio.run(scenario())


def test_tui_approval_modal_deny(tmp_path):
    agent = _agent(approval="default")
    agent.client = FakeClient(
        [
            [
                Chunk(
                    Delta(
                        tool_calls=[
                            ToolCallDelta(
                                0,
                                id="call_1",
                                name="run_command",
                                arguments=json.dumps({"command": "echo hi"}),
                            )
                        ]
                    )
                )
            ],
            [Chunk(Delta(content="stopped"))],
        ]
    )
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 36)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.value = "run echo hi"
            await composer.action_submit()
            await _wait_until(
                lambda: isinstance(app.screen, ApprovalModal), pilot
            )
            await pilot.pause()
            await pilot.click(app.screen.query_one("#no"))
            await _wait_until(
                lambda: any(
                    "declined" in text.lower()
                    for text in _chat_contents(app, "observation")
                ),
                pilot,
            )
            await _wait_until(lambda: not app._busy, pilot)

    asyncio.run(scenario())


def test_tui_replay_skips_duplicate_announces():
    line = "I'll edit the TaskManager.js file to include the edit task functionality."
    agent = _agent()
    agent.messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "add edit task"},
        *[{"role": "assistant", "content": line} for _ in range(8)],
    ]
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 36)) as pilot:
            await pilot.pause()
            assert len(app.query("Markdown.assistant")) == 1

    asyncio.run(scenario())


def test_tui_live_turn_does_not_stack_same_announce():
    line = "I'll edit the TaskManager.js file to include the edit task functionality."
    agent = _agent(approval="auto")
    agent.client = FakeClient(
        [
            [Chunk(Delta(content=line))],
            [Chunk(Delta(content=f"{line}\n{line}\n{line}"))],
            [Chunk(Delta(content=line))],
            [Chunk(Delta(content="done editing"))],
        ]
    )
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test(size=(120, 36)) as pilot:
            composer = app.query_one("#composer", Composer)
            composer.value = "add the edit task"
            await composer.action_submit()
            await _wait_until(lambda: not app._busy, pilot, timeout=8)
            # 3 identical announces + a final reply must not paint 4 bubbles
            assert len(app.query("Markdown.assistant")) <= 2

    asyncio.run(scenario())


def test_tui_collapses_duplicate_errors():
    app = HfAgentApp(_agent())
    dump = (
        "BadRequestError: Error code: 400 - {'error': 'auto tool choice "
        "requires --enable-auto-tool-choice and --tool-call-parser to be set'}"
    )
    text = format_api_error(Exception(dump))

    async def scenario():
        async with app.run_test(size=(100, 30)) as pilot:
            app.post_message(ErrorMsg(text))
            await pilot.pause()
            app.post_message(ErrorMsg(text))
            await pilot.pause()
            errors = list(app.query(".error"))
            assert len(errors) == 1
            assert "Retrying in text-tool mode" in str(errors[0].content)
            assert "BadRequestError" not in str(errors[0].content)

    asyncio.run(scenario())
