"""Assists for smaller models: guards, summaries, installs, tests, audit."""

import json

from hfagent.agent import REPEAT_FAIL_HINT
from hfagent.assist.audit import audit
from hfagent.assist.cache import clear, lookup, remember
from hfagent.assist.deps import InstallScheduler, plan_install
from hfagent.assist.errors import enrich_failure, path_context
from hfagent.assist.hosts import known_hosts, suggestion
from hfagent.assist.security import apply_finding, apply_with_approval, scan_text
from hfagent.assist.settings import env_flag, tool_guard
from hfagent.assist.style import apply_header, style_notes
from hfagent.assist.summary import is_long, summarize
from hfagent.assist.testing import detect_frameworks, parse_test_command, run_project_tests, smoke_plan
from hfagent.commands import HELP, dispatch_slash
from hfagent.tools.files import ReadFile
from tests.test_agent import Chunk, Delta, ToolCallDelta, make_agent
from tests.test_commands import Recorder


def test_flags_default_on_and_master_switch(monkeypatch):
    monkeypatch.delenv("HFAGENT_TOOL_GUARD", raising=False)
    monkeypatch.delenv("HFAGENT_ASSISTS", raising=False)
    assert tool_guard() is True
    monkeypatch.setenv("HFAGENT_TOOL_GUARD", "off")
    assert tool_guard() is False
    monkeypatch.setenv("HFAGENT_TOOL_GUARD", "1")
    monkeypatch.setenv("HFAGENT_ASSISTS", "0")
    assert env_flag("HFAGENT_STYLE_AUDIT") is False
    assert tool_guard() is False


def test_missing_path_lists_directory_and_close_names(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "index.jsx").write_text("export {}\n")
    missing = tmp_path / "src" / "index.js"
    text = path_context(str(missing))
    assert "Directory listing of" in text
    assert "index.jsx" in text
    assert "Closest paths:" in text
    enriched = enrich_failure(
        "read_file",
        {"path": str(missing)},
        f"Error: FileNotFoundError: {missing}",
    )
    assert enriched.startswith("Error:")
    assert "index.jsx" in enriched


def test_identical_failing_read_is_not_rerun(tmp_path, monkeypatch):
    calls = {"n": 0}
    original = ReadFile.run

    def wrapped(self, path):
        calls["n"] += 1
        return original(self, path)

    monkeypatch.setattr(ReadFile, "run", wrapped)
    missing = str(tmp_path / "src" / "index.js")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "App.js").write_text("export {}\n")

    def fail_read(i):
        return [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id=f"call_{i}",
                            name="read_file",
                            arguments=json.dumps({"path": missing}),
                        )
                    ]
                )
            )
        ]

    turns = [fail_read(0), fail_read(1), [Chunk(Delta(content="stopped"))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.max_steps = 6
    agent.run("read the missing file")
    assert calls["n"] == 1
    blob = "\n".join(obs for _name, obs in ui.observations)
    assert "Directory listing of" in blob
    assert "App.js" in blob
    assert "BLOCKED:" in blob
    assert "HINT:" in blob
    assert REPEAT_FAIL_HINT.strip() in blob


def test_tool_guard_off_reruns_the_read(tmp_path, monkeypatch):
    monkeypatch.setenv("HFAGENT_TOOL_GUARD", "0")
    calls = {"n": 0}
    original = ReadFile.run

    def wrapped(self, path):
        calls["n"] += 1
        return original(self, path)

    monkeypatch.setattr(ReadFile, "run", wrapped)
    missing = str(tmp_path / "missing.js")

    def fail_read(i):
        return [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id=f"call_{i}",
                            name="read_file",
                            arguments=json.dumps({"path": missing}),
                        )
                    ]
                )
            )
        ]

    agent, _ui = make_agent(tmp_path, [fail_read(0), fail_read(1), [Chunk(Delta(content="done"))]])
    agent.run("read it")
    assert calls["n"] == 2


