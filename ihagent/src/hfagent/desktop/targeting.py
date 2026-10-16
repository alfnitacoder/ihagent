"""Pick a pointer target: element id, then a name, then the vision model."""

from __future__ import annotations

from dataclasses import dataclass

from .elements import UiElement, match_elements


@dataclass(frozen=True)
class Aim:
    x: float
    y: float
    source: str
    label: str
    element: UiElement | None = None


@dataclass(frozen=True)
class NeedVision:
    """The tree was empty or had no match for this description."""

    description: str


def coerce_element(value: object) -> int | None:
    """Accept ``3``, ``"3"``, and ``"e3"``. Blank means not provided."""
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("e") and text[1:].isdigit():
        text = text[1:]
    try:
        return int(float(text))
    except ValueError:
        return None


def has_target(element: int | None, target: str, x: object, y: object) -> bool:
    return element is not None or bool(target) or (x is not None and y is not None)


def resolve_aim(
    elements: list[UiElement],
    *,
    element: int | None,
    target: str,
    x: object,
    y: object,
) -> Aim | NeedVision | str:
    """An aim, a vision request, or an ``Error:`` string.

    One exact or unique name match clicks that element's centre. Several
    matches ask for an id. No match, or an empty tree, asks for a screenshot.
    """
    if element is not None:
        found = next((item for item in elements if item.id == element), None)
        if found is None:
            return (
                f"Error: element {element} is not in the current list. "
                "Call mouse_elements again."
            )
        cx, cy = found.center()
        return Aim(cx, cy, "accessibility", found.label(), found)
    if x is not None and y is not None:
        try:
            px = float(x)
            py = float(y)
        except (TypeError, ValueError):
            return "Error: x and y must be numbers."
        return Aim(px, py, "coordinates", f"({px}, {py})", None)
    query = (target or "").strip()
    if not query:
        return "Error: pass element (an id from mouse_elements), target (a description), or x and y."
    matches = match_elements(elements, query)
    if len(matches) == 1:
        found = matches[0]
        cx, cy = found.center()
        return Aim(cx, cy, "accessibility", found.label(), found)
    if len(matches) > 1:
        listed = ", ".join(f'{item.id} {item.role} "{item.name}"' for item in matches[:8])
        return (
            f"Error: '{query}' matches more than one element. "
            f"Pick an id from mouse_elements: {listed}"
        )
    return NeedVision(query)
