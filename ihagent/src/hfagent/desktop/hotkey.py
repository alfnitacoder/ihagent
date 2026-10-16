"""Emergency-stop chord. The listener starts only while mouse control is on.

It watches the keyboard for one chord. It does not read other keys into
the log and it does not look at the screen.
"""

from __future__ import annotations

from .session import get_session


_listener = None


def parse_hotkey(spec: str) -> set[str]:
    aliases = {
        "control": "ctrl",
        "option": "alt",
        "cmd": "command",
        "meta": "command",
        "escape": "esc",
        "win": "win",
    }
    parts: set[str] = set()
    for raw in (spec or "").split("+"):
        name = raw.strip().lower()
        if not name:
            continue
        parts.add(aliases.get(name, name))
    return parts


def chord_down(wanted: set[str], pressed: set[str]) -> bool:
    return bool(wanted) and wanted <= pressed


def normalize_key_name(name: str) -> str:
    text = (name or "").strip().lower()
    if text.endswith("_l") or text.endswith("_r"):
        base = text[:-2]
        if base in {"ctrl", "alt", "shift", "cmd"}:
            return "command" if base == "cmd" else base
    if text in {"escape"}:
        return "esc"
    if text in {"cmd", "cmd_l", "cmd_r"}:
        return "command"
    return text


def start_hotkey(spec: str | None = None) -> str:
    """Start the listener. The return value is empty on success, or a note."""
    global _listener
    stop_hotkey()
    chord = parse_hotkey(spec or get_session().hotkey_spec())
    if not chord:
        return "stop hotkey is empty; move the pointer to a screen corner to stop"
    try:
        from pynput import keyboard
    except ImportError:
        return (
            "hotkey listener unavailable (pip install 'ihagent[desktop]'); "
            "move the pointer to a screen corner to stop"
        )
    pressed: set[str] = set()

    def on_press(key) -> None:
        pressed.add(_from_pynput(key))
        if chord_down(chord, pressed):
            get_session().trip(f"hotkey {spec or get_session().hotkey_spec()}")
            try:
                from .overlay_host import hide_overlay

                hide_overlay()
            except Exception:
                pass

    def on_release(key) -> None:
        pressed.discard(_from_pynput(key))

    try:
        listener = keyboard.Listener(on_press=on_press, on_release=on_release)
        listener.daemon = True
        listener.start()
    except Exception as exc:
        return (
            f"hotkey listener did not start ({exc}); "
            "move the pointer to a screen corner to stop"
        )
    _listener = listener
    return ""


def stop_hotkey() -> None:
    global _listener
    listener = _listener
    _listener = None
    if listener is None:
        return
    try:
        listener.stop()
    except Exception:
        pass


def _from_pynput(key) -> str:
    char = getattr(key, "char", None)
    if char:
        return str(char).lower()
    name = getattr(key, "name", None) or ""
    return normalize_key_name(str(name))