def test_long_command_is_summarized_and_output_restores_it(tmp_path):
    noisy = "exit code: 1\n" + "\n".join(
        f"line {i} ok" if i % 17 else f"Error: boom at line {i}" for i in range(80)
    )
    assert is_long(noisy)
    summary = summarize("run_command", noisy)
    assert "exit code: 1" in summary
    assert "Error: boom" in summary
    assert "/output" in summary
    assert len(summary) < len(noisy)

    from tests.test_agent import make_agent as build

    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="run_command",
                            arguments=json.dumps({"command": "printf 'x\\n%.0s' {1..80}", "timeout": 10}),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="summarized"))],
    ]
    agent, ui = build(tmp_path, turns)
    agent.config.approval = "auto"
    ui.allow = True
    # Force a long observation without depending on shell length.
    real = agent._execute

    def execute(call):
        if json.loads(call["function"]["arguments"]).get("command", "").startswith("printf"):
            call = {
                "id": call["id"],
                "type": "function",
                "function": {
                    "name": "run_command",
                    "arguments": json.dumps({"command": "echo hi", "timeout": 10}),
                },
            }
        return real(call)

    agent._execute = execute
    agent.run("run it")
    agent._last_full_output = noisy
    ui2 = Recorder()
    assert dispatch_slash(agent, ui2, "/output") == "handled"
    assert "Error: boom at line 0" in ui2.system[-1]


def test_summary_is_what_the_user_sees(tmp_path, monkeypatch):
    monkeypatch.setenv("HFAGENT_SUMMARY_MIN_CHARS", "200")
    body = "exit code: 2\n" + ("warning: slow\n" * 5) + ("x\n" * 40)
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="run_command",
                            arguments=json.dumps({"command": "echo hi", "timeout": 10}),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="done"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "auto"
    ui.allow = True
    real_run = agent.registry.get("run_command").run

    def fake_run(command, timeout=60, background=False):
        if command == "echo hi":
            return body
        return real_run(command, timeout=timeout, background=background)

    agent.registry.get("run_command").run = fake_run
    agent.run("echo")
    shown = ui.observations[-1][1]
    assert "Summary of run_command" in shown
    assert "exit code: 2" in shown
    tool_msgs = [m["content"] for m in agent.messages if m["role"] == "tool"]
    assert tool_msgs[-1].startswith("exit code: 2")
    assert "Summary of run_command" not in tool_msgs[-1]


def test_install_plan_picks_the_package_manager(tmp_path):
    (tmp_path / "package.json").write_text("{}\n")
    assert plan_install(str(tmp_path / "package.json"))["label"] == "npm install"
    (tmp_path / "pnpm-lock.yaml").write_text("lock: 1\n")
    assert plan_install(str(tmp_path / "package.json"))["cmd"][0] == "pnpm"
    (tmp_path / "pnpm-lock.yaml").unlink()
    (tmp_path / "yarn.lock").write_text("# yarn\n")
    assert plan_install(str(tmp_path / "package.json"))["cmd"][0] == "yarn"
    (tmp_path / "requirements.txt").write_text("flask==1\n")
    pip = plan_install(str(tmp_path / "requirements.txt"))
    assert pip["cmd"][-2:] == ["-r", "requirements.txt"]
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert plan_install(str(tmp_path / "pyproject.toml"))["label"] == "pip install -e ."
    assert plan_install(str(tmp_path / "README.md")) is None


def test_background_install_surfaces_errors(tmp_path):
    (tmp_path / "package.json").write_text('{"name":"demo"}\n')
    seen = []
    sched = InstallScheduler(
        runner=lambda plan, timeout=180: (1, "npm ERR! missing module"),
        notify=seen.append,
    )
    started = sched.schedule(plan_install(str(tmp_path / "package.json")))
    assert "background" in started
    sched.wait(2)
    notes = sched.drain()
    assert notes and "auto-install failed" in notes[0]
    assert "npm ERR!" in notes[0]
    assert seen and "auto-install failed" in seen[0]
    again = sched.schedule(plan_install(str(tmp_path / "package.json")))
    assert "background" in again


def test_write_file_schedules_install(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hfagent.assist.deps.run_plan",
        lambda plan, timeout=180: (1, "pip ERR! no network"),
    )
    target = tmp_path / "requirements.txt"
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="write_file",
                            arguments=json.dumps({
                                "path": str(target),
                                "content": "requests==2.31.0\n",
                            }),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="wrote requirements"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "auto"
    ui.allow = True
    agent.run("add requests")
    assert target.read_text() == "requests==2.31.0\n"
    tool = next(m["content"] for m in agent.messages if m["role"] == "tool")
    assert "auto-install started" in tool
    agent._installs.wait(2)
    agent._drain_install_notes()
    joined = "\n".join(m.get("content") or "" for m in agent.messages)
    assert "auto-install failed" in joined
    assert "pip ERR!" in joined


