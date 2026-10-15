"""Tests for v0.2: edit_file, diff previews, session persistence."""

from hfagent.sessions import list_sessions, load_session, save_session
from hfagent.tools import default_registry


def test_edit_file_success(tmp_path):
    registry = default_registry()
    target = tmp_path / "app.py"
    target.write_text("def greet():\n    return 'hi'\n")
    result = registry.get("edit_file").run(
        path=str(target), old_text="return 'hi'", new_text="return 'hello world'"
    )
    assert "edited" in result
    assert "return 'hello world'" in target.read_text()


def test_edit_file_tolerates_line_whitespace(tmp_path):
    registry = default_registry()
    target = tmp_path / "App.js"
    target.write_text("function App() {\n    return <div />;\n}\n")
    result = registry.get("edit_file").run(
        path=str(target),
        old_text="function App() {\nreturn <div />;\n}",
        new_text="function App() {\n  return <TaskManager />;\n}",
    )
    assert "edited" in result
    assert "<TaskManager />" in target.read_text()


def test_edit_file_not_found(tmp_path):
    registry = default_registry()
    target = tmp_path / "app.py"
    target.write_text("x = 1\n")
    result = registry.get("edit_file").run(
        path=str(target), old_text="nope", new_text="y"
    )
    assert "not found" in result
    assert target.read_text() == "x = 1\n"


def test_edit_file_ambiguous(tmp_path):
    registry = default_registry()
    target = tmp_path / "app.py"
    target.write_text("x = 1\nx = 1\n")
    result = registry.get("edit_file").run(
        path=str(target), old_text="x = 1", new_text="y = 2"
    )
    assert "2 times" in result
    assert target.read_text() == "x = 1\nx = 1\n"


def test_write_file_preview_diff_and_new(tmp_path):
    registry = default_registry()
    target = tmp_path / "app.py"
    registry.get("write_file").run(path=str(target), content="a = 1\n")
    preview = registry.get("write_file").preview(path=str(target), content="a = 2\n")
    assert "-a = 1" in preview and "+a = 2" in preview
    new_preview = registry.get("write_file").preview(
        path=str(tmp_path / "new.py"), content="print(1)\n"
    )
    assert "(new file)" in new_preview


def test_edit_file_preview_shows_diff(tmp_path):
    registry = default_registry()
    target = tmp_path / "app.py"
    target.write_text("a = 1\n")
    preview = registry.get("edit_file").preview(
        path=str(target), old_text="a = 1", new_text="a = 41"
    )
    assert "-a = 1" in preview and "+a = 41" in preview


def test_run_command_preview():
    registry = default_registry()
    preview = registry.get("run_command").preview(command="ls -la")
    assert preview.startswith("$ ls -la")


def test_session_roundtrip(tmp_path):
    from hfagent.sessions import session_dir

    for stale in session_dir(str(tmp_path)).glob("*.json"):
        stale.unlink()  # pytest reuses numbered tmp dirs across runs
    msgs = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "what files are here?"},
        {"role": "assistant", "content": "looking..."},
    ]
    save_session(msgs, model="test-model", cwd=str(tmp_path))
    sessions = list_sessions(cwd=str(tmp_path))
    assert len(sessions) == 1
    assert "what files" in sessions[0]["preview"]
    loaded = load_session("last", cwd=str(tmp_path))
    assert loaded == msgs


def test_load_session_missing_returns_none(tmp_path):
    assert load_session("nope", cwd=str(tmp_path)) is None
