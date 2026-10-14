"""Which terminal app is hosting this ihagent process.

The helper compares the frontmost macOS app to this record. Parent-process
walk is preferred (it carries a pid). ``TERM_PROGRAM`` and a few well-known
environment variables cover terminals that do not stay in the process tree.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass(frozen=True)
class TerminalSpec:
    kind: str
    app_name: str
    bundle_id: str


@dataclass(frozen=True)
class ProcessRow:
    ppid: int
    comm: str
    command: str


@dataclass(frozen=True)
class HostInfo:
    kind: str
    app_name: str
    bundle_id: str
    pid: int | None
    ancestor_pids: tuple[int, ...]
    tty: str
    source: str

    def window_level(self) -> bool:
        """Terminal.app and iTerm2 can name the focused tty. Other apps cannot."""
        return self.kind in {"terminal", "iterm2"} and bool(self.tty)


TERMINAL = TerminalSpec("terminal", "Terminal", "com.apple.Terminal")
ITERM = TerminalSpec("iterm2", "iTerm2", "com.googlecode.iterm2")
WARP = TerminalSpec("warp", "Warp", "dev.warp.Warp-Stable")
VSCODE = TerminalSpec("vscode", "Code", "com.microsoft.VSCode")
CURSOR = TerminalSpec("cursor", "Cursor", "com.todesktop.230313mzl4w4u92")
GHOSTTY = TerminalSpec("ghostty", "ghostty", "com.mitchellh.ghostty")
KITTY = TerminalSpec("kitty", "kitty", "net.kovidgoyal.kitty")
WEZTERM = TerminalSpec("wezterm", "WezTerm", "com.github.wez.wezterm")
ALACRITTY = TerminalSpec("alacritty", "Alacritty", "org.alacritty")
HYPER = TerminalSpec("hyper", "Hyper", "co.zeit.hyper")

# Longer markers first so "Cursor.app" wins over a generic "Code" match.
_APP_MARKERS: tuple[tuple[str, TerminalSpec], ...] = (
    ("cursor.app", CURSOR),
    ("warp.app", WARP),
    ("iterm.app", ITERM),
    ("visual studio code.app", VSCODE),
    ("terminal.app", TERMINAL),
    ("ghostty.app", GHOSTTY),
    ("kitty.app", KITTY),
    ("wezterm.app", WEZTERM),
    ("alacritty.app", ALACRITTY),
    ("hyper.app", HYPER),
)

_EXACT_BASENAMES = {
    "terminal": TERMINAL,
    "iterm2": ITERM,
    "iterm": ITERM,
    "warp": WARP,
    "code": VSCODE,
    "cursor": CURSOR,
    "ghostty": GHOSTTY,
    "kitty": KITTY,
    "wezterm": WEZTERM,
    "wezterm-gui": WEZTERM,
    "alacritty": ALACRITTY,
    "hyper": HYPER,
}

_TERM_PROGRAM = {
    "apple_terminal": TERMINAL,
    "iterm.app": ITERM,
    "warpterminal": WARP,
    "vscode": VSCODE,
    "cursor": CURSOR,
    "ghostty": GHOSTTY,
    "kitty": KITTY,
    "wezterm": WEZTERM,
    "alacritty": ALACRITTY,
    "hyper": HYPER,
}

_SHELLS = {
    "zsh",
    "bash",
    "sh",
    "fish",
    "dash",
    "ksh",
    "tcsh",
    "login",
    "tmux",
    "screen",
    "sudo",
    "su",
    "env",
    "python",
    "python3",
}

_IGNORED = {"launchd", "systemd", "init", "kernel_task"}

WINDOW_LEVEL_KINDS = frozenset({"terminal", "iterm2"})


def current_tty() -> str:
    try:
        if not os.isatty(0):
            return ""
        return os.ttyname(0)
    except OSError:
        return ""


def parse_ps_line(line: str) -> ProcessRow | None:
    """Parse one ``ps -o ppid=,comm=,args=`` line."""
    parts = line.strip().split(None, 2)
    if len(parts) < 2:
        return None
    try:
        ppid = int(parts[0])
    except ValueError:
        return None
    comm = parts[1]
    command = parts[2] if len(parts) > 2 else comm
    return ProcessRow(ppid=ppid, comm=comm, command=command)


def ps_lookup(pid: int) -> ProcessRow | None:
    if pid <= 0:
        return None
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-ww", "-o", "ppid=,comm=,args="],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=2,
        )
    except (subprocess.SubprocessError, OSError, ValueError):
        return None
    line = out.strip().splitlines()
    if not line:
        return None
    return parse_ps_line(line[0])


def match_process(comm: str, command: str) -> TerminalSpec | None:
    blob = f"{comm} {command}".lower()
    for marker, spec in _APP_MARKERS:
        if marker in blob:
            return spec
    for name in (os.path.basename(comm), _exe_basename(command)):
        spec = _EXACT_BASENAMES.get(name.lower())
        if spec is not None:
            return spec
    return None


def _exe_basename(command: str) -> str:
    token = command.strip().split(" ", 1)[0] if command.strip() else ""
    return os.path.basename(token)


def _is_shell(comm: str) -> bool:
    base = os.path.basename(comm).lower()
    return base in _SHELLS or base.startswith("python")


def _is_ignored(comm: str) -> bool:
    base = os.path.basename(comm).lower()
    return base in _IGNORED or _is_shell(comm)


def _cursor_env(environ: dict[str, str]) -> bool:
    if environ.get("CURSOR_TRACE_ID"):
        return True
    blob = " ".join(
        environ.get(name, "")
        for name in (
            "VSCODE_GIT_ASKPASS_MAIN",
            "VSCODE_GIT_ASKPASS_NODE",
            "PATH",
            "TERM_PROGRAM",
        )
    )
    return "Cursor.app" in blob or "cursor" == environ.get("TERM_PROGRAM", "").lower()


def spec_from_environ(environ: dict[str, str]) -> TerminalSpec | None:
    if environ.get("ITERM_SESSION_ID"):
        return ITERM
    if environ.get("WARP_HONOR_PS1") or environ.get("WARP_IS_LOCAL_SHELL_SESSION"):
        return WARP
    if environ.get("GHOSTTY_RESOURCES_DIR") or environ.get("GHOSTTY_BIN_DIR"):
        return GHOSTTY
    if environ.get("KITTY_WINDOW_ID") or environ.get("KITTY_PID"):
        return KITTY
    if environ.get("WEZTERM_PANE_ID") or environ.get("WEZTERM_EXECUTABLE"):
        return WEZTERM
    program = environ.get("TERM_PROGRAM", "").strip().lower()
    spec = _TERM_PROGRAM.get(program)
    if spec is VSCODE and _cursor_env(environ):
        return CURSOR
    if spec is not None:
        return spec
    if _cursor_env(environ):
        return CURSOR
    return None


def _walk(
    start_pid: int,
    lookup,
) -> list[tuple[int, str, str]]:
    rows: list[tuple[int, str, str]] = []
    seen: set[int] = set()
    current = start_pid
    while current > 1 and current not in seen and len(rows) < 32:
        seen.add(current)
        row = lookup(current)
        if row is None:
            break
        rows.append((current, row.comm, row.command))
        if row.ppid <= 1:
            break
        current = row.ppid
    return rows


def detect_host(
    *,
    pid: int | None = None,
    environ: dict[str, str] | None = None,
    tty: str | None = None,
    lookup=None,
) -> HostInfo:
    """Describe the GUI app that owns this terminal session."""
    env = os.environ if environ is None else environ
    if tty is None:
        tty = current_tty()
    start = os.getppid() if pid is None else pid
    walker = ps_lookup if lookup is None else lookup
    rows = _walk(start, walker) if start > 1 else []
    ancestors = tuple(item[0] for item in rows)

    matched: tuple[int, TerminalSpec] | None = None
    for proc_pid, comm, command in rows:
        spec = match_process(comm, command)
        if spec is not None:
            # Keep walking so the outermost Terminal/Code/Cursor wins over a helper.
            matched = (proc_pid, spec)

    if matched is not None:
        host_pid, spec = matched
        # Prefer the real app pid over "Code Helper" / "Cursor Helper".
        for proc_pid, comm, command in rows:
            spec_here = match_process(comm, command)
            if spec_here is None or spec_here.kind != spec.kind:
                continue
            if "helper" not in f"{comm} {command}".lower():
                host_pid = proc_pid
        return HostInfo(
            kind=spec.kind,
            app_name=spec.app_name,
            bundle_id=spec.bundle_id,
            pid=host_pid,
            ancestor_pids=ancestors,
            tty=tty,
            source="parent",
        )

    env_spec = spec_from_environ(env)
    if env_spec is not None:
        return HostInfo(
            kind=env_spec.kind,
            app_name=env_spec.app_name,
            bundle_id=env_spec.bundle_id,
            pid=None,
            ancestor_pids=ancestors,
            tty=tty,
            source="environ",
        )

    fallback_pid: int | None = None
    fallback_name = ""
    for proc_pid, comm, _command in rows:
        if _is_ignored(comm):
            continue
        fallback_pid = proc_pid
        fallback_name = os.path.basename(comm) or comm
    return HostInfo(
        kind="unknown",
        app_name=fallback_name,
        bundle_id="",
        pid=fallback_pid,
        ancestor_pids=ancestors,
        tty=tty,
        source="ancestor" if fallback_pid else "unknown",
    )


def front_tty_script(kind: str) -> str | None:
    """AppleScript that prints the tty of the focused terminal window, if we can."""
    if kind == "terminal":
        return (
            'tell application "Terminal"\n'
            "  if (count of windows) is 0 then return \"\"\n"
            "  try\n"
            "    return tty of selected tab of front window\n"
            "  on error\n"
            '    return ""\n'
            "  end try\n"
            "end tell"
        )
    if kind == "iterm2":
        return (
            'tell application "iTerm2"\n'
            "  if (count of windows) is 0 then return \"\"\n"
            "  try\n"
            "    return tty of current session of current window\n"
            "  on error\n"
            '    return ""\n'
            "  end try\n"
            "end tell"
        )
    return None


def _applescript_string(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def focus_script(kind: str, tty: str) -> str | None:
    """AppleScript that brings the ihagent window/tab forward, if we can name it."""
    if not tty or kind not in WINDOW_LEVEL_KINDS:
        return None
    quoted = _applescript_string(tty)
    if kind == "terminal":
        return (
            'tell application "Terminal"\n'
            "  repeat with w in windows\n"
            "    repeat with t in tabs of w\n"
            "      try\n"
            f'        if tty of t is "{quoted}" then\n'
            "          set index of w to 1\n"
            "          set selected tab of w to t\n"
            "          activate\n"
            '          return "ok"\n'
            "        end if\n"
            "      end try\n"
            "    end repeat\n"
            "  end repeat\n"
            "  activate\n"
            '  return "app"\n'
            "end tell"
        )
    return (
        'tell application "iTerm2"\n'
        "  repeat with w in windows\n"
        "    repeat with t in tabs of w\n"
        "      repeat with s in sessions of t\n"
        "        try\n"
        f'          if tty of s is "{quoted}" then\n'
        "            select w\n"
        "            select t\n"
        "            select s\n"
        "            activate\n"
        '            return "ok"\n'
        "          end if\n"
        "        end try\n"
        "      end repeat\n"
        "    end repeat\n"
        "  end repeat\n"
        "  activate\n"
        '  return "app"\n'
        "end tell"
    )


def run_osascript(script: str, timeout: float = 1.0, runner=None) -> str:
    """Run AppleScript and return stdout. Failures become an empty string."""
    if runner is not None:
        try:
            return str(runner(script) or "").strip()
        except Exception:
            return ""
    try:
        out = subprocess.check_output(
            ["osascript", "-e", script],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return out.strip()


def _norm_tty(value: str) -> str:
    """Terminal.app sometimes returns ``ttys003`` and sometimes ``/dev/ttys003``."""
    text = value.strip()
    if text.startswith("/dev/"):
        return text
    if text.startswith("dev/"):
        return "/" + text
    if text.startswith("tty") or text.startswith("pts"):
        return "/dev/" + text
    return text


def same_tty(left: str, right: str) -> bool:
    if not left or not right:
        return False
    left = _norm_tty(left)
    right = _norm_tty(right)
    if left == right:
        return True
    try:
        return os.path.realpath(left) == os.path.realpath(right)
    except OSError:
        return False
