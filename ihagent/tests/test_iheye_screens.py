"""Companion cards stay on the display that holds the bubble."""

from pathlib import Path

from hfagent.iheye.position import (
    Display,
    SavedPlace,
    ScreenFrame,
    display_for_rect,
    drop_origin,
    load_placement,
    restore_bubble,
    save_position,
)

# Menu-bar display, a display to its left, and one below. Origins are global.
MAIN = Display(ScreenFrame(0, 0, 1440, 900), ScreenFrame(0, 25, 1440, 850))
LEFT = Display(ScreenFrame(-1920, 0, 1920, 1080), ScreenFrame(-1920, 0, 1920, 1040))
BELOW = Display(ScreenFrame(0, -1200, 1920, 1080), ScreenFrame(0, -1200, 1920, 1040))

EYE_W = 168.0
EYE_H = 220.0
CARD_W = 280.0
CARD_H = 176.0


def _inside(x, y, w, h, screen: ScreenFrame) -> bool:
    return (
        x >= screen.x - 0.1
        and y >= screen.y - 0.1
        and x + w <= screen.x + screen.w + 0.1
        and y + h <= screen.y + screen.h + 0.1
    )


def test_card_stays_on_a_display_with_a_negative_origin():
    eye_x, eye_y = -1700.0, 640.0
    x, y = drop_origin(
        eye_x, eye_y, EYE_W, EYE_H, CARD_W, CARD_H,
        LEFT.visible.x, LEFT.visible.y, LEFT.visible.w, LEFT.visible.h,
    )
    assert x < 0
    assert _inside(x, y, CARD_W, CARD_H, LEFT.visible)
    assert y < eye_y
    # Clamping that same card to the main display is the bug: it jumps there.
    jumped = drop_origin(
        eye_x, eye_y, EYE_W, EYE_H, CARD_W, CARD_H,
        MAIN.visible.x, MAIN.visible.y, MAIN.visible.w, MAIN.visible.h,
    )
    assert jumped[0] >= 0


def test_card_flips_above_and_left_without_leaving_its_display():
    # Near the bottom of the left display: the card goes above the bubble.
    above = drop_origin(
        -1600, 8, EYE_W, EYE_H, CARD_W, CARD_H,
        LEFT.visible.x, LEFT.visible.y, LEFT.visible.w, LEFT.visible.h,
    )
    assert above[1] >= 8 + EYE_H
    assert _inside(*above, CARD_W, CARD_H, LEFT.visible)

    # Near the right edge: the card shifts left instead of crossing onto main.
    leftward = drop_origin(
        -180, 500, EYE_W, EYE_H, CARD_W, CARD_H,
        LEFT.visible.x, LEFT.visible.y, LEFT.visible.w, LEFT.visible.h,
    )
    assert leftward[0] + CARD_W <= LEFT.visible.x + LEFT.visible.w
    assert leftward[0] < -180
    assert _inside(*leftward, CARD_W, CARD_H, LEFT.visible)

    # Near the left edge: the card shifts right and still starts on this display.
    rightward = drop_origin(
        LEFT.visible.x + 4, 500, EYE_W, EYE_H, CARD_W, CARD_H,
        LEFT.visible.x, LEFT.visible.y, LEFT.visible.w, LEFT.visible.h,
    )
    assert rightward[0] >= LEFT.visible.x
    assert _inside(*rightward, CARD_W, CARD_H, LEFT.visible)


def test_card_on_a_display_below_stays_below():
    eye_y = -1100.0
    x, y = drop_origin(
        400, eye_y, EYE_W, EYE_H, CARD_W, CARD_H,
        BELOW.visible.x, BELOW.visible.y, BELOW.visible.w, BELOW.visible.h,
    )
    assert y + CARD_H <= 0
    assert _inside(x, y, CARD_W, CARD_H, BELOW.visible)
    # Too close to the bottom of that display: flip up, still not onto main.
    flipped = drop_origin(
        400, BELOW.visible.y + 4, EYE_W, EYE_H, CARD_W, CARD_H,
        BELOW.visible.x, BELOW.visible.y, BELOW.visible.w, BELOW.visible.h,
    )
    assert flipped[1] > BELOW.visible.y + 4
    assert flipped[1] + CARD_H <= 0 or flipped[1] < 0
    assert _inside(*flipped, CARD_W, CARD_H, BELOW.visible)


def test_window_center_picks_the_display_across_a_shared_edge():
    displays = [MAIN, LEFT, BELOW]
    # Center is on the left display even though the rect crosses x=0.
    assert display_for_rect(displays, -100, 200, EYE_W, EYE_H) is LEFT
    # Center is on the main display.
    assert display_for_rect(displays, -20, 200, EYE_W, EYE_H) is MAIN
    # Center is on the display below.
    assert display_for_rect(displays, 100, -400, EYE_W, EYE_H) is BELOW


def test_saved_bubble_returns_to_its_display(tmp_path):
    path = tmp_path / "iheye-position.json"
    save_position(path, -1700, 800, LEFT.frame)
    saved = load_placement(path)
    assert saved is not None
    assert saved.screen is not None
    assert saved.screen.x == -1920
    placed = restore_bubble(saved, [MAIN, LEFT], mouse=(100, 100), main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.x < 0
    assert _inside(placed.x, placed.y, EYE_W, EYE_H, LEFT.visible)
    assert placed.visible.x == LEFT.visible.x


def test_legacy_point_on_a_side_display_is_kept():
    saved = SavedPlace(-1500, 400, screen=None)
    placed = restore_bubble(saved, [MAIN, LEFT, BELOW], mouse=(10, 10), main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.x < 0
    assert _inside(placed.x, placed.y, EYE_W, EYE_H, LEFT.visible)


def test_missing_display_falls_back_to_the_pointer_then_main():
    saved = SavedPlace(-1700, 800, screen=LEFT.frame)
    # Pointer is on the display below; the left display is unplugged.
    placed = restore_bubble(saved, [MAIN, BELOW], mouse=(200, -600), main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.y < 0
    assert _inside(placed.x, placed.y, EYE_W, EYE_H, BELOW.visible)
    # No pointer hit: the main display's top-right, not the old negative origin.
    placed = restore_bubble(saved, [MAIN], mouse=None, main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.x > 1000
    assert placed.y > 0
    assert _inside(placed.x, placed.y, EYE_W, EYE_H, MAIN.visible)


def test_first_launch_uses_the_main_display_not_the_pointer():
    placed = restore_bubble(None, [MAIN, LEFT], mouse=(-100, 100), main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.x > 0
    assert _inside(placed.x, placed.y, EYE_W, EYE_H, MAIN.visible)


def test_saved_point_under_the_menu_bar_is_pulled_into_the_visible_frame():
    saved = SavedPlace(1200, 880, screen=MAIN.frame)
    placed = restore_bubble(saved, [MAIN, LEFT], mouse=None, main=MAIN, panel_w=EYE_W, panel_h=EYE_H)
    assert placed.y + EYE_H <= MAIN.visible.y + MAIN.visible.h
    assert placed.y >= MAIN.visible.y


def test_overlay_places_the_card_from_the_bubble_window():
    overlay = Path(__file__).resolve().parents[1] / "src" / "hfagent" / "iheye" / "overlay_app.py"
    source = overlay.read_text(encoding="utf-8")
    place = source.split("def _place_drop", 1)[1].split("\n        def ", 1)[0]
    assert "bubble_display(self.panel)" in place
    assert "NSScreen.mainScreen" not in place
    assert "screens()[0]" not in source
    assert "_screen_rect" not in source
