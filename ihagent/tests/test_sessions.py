"""Session titles, grouping, and the sidebar that renders them."""

import asyncio
import json
import os
from datetime import datetime

from hfagent.agent import Agent, AgentUI
from hfagent.commands import dispatch_slash
from hfagent.config import Config
from hfagent.sessions import (
    SESSION_PAGE,
    build_session_rows,
    delete_session,
    derive_title,
    format_session_listing,
    list_sessions,
    load_session,
    page_session_rows,
    rename_session,
    save_session,
    session_dir,
    toggle_session_flag,
    trim_title,
)
from hfagent.tools import default_registry
from hfagent.ui.tui import Composer, HfAgentApp, SessionList, SessionRow


class Recorder(AgentUI):
    def __init__(self):
        self.status = []
        self.system = []
        self.errors = []

    def on_status(self, text):
        self.status.append(text)

    def on_system(self, text):
        self.system.append(text)

    def on_error(self, message):
        self.errors.append(message)


def _agent():
    return Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test"),
        default_registry(),
        AgentUI(),
    )


def _use_root(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.sessions.SESSIONS_ROOT", tmp_path)
    return session_dir(str(tmp_path))


def _write(folder, name, content, saved_at, model="Qwen/Qwen3-32B", **extra):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": content},
            {"role": "assistant", "content": "ok"},
        ],
    }
    if saved_at is not None:
        payload["saved_at"] = saved_at
    payload.update(extra)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


NOW = datetime(2026, 10, 14, 23, 10, 0)


def test_title_trims_and_skips_nudges():
    long = "can we hack a website " * 6
    title = derive_title([{"role": "user", "content": long}])
    assert len(title) <= 40
    assert title.endswith("…")
    assert title.startswith("can we hack")
    assert (
        derive_title(
            [
                {"role": "user", "content": "Do not propose commands in text."},
                {"role": "user", "content": "  list   the files  "},
            ]
        )
        == "list the files"
    )
    assert trim_title("   ") == "untitled"
    assert derive_title([{"role": "system", "content": "sys"}]) == "untitled"


def test_old_session_file_still_loads(monkeypatch, tmp_path):
    folder = _use_root(monkeypatch, tmp_path)
    body = {
        "model": "test-model",
        "messages": [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "what files are here?"},
            {"role": "assistant", "content": "looking..."},
        ],
    }
    (folder / "20260101-000001.json").write_text(json.dumps(body), encoding="utf-8")
    sessions = list_sessions(cwd=str(tmp_path))
    assert len(sessions) == 1
    assert sessions[0]["preview"].startswith("what files")
    assert sessions[0]["title"] == "what files are here?"
    assert sessions[0]["message_count"] == 2
    assert sessions[0]["pinned"] is False
    assert load_session("last", cwd=str(tmp_path)) == body["messages"]
    assert load_session("missing", cwd=str(tmp_path)) is None


def test_rows_group_by_day_and_do_not_repeat_a_title(monkeypatch, tmp_path):
    folder = _use_root(monkeypatch, tmp_path)
    newest = _write(
        folder,
        "20261014-223433",
        "can we hack a website",
        "2026-10-14T22:34:33",
    )
    _write(
        folder,
        "20261014-222800",
        "can we hack a website",
        "2026-10-14T22:28:00",
    )
    _write(folder, "20261013-180200", "fix the login form", "2026-10-13T18:02:00")
    _write(folder, "20261012-090000", "older note", "2026-10-12T09:00:00")
    sessions = list_sessions(cwd=str(tmp_path))
    rows = build_session_rows(
        sessions, now=NOW, active="20261014-223433"
    )
    headers = [row.primary for row in rows if row.kind == "header"]
    assert headers == ["Today · 2", "Yesterday · 1", "Oct 12 · 1"]
    sessions_rows = [row for row in rows if row.kind == "session"]
    assert sessions_rows[0].active
    assert sessions_rows[0].primary.startswith("● ")
    assert "can we hack a website" in sessions_rows[0].primary
    assert "×2" in sessions_rows[0].primary
    assert "22:34" in sessions_rows[0].secondary
    assert "2 msgs" in sessions_rows[0].secondary
    assert "Qwen3-32B" in sessions_rows[0].secondary
    assert sessions_rows[1].continuation
    assert "can we hack a website" not in sessions_rows[1].primary
    assert "22:28" in sessions_rows[1].primary
    assert "2026-10-14 22:34:33" in sessions_rows[0].tooltip
    assert newest.stem in sessions_rows[0].tooltip
    listing = format_session_listing(sessions, now=NOW, active="20261014-223433")
    assert listing.count("can we hack a website") == 1
    assert "Today · 2" in listing
    assert "20261014-222800" in listing