def test_framework_detection_and_smoke_fallback(tmp_path):
    node = tmp_path / "node"
    node.mkdir()
    (node / "package.json").write_text(json.dumps({
        "scripts": {"test": "vitest", "build": "echo built"},
        "devDependencies": {"vitest": "1.0.0"},
    }))
    found = detect_frameworks(node)
    assert found[0]["name"] == "vitest"
    assert any(item["name"] == "npm-build" for item in smoke_plan(node))

    jest = tmp_path / "jest"
    jest.mkdir()
    (jest / "package.json").write_text(json.dumps({"devDependencies": {"jest": "29.0.0"}}))
    assert detect_frameworks(jest)[0]["name"] == "jest"
    mocha = tmp_path / "mocha"
    mocha.mkdir()
    (mocha / "package.json").write_text(json.dumps({
        "scripts": {"test": "mocha"},
        "devDependencies": {"mocha": "10.0.0"},
    }))
    assert detect_frameworks(mocha)[0]["name"] == "mocha"

    py = tmp_path / "py"
    py.mkdir()
    (py / "pyproject.toml").write_text("[tool.pytest.ini_options]\ntestpaths=['tests']\n")
    (py / "tests").mkdir()
    (py / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n")
    assert any(item["name"] == "pytest" for item in detect_frameworks(py))

    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "app.py").write_text("print('hi')\n")
    calls = []

    def runner(spec):
        calls.append(spec["name"])
        return 0, "ok"

    report = run_project_tests(str(bare), runner=runner)
    assert "No test framework" in report
    assert "py_compile" in calls
    assert "smoke test" in report
    scaffolded = run_project_tests(str(bare), scaffold=True, runner=runner)
    assert "wrote smoke scaffold" in scaffolded
    assert (bare / "tests" / "test_smoke.py").is_file()
    again = run_project_tests(str(bare), scaffold=True, runner=runner)
    assert "already exists" in again
    path, flag = parse_test_command("scaffold sub/app")
    assert flag is True and path == "sub/app"


