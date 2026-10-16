"""Compact accessibility elements for the text model.

Roles are normalised across macOS AX, Windows UI Automation, and Linux
AT-SPI. Bounds stay in pointer space (see :mod:`hfagent.desktop.geometry`).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable

from .geometry import Rect


KEEP_ROLES = {
    "button",
    "checkbox",
    "radio",
    "textfield",
    "textarea",
    "securetextfield",
    "searchfield",
    "link",
    "menuitem",
    "menu",
    "tab",
    "combobox",
    "slider",
    "listitem",
    "cell",
    "image",
    "statictext",
    "toggle",
    "switch",
}

_ENTRY_ROLES = {
    "textfield",
    "textarea",
    "securetextfield",
    "searchfield",
}

_ROLE_ALIASES = {
    "radiobutton": "radio",
    "popupbutton": "combobox",
    "passwordtext": "securetextfield",
    "password": "securetextfield",
    "entry": "textfield",
    "edit": "textfield",
    "document": "textarea",
    "hyperlink": "link",
    "tabitem": "tab",
    "checkbutton": "checkbox",
    "togglebutton": "toggle",
    "pushbutton": "button",
    "icon": "image",
    "text": "statictext",
}

_PASSWORD_NAME = re.compile(r"\b(password|passwd|passcode|pin)\b", re.IGNORECASE)

_LEAVE_KEYS = {
    ("esc",),
    ("escape",),
    ("tab",),
    ("shift", "tab"),
}


@dataclass(frozen=True)
class UiElement:
    id: int
    role: str
    name: str
    x: float
    y: float
    width: float
    height: float
    password: bool = False

    def center(self) -> tuple[float, float]:
        return (self.x + self.width / 2.0, self.y + self.height / 2.0)

    def label(self) -> str:
        name = self.name.strip()
        if name:
            return f'{self.role} "{name}"'
        return self.role


@dataclass
class TreeSnapshot:
    """One read of the focused app. ``error`` is already a user-facing sentence."""

    app: str = ""
    elements: list[UiElement] = field(default_factory=list)
    window: Rect | None = None
    focused: UiElement | None = None
    error: str = ""


def normalize_role(role: str) -> str:
    """``AXButton``, ``Button``, and ``push button`` all become ``button``."""
    text = (role or "").strip().lower().replace(" ", "").replace("_", "").replace("-", "")
    if text.startswith("ax"):
        text = text[2:]
    return _ROLE_ALIASES.get(text, text)


def is_password(role: str, name: str, flagged: bool = False) -> bool:
    """True for secure fields and for text entries whose name says password.

    A button named "Change password" is not a password field.
    """
    if flagged:
        return True
    normal = normalize_role(role)
    if normal == "securetextfield":
        return True
    if normal in _ENTRY_ROLES and _PASSWORD_NAME.search(name or ""):
        return True
    return False


def elements_from_raw(rows: list[dict[str, Any]], *, limit: int = 60) -> list[UiElement]:
    """Keep interactive, named rows and number them from 1."""
    kept: list[UiElement] = []
    for row in rows:
        if len(kept) >= limit:
            break
        role = normalize_role(str(row.get("role") or ""))
        name = str(row.get("name") or "").replace("\n", " ").strip()
        if role not in KEEP_ROLES:
            continue
        if role in {"statictext", "image"} and not name:
            continue
        try:
            x = float(row["x"])
            y = float(row["y"])
            width = float(row["width"])
            height = float(row["height"])
        except (KeyError, TypeError, ValueError):
            continue
        if width <= 0 or height <= 0:
            continue
        password = is_password(role, name, bool(row.get("password")))
        kept.append(
            UiElement(
                id=len(kept) + 1,
                role=role,
                name=name,
                x=x,
                y=y,
                width=width,
                height=height,
                password=password,
            )
        )
    return kept


def focused_from_raw(raw: dict[str, Any] | None) -> UiElement | None:
    """The focused control, even when it has no size yet (still refuse passwords)."""
    if not raw:
        return None
    built = elements_from_raw([raw])
    if built:
        return built[0]
    role = normalize_role(str(raw.get("role") or ""))
    name = str(raw.get("name") or "").strip()
    if is_password(role, name, bool(raw.get("password"))):
        return UiElement(0, role or "securetextfield", name, 0, 0, 0, 0, password=True)
    return None


def walk_nodes(
    root: Any,
    children_of: Callable[[Any], list[Any] | None],
    attrs_of: Callable[[Any], dict[str, Any] | None],
    *,
    limit: int = 400,
    max_depth: int = 8,
) -> list[dict[str, Any]]:
    """Depth-first walk. Platform backends supply the two callbacks."""
    rows: list[dict[str, Any]] = []

    def visit(node: Any, depth: int) -> None:
        if node is None or depth > max_depth or len(rows) >= limit:
            return
        raw = attrs_of(node)
        if raw:
            rows.append(raw)
        try:
            children = list(children_of(node) or [])
        except Exception:
            children = []
        for child in children:
            if len(rows) >= limit:
                return
            visit(child, depth + 1)

    visit(root, 0)
    return rows


def format_elements(elements: list[UiElement], app: str = "") -> str:
    """The numbered list the text model picks from."""
    if not elements:
        lines = ["No accessibility elements."]
        if app:
            lines.insert(0, f"Focused app: {app}")
        return "\n".join(lines)
    lines: list[str] = []
    if app:
        lines.append(f"Focused app: {app}")
    for element in elements:
        name = element.name.strip()
        if len(name) > 80:
            name = name[:79].rstrip() + "…"
        secret = " password" if element.password else ""
        bounds = (
            f"bounds=({_num(element.x)},{_num(element.y)},"
            f"{_num(element.width)},{_num(element.height)})"
        )
        lines.append(f'{element.id}. {element.role} "{name}"{secret} {bounds}')
    return "\n".join(lines)


def match_elements(elements: list[UiElement], query: str) -> list[UiElement]:
    """Exact name first, otherwise a case-insensitive substring."""
    needle = (query or "").strip().lower()
    if not needle:
        return []
    exact = [element for element in elements if element.name.strip().lower() == needle]
    if exact:
        return exact
    return [element for element in elements if needle in element.name.strip().lower()]


def tree_is_useful(elements: list[UiElement]) -> bool:
    """A tree with no names cannot be clicked by description."""
    return any(element.name.strip() for element in elements)


def key_is_navigation(spec: str) -> bool:
    """Tab and Escape leave a field. They are not typing into it."""
    parts = tuple(part.strip().lower() for part in (spec or "").split("+") if part.strip())
    return parts in _LEAVE_KEYS


def _num(value: float) -> str:
    rounded = round(value)
    if abs(value - rounded) < 0.05:
        return str(int(rounded))
    return f"{value:.1f}"