def test_search_pin_archive_and_rename_roundtrip(monkeypatch, tmp_path):
    folder = _use_root(monkeypatch, tmp_path)
    _write(folder, "20261014-223433", "can we hack a website", "2026-10-14T22:34:33")
    _write(folder, "20261013-180200", "fix the login form", "2026-10-13T18:02:00")
    cwd = str(tmp_path)
    assert rename_session("20261014-223433", "  portal   review  ", cwd=cwd) == "portal review"
    saved = save_session(
        [
            {"role": "user", "content": "can we hack a website"},
            {"role": "assistant", "content": "still the same file"},
        ],
        model="other/model",
        cwd=cwd,
        name="20261014-223433",
    )
    assert saved.name == "20261014-223433.json"
    assert len(list(folder.glob("*.json"))) == 2
    payload = json.loads(saved.read_text(encoding="utf-8"))
    assert payload["title"] == "portal review"
    assert payload["title_custom"] is True
    assert payload["model"] == "other/model"

    ident, pinned = toggle_session_flag("20261013-180200", "pinned", cwd=cwd)
    assert ident == "20261013-180200" and pinned is True
    ident, archived = toggle_session_flag("20261014-223433", "archived", cwd=cwd)
    assert archived is True

    visible = list_sessions(cwd=cwd)
    assert [item["name"] for item in visible] == ["20261013-180200"]
    rows = build_session_rows(list_sessions(cwd=cwd, include_archived=True), now=NOW)
    assert any(row.primary.startswith("Pinned") for row in rows)
    assert all(row.name != "20261014-223433" for row in rows)
    found = build_session_rows(
        list_sessions(cwd=cwd, include_archived=True),
        now=NOW,
        query="portal",
    )
    assert any(row.name == "20261014-223433" for row in found)
    assert "archived" in next(row.secondary or row.primary for row in found if row.name == "20261014-223433")

    fresh = save_session(
        [{"role": "user", "content": "brand new question about logs"}],
        model="m",
        cwd=cwd,
    )
    again = save_session(
        [
            {"role": "user", "content": "brand new question about logs"},
            {"role": "assistant", "content": "answer"},
        ],
        model="m",
        cwd=cwd,
        name=fresh.stem,
    )
    assert again == fresh
    assert json.loads(again.read_text(encoding="utf-8"))["title"].startswith("brand new")


def test_page_window_caps_render():
    rows = build_session_rows(
        [
            {
                "name": f"20261014-{index:06d}",
                "title": f"note {index}",
                "model": "m",
                "message_count": index + 1,
                "when": datetime(2026, 10, 14, 12, index % 60, 0),
                "pinned": False,
                "archived": False,
                "title_custom": False,
            }
            for index in range(SESSION_PAGE + 15)
        ],
        now=NOW,
    )
    assert len(rows) > SESSION_PAGE
    first, offset = page_session_rows(rows, 0, SESSION_PAGE)
    assert len(first) == SESSION_PAGE
    second, offset = page_session_rows(rows, offset, SESSION_PAGE)
    assert second
    assert offset == len(first) + len(second)
    assert page_session_rows(rows, offset, SESSION_PAGE)[0] == []


def test_slash_rename_search_and_pin(monkeypatch, tmp_path):
    folder = _use_root(monkeypatch, tmp_path)
    cwd = str(tmp_path)
    monkeypatch.chdir(tmp_path)
    _write(folder, "20261014-223433", "can we hack a website", "2026-10-14T22:34:33")
    _write(folder, "20261014-222800", "can we hack a website", "2026-10-14T22:28:00")
    agent = _agent()
    ui = Recorder()
    assert dispatch_slash(agent, ui, "/rename portal review") == "handled"
    assert ui.errors[-1].startswith("usage:")
    agent.session_name = "20261014-223433"
    assert dispatch_slash(agent, ui, "/rename portal review") == "handled"
    assert ui.status[-1] == "renamed session: portal review"
    assert dispatch_slash(agent, ui, "/search") == "handled"
    assert "search cleared" in ui.status[-1]
    assert dispatch_slash(agent, ui, "/search portal") == "handled"
    assert "portal review" in ui.system[-1]
    assert "can we hack a website" not in ui.system[-1]
    assert dispatch_slash(agent, ui, "/pin 20261014-222800") == "handled"
    assert ui.status[-1].startswith("pinned ")
    assert dispatch_slash(agent, ui, "/archive") == "handled"
    assert "archived 20261014-223433" in ui.status[-1]
    assert dispatch_slash(agent, ui, "/sessions") == "handled"
    assert "20261014-223433" not in ui.system[-1]
    assert dispatch_slash(agent, ui, "/sessions all") == "handled"
    assert "20261014-223433" in ui.system[-1]
    assert dispatch_slash(agent, ui, "/search portal") == "handled"
    assert "archived" in ui.system[-1]
    assert delete_session("20261014-222800", cwd=cwd) == "20261014-222800"