def test_session_cache_invalidates_when_a_file_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("HFAGENT_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("HFAGENT_SESSION_CACHE", "1")
    root = tmp_path / "proj"
    root.mkdir()
    (root / "package.json").write_text('{"name":"p"}\n')
    listing = "f          package.json\n"
    remember("list_dir", str(root), listing, root=root)
    remember("read_file", str(root / "package.json"), '{"name":"p"}\n', root=root)
    assert "package.json" in lookup("list_dir", str(root))
    (root / "package.json").write_text('{"name":"changed"}\n')
    assert lookup("read_file", str(root / "package.json")) is None
    from hfagent.assist.cache import invalidate

    invalidate(str(root / "package.json"))
    assert lookup("list_dir", str(root)) is None
    assert "empty" in clear(root)


def test_audit_report_is_actionable_and_read_only(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    report, warnings = audit(empty)
    assert warnings >= 1
    assert "README" in report
    assert "Read-only" in report
    assert not (empty / "node_modules").exists()

    healthy = tmp_path / "healthy"
    (healthy / "tests").mkdir(parents=True)
    (healthy / ".github" / "workflows").mkdir(parents=True)
    (healthy / "README.md").write_text("# Demo\n")
    (healthy / "LICENSE").write_text("MIT\n")
    (healthy / "tests" / "test_ok.py").write_text("def test_ok():\n    assert 1\n")
    (healthy / "ruff.toml").write_text("line-length = 100\n")
    (healthy / ".github" / "workflows" / "ci.yml").write_text("name: ci\n")
    (healthy / ".gitignore").write_text(".venv\n")
    (healthy / "requirements.txt").write_text("flask==2.3.0\n")
    (healthy / "app.py").write_text("print('ok')\n")
    clean, count = audit(healthy)
    assert count == 0
    assert "No warnings" in clean


def test_audit_cli_exits_on_warnings(tmp_path, capsys):
    from hfagent.cli import _main

    empty = tmp_path / "empty"
    empty.mkdir()
    try:
        _main(["--audit", str(empty)])
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("expected exit 1")
    assert "IHAgent audit" in capsys.readouterr().out


def test_host_hint_uses_memory(monkeypatch, tmp_path):
    monkeypatch.setenv("HFAGENT_MEMORY_PATH", str(tmp_path / "mem"))
    from hfagent.memory import save_fact

    save_fact("kali219 is the user's Kali Linux pentest box")
    memory = "kali219 is the user's Kali Linux pentest box"
    assert "kali219" in known_hosts(memory)
    hint = suggestion("on kali219 run: ls /usr/share/wordlists", memory)
    assert hint is not None
    assert "ssh_run" in hint
    assert "kali219" in hint
    assert "ls /usr/share/wordlists" in hint
    assert suggestion("list the local files", memory) is None

    turns = [[Chunk(Delta(content="Hello. kali219 is a remembered host."))]]
    agent, ui = make_agent(tmp_path, turns)
    agent.run("check kali219")
    assert any("ssh_run" in text for text in ui.statuses)
    assert any("kali219" in (m.get("content") or "") for m in agent.messages if m["role"] == "user")


def test_style_audit_and_new_file_header(tmp_path, monkeypatch):
    html = "<html><body><p>Hi</p></body></html>\n"
    notes = style_notes("index.html", html)
    assert "font" in notes
    assert "background" in notes
    assert "transition" in notes
    good = (
        "@import url('https://fonts.googleapis.com/css2?family=Fraunces');\n"
        "body { font-family: Fraunces, serif; background: linear-gradient(#111, #333); }\n"
        "a { transition: color 150ms; }\n"
    )
    assert style_notes("site.css", good) == ""
    monkeypatch.setenv("HFAGENT_STYLE_AUDIT", "0")
    assert style_notes("index.html", html) == ""

    fresh, note = apply_header(str(tmp_path / "app.py"), "print('hi')\n", existed=False)
    assert note
    assert fresh.startswith("# app.py")
    assert '"""app.py.' in fresh
    again, note2 = apply_header(str(tmp_path / "app.py"), fresh, existed=True)
    assert note2 == "" and again == fresh

    target = tmp_path / "new.js"
    turns = [
        [
            Chunk(
                Delta(
                    tool_calls=[
                        ToolCallDelta(
                            0,
                            id="c1",
                            name="write_file",
                            arguments=json.dumps({
                                "path": str(target),
                                "content": "console.log(1)\n",
                            }),
                        )
                    ]
                )
            )
        ],
        [Chunk(Delta(content="created"))],
    ]
    agent, ui = make_agent(tmp_path, turns)
    agent.config.approval = "auto"
    ui.allow = True
    agent.run("create new.js")
    text = target.read_text()
    assert text.startswith("/* new.js")
    assert "console.log(1)" in text
    tool = next(m["content"] for m in agent.messages if m["role"] == "tool")
    assert "header comment" in tool


def test_security_checklist_applies_only_with_approval(tmp_path):
    app = tmp_path / "app.py"
    app.write_text(
        'DEBUG = True\npassword = "admin"\napi_key = "sk-abcdefghijklmnopqrst"\n'
    )
    page = tmp_path / "login.html"
    page.write_text('<form method="post" action="/login"><input name="q"></form>\n')
    findings = scan_text(str(app), app.read_text()) + scan_text(str(page), page.read_text())
    kinds = {item["kind"] for item in findings}
    assert {"debug", "default_creds", "secret", "csrf"} <= kinds
    shown = "\n".join(item["detail"] for item in findings)
    assert "admin" not in shown
    assert "sk-abcdefghijklmnopqrst" not in shown

    declined = apply_with_approval(tmp_path, lambda *args: False)
    assert "declined" in declined
    assert "DEBUG = True" in app.read_text()

    debug = next(item for item in findings if item["kind"] == "debug")
    assert apply_finding(debug).startswith("updated")
    assert "DEBUG = False" in app.read_text()

    def approve(name, args, preview=None):
        return args.get("kind") == "default_creds"

    applied = apply_with_approval(tmp_path, approve)
    assert "updated" in applied
    assert 'os.environ["APP_SECRET"]' in app.read_text()
    assert "import os" in app.read_text()


def test_slash_commands_are_documented_and_dispatch(tmp_path):
    assert "/audit" in HELP
    assert "/test" in HELP
    assert "/output" in HELP
    assert "/cache" in HELP
    assert "/security" in HELP
    from hfagent.agent import Agent
    from hfagent.config import Config
    from hfagent.tools import default_registry

    agent = Agent(
        Config(api_key="none", base_url="http://127.0.0.1:9/v1", model="test"),
        default_registry(),
        Recorder(),
    )
    ui = Recorder()
    assert dispatch_slash(agent, ui, f"/audit {tmp_path}") == "handled"
    assert "IHAgent audit" in ui.system[-1]
    assert dispatch_slash(agent, ui, "/output") == "handled"
    assert "no saved" in ui.system[-1]
