"""Exit rules for the helper so it cannot outlive ihagent."""

from __future__ import annotations

import os
import select
import threading
from collections.abc import Callable


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def helper_should_exit(*, quit_requested: bool, parent_alive: bool) -> bool:
    """The overlay process ends when ihagent asks it to, or when ihagent is gone."""
    return quit_requested or not parent_alive


def watch_parent_exit(parent_pid: int, on_exit: Callable[[], None]) -> None:
    """Block until ``parent_pid`` exits, then call ``on_exit``.

    Uses kqueue on macOS. Where kqueue is missing this returns immediately and
    the helper's timer poll is the backup.
    """
    if parent_pid <= 0 or not hasattr(select, "kqueue"):
        return
    try:
        kq = select.kqueue()
        event = select.kevent(
            parent_pid,
            select.KQ_FILTER_PROC,
            select.KQ_EV_ADD,
            select.KQ_NOTE_EXIT,
        )
        kq.control([event], 0)
        while True:
            got = kq.control(None, 1, 1.0)
            if got:
                on_exit()
                return
    except Exception:
        return


def watch_parent_exit_async(parent_pid: int, on_exit: Callable[[], None]) -> threading.Thread | None:
    if parent_pid <= 0 or not hasattr(select, "kqueue"):
        return None
    thread = threading.Thread(
        target=watch_parent_exit,
        args=(parent_pid, on_exit),
        name="iheye-parent",
        daemon=True,
    )
    thread.start()
    return thread
