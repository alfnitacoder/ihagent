"""Tests for the built-in tool registry."""

from hfagent.tools import default_registry


def test_specs_are_valid_function_specs():
    registry = default_registry()
    specs = registry.specs()
    names = {spec["function"]["name"] for spec in specs}
    assert names == {
        "list_dir", "read_file", "write_file", "edit_file", "grep",
        "run_command", "ssh_run",
    }
    for spec in specs:
        assert spec["type"] == "function"
        assert spec["function"]["parameters"]["type"] == "object"


def test_write_then_read(tmp_path):
    registry = default_registry()
    target = tmp_path / "src" / "main.py"
    result = registry.get("write_file").run(path=str(target), content="print('hi')\n")
    assert "wrote" in result
    assert "print('hi')" in registry.get("read_file").run(path=str(target))


def test_list_dir(tmp_path):
    registry = default_registry()
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
    listing = registry.get("list_dir").run(path=str(tmp_path / "pkg"))
    assert "mod.py" in listing


def test_grep_finds_matches(tmp_path):
    registry = default_registry()
    (tmp_path / "a.py").write_text("def hello():\n    pass\n")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("hello = 1\n")
    result = registry.get("grep").run(pattern="hello", path=str(tmp_path))
    assert f"{tmp_path}/a.py:1" in result
    assert f"{tmp_path}/sub/b.py:1" in result


def test_run_command_reports_exit_code():
    registry = default_registry()
    result = registry.get("run_command").run(command="echo hello", timeout=10)
    assert "exit code: 0" in result
    assert "hello" in result


def test_unknown_tool_message():
    registry = default_registry()
    assert registry.get("nope") is None


def test_ssh_run_spec_and_preview():
    registry = default_registry()
    spec = next(s for s in registry.specs() if s["function"]["name"] == "ssh_run")
    assert set(spec["function"]["parameters"]["properties"]) == {"host", "command", "timeout"}
    preview = registry.get("ssh_run").preview(host="wantok20", command="uptime")
    assert preview.startswith("$ ssh wantok20 'uptime'")


def test_ssh_run_bad_host_fails_fast():
    registry = default_registry()
    result = registry.get("ssh_run").run(
        host="nonexistent-host-invalid-zz", command="echo hi", timeout=20
    )
    # BatchMode + bad host -> ssh exits 255 with an error, never hangs
    assert result.startswith("exit code: 255") or "Error" in result