def _labels(app):
    texts = []
    for item in app.query_one(SessionList).children:
        for label in item.query("Label"):
            texts.append(str(label.content))
    return texts


def test_sidebar_renders_titles_and_keyboard(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.sessions.SESSIONS_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    folder = session_dir()
    _write(folder, "20261014-223433", "can we hack a website", "2026-10-14T22:34:33")
    _write(folder, "20261014-222800", "can we hack a website", "2026-10-14T22:28:00")
    monkeypatch.setattr(
        "hfagent.ui.tui.session_clock", lambda: NOW
    )
    agent = _agent()
    agent.session_name = "20261014-223433"
    app = HfAgentApp(agent)

    async def scenario():
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause()
            labels = _labels(app)
            blob = "\n".join(labels)
            assert "Today" in blob
            assert "can we hack a website" in blob
            assert "×2" in blob
            assert "22:34" in blob
            assert "msgs" in blob
            assert "●" in blob
            assert not any(text.startswith("20261014") for text in labels)
            rows = [child for child in app.query_one(SessionList).children if isinstance(child, SessionRow)]
            active = next(row for row in rows if row.view.active)
            assert "2026-10-14 22:34:33" in (active.tooltip or "")
            continuations = [row for row in rows if row.view.continuation]
            assert continuations
            assert "can we hack a website" not in continuations[0].view.primary

            await pilot.press("f3")
            await pilot.pause()
            assert app.query_one(SessionList).has_focus
            await pilot.press("r")
            await pilot.pause()
            rename = app.query_one("#session-rename")
            assert rename.has_class("visible")
            assert "can we hack a website" in rename.value
            rename.value = "portal review"
            await pilot.press("enter")
            await pilot.pause()
            stored = json.loads((folder / "20261014-223433.json").read_text(encoding="utf-8"))
            assert stored["title"] == "portal review"
            assert stored["title_custom"] is True
            assert any("portal review" in text for text in _labels(app))

            await pilot.press("p")
            await pilot.pause()
            pinned = json.loads((folder / "20261014-223433.json").read_text(encoding="utf-8"))
            assert pinned["pinned"] is True
            assert any(text.startswith("Pinned") for text in _labels(app))

            await pilot.press("slash")
            await pilot.pause()
            filt = app.query_one("#session-filter")
            assert filt.has_focus
            await pilot.press("z", "z", "z")
            await pilot.pause()
            assert any("No matches" in text for text in _labels(app))
            filt.value = ""
            await pilot.pause()

            await pilot.press("escape")
            await pilot.pause()
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert agent.session_name
            assert any(
                message.get("content") == "can we hack a website"
                for message in agent.messages
                if message.get("role") == "user"
            )

    asyncio.run(scenario())


def test_sidebar_mounts_a_window_then_loads_more(monkeypatch, tmp_path):
    monkeypatch.setattr("hfagent.sessions.SESSIONS_ROOT", tmp_path)
    monkeypatch.chdir(tmp_path)
    folder = session_dir()
    for index in range(SESSION_PAGE + 12):
        _write(
            folder,
            f"20261014-{index:06d}",
            f"distinct topic {index}",
            f"2026-10-14T12:{index % 60:02d}:00",
            model="m",
        )
    monkeypatch.setattr("hfagent.ui.tui.session_clock", lambda: NOW)
    app = HfAgentApp(_agent())

    async def scenario():
        async with app.run_test(size=(110, 36)) as pilot:
            await pilot.pause()
            view = app.query_one(SessionList)
            assert len(view.children) == SESSION_PAGE
            view.index = len(view.children) - 1
            await pilot.pause()
            await pilot.pause()
            assert len(view.children) > SESSION_PAGE

    asyncio.run(scenario())


def test_delete_still_removes_one_file(monkeypatch, tmp_path):
    folder = _use_root(monkeypatch, tmp_path)
    _write(folder, "20260101-000001", "hi", None, model="m")
    _write(folder, "20260101-000002", "hi", None, model="m")
    assert delete_session("20260101-000002", cwd=str(tmp_path)) == "20260101-000002"
    assert not (folder / "20260101-000002.json").exists()
    assert load_session("last", cwd=str(tmp_path)) is not None
    assert os.path.basename(list_sessions(cwd=str(tmp_path))[0]["path"]).startswith(
        "20260101-000001"
    )
