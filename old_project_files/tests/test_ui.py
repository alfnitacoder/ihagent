"""The UI must never crash on bracket-laden dynamic text (rich markup)."""

from hfagent.ui.console import ConsoleUI

HOSTILE = "closing tag '[/(/g; s/\\]' at position 33 doesn't match any open tag"


def test_on_error_survives_bracket_bombs(capsys):
    ui = ConsoleUI()
    ui.on_error(HOSTILE)  # must not raise
    captured = capsys.readouterr()
    assert "error:" in captured.out + captured.err


def test_on_status_survives_bracket_bombs():
    ui = ConsoleUI()
    ui.on_status("nudging [auto] it [/done]")


def test_panels_survive_bracket_bombs():
    ui = ConsoleUI()
    ui.on_action("run_command", {"command": "sed 's/a[/b/g' file"})
    ui.on_observation("ssh_run", "[WARNING] x [/y] [ERROR] z")


def test_assistant_stream_survives_bracket_bombs():
    ui = ConsoleUI()
    ui.on_assistant_delta("[/x] [y] text [1m")
    ui.on_assistant_done("")
