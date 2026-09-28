"""CLI flags and TUI-vs-console selection."""

from hfagent.cli import build_parser, use_tui


def test_console_and_tui_flags():
    parser = build_parser()
    assert parser.parse_args(["--console"]).console is True
    assert parser.parse_args(["--tui"]).tui is True
    assert parser.parse_args([]).console is False
    assert parser.parse_args([]).tui is False


def test_console_and_tui_are_exclusive():
    parser = build_parser()
    try:
        parser.parse_args(["--console", "--tui"])
    except SystemExit:
        return
    raise AssertionError("expected argparse to reject --console --tui")


def test_use_tui_defaults_on_tty():
    args = build_parser().parse_args([])
    assert use_tui(args, stdin_tty=True, stdout_tty=True) is True
    assert use_tui(args, stdin_tty=False, stdout_tty=True) is False


def test_use_tui_prompt_and_console_disable():
    parser = build_parser()
    assert use_tui(parser.parse_args(["-p", "hi"]), stdin_tty=True, stdout_tty=True) is False
    assert use_tui(parser.parse_args(["--console"]), stdin_tty=True, stdout_tty=True) is False
    assert use_tui(parser.parse_args(["--tui"]), stdin_tty=False, stdout_tty=False) is True
