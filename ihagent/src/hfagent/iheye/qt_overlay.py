"""Windows and Linux overlay: a frameless, always-on-top iheye bubble.

PySide6 is imported inside ``run_overlay``. The macOS helper never loads
this module. The socket protocol, skins, and card placement match the
AppKit overlay. A screenshot of another window runs only when a hover
message asks for one.
"""

from __future__ import annotations

import os
import queue
import sys
import tempfile
import time

from .appearance import pupil_offset
from .backend import INSTALL_QT_HINT
from .bot import point_in_bot, reduce_motion_from_env
from .face import point_in_face
from .foreground import (
    HOTKEY_LABEL,
    ancestor_pids,
    eye_visible,
    foreground_is_terminal,
    interpret_linux_focus,
    linux_active_pid,
    linux_parent_of,
    linux_session_mode,
    read_windows_foreground,
    terminal_pid_set,
    toggle_manual_hidden,
)
from .host import HostInfo
from .lifecycle import helper_should_exit, pid_alive
from .look import (
    LookCapture,
    ShotResult,
    capture_explicit_window,
    capture_indicator,
    cycle_look_pin,
    look_button_title,
    should_capture,
)
from .panels import (
    SUMMARY_SECONDS,
    approval_body,
    drop_contents,
    drop_size,
    layout_drop,
    pointer_inside,
    step_hover,
    step_summary_timer,
)
from .position import display_for_rect, load_placement, restore_bubble, save_position
from .protocol import EyeServer
from .qt_geometry import (
    QtRect,
    appkit_origin_to_qt,
    place_card,
    primary_bottom,
    qt_origin_to_appkit,
    qt_point_to_appkit,
    qt_screen_to_display,
)
from .qt_paint import paint_skin
from .robot import load_layout, normalize_skin, panel_size, point_in_frame, resolve_skin
from .tracking import PHASE_PER_SEC, gaze_from_points, tick_interval, tracked_pupil, typing_reaction


def run_overlay(args, host: HostInfo) -> int:
    """Show the bubble until ihagent exits. Returns 0 when PySide6 is missing."""
    if not pid_alive(int(args.parent_pid)):
        return 0
    try:
        from PySide6.QtCore import Qt
        from PySide6.QtWidgets import QApplication
    except ImportError:
        print(INSTALL_QT_HINT, file=sys.stderr)
        return 0
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv[:1] or ["iheye"])
    app.setQuitOnLastWindowClosed(False)
    desktop = Desktop(args, host)
    desktop.start()
    return int(app.exec() or 0)


class Desktop:
    """GUI objects live on the Qt thread. The socket thread only enqueues."""

    def __init__(self, args, host: HostInfo) -> None:
        from PySide6.QtCore import QAbstractNativeEventFilter, Qt, QTimer
        from PySide6.QtGui import QCursor
        from PySide6.QtWidgets import (
            QLabel,
            QLineEdit,
            QPushButton,
            QWidget,
        )

        self._Qt = Qt
        self._QCursor = QCursor
        self.args = args
        self.host = host
        self.parent_pid = int(args.parent_pid)
        self.position_path = str(args.position)
        self.skin = resolve_skin(getattr(args, "skin", ""), os.environ)
        self.panel_w, self.panel_h = panel_size(self.skin)
        self.track = str(getattr(args, "track", "1")) != "0"
        self.summary_enabled = str(getattr(args, "summary", "1")) != "0"
        self.look_enabled = str(getattr(args, "look", "1")) != "0"
        self.look_pin = None
        self.reduce_motion = reduce_motion_from_env()
        self.state = "idle"
        self.state_t0 = time.monotonic()
        self.last_tick = self.state_t0
        self.working = False
        self.failed = False
        self.reply_text = ""
        self.summary_text = ""
        self.summary_left = 0.0
        self.summary_showing = False
        self.approval = None
        self.hover_open = False
        self.away = 0.0
        self.suppressed = False
        self.eye_visible = False
        self.manual_hidden = False
        self.detection_ok = False
        self._last_terminal: bool | None = None
        self._wayland_noted = False
        self.quit_requested = False
        self._mx = 0.0
        self._my = 0.0
        self._mouse_moved_until = 0.0
        self._mode = "closed"
        self._shown = None
        self._pupil = None
        self._blink_extra = 0.0
        self._phase = 0.0
        self._focus_at = 0.0
        self._parent_at = 0.0
        self._inbox: queue.Queue = queue.Queue()
        self._ancestors = ancestor_pids(
            os.getppid(),
            linux_parent_of if sys.platform != "win32" else _windows_parent,
        )

        flags = (
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.bubble = _Bubble(self)
        self.bubble.setWindowFlags(flags | Qt.WindowType.WindowDoesNotAcceptFocus)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.bubble.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.bubble.setFixedSize(int(self.panel_w), int(self.panel_h))
        self.bubble.setWindowTitle("iheye")

        self.card = QWidget()
        self.card.setWindowFlags(flags)
        self.card.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.card.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.card.setObjectName("iheyeCard")
        self.card.setStyleSheet(
            "#iheyeCard { background-color: rgba(18, 26, 36, 245); border-radius: 14px; }"
        )
        self.card.setWindowTitle("iheye")
        self.reply = QLabel(self.card)
        self.reply.setWordWrap(True)
        self.field = QLineEdit(self.card)
        self.field.setPlaceholderText("Message ihagent")
        self.look_button = QPushButton(self.card)
        self.yes = QPushButton("y", self.card)
        self.no = QPushButton("N", self.card)
        self.note = QLabel(self.card)
        for widget in (self.reply, self.field, self.look_button, self.yes, self.no, self.note):
            widget.setStyleSheet(_control_style(widget is self.field or isinstance(widget, QPushButton)))
        self.reply.setStyleSheet("color: #d5e8ea; background: transparent;")
        self.note.setStyleSheet("color: #8fd0c8; background: transparent; font-size: 11px;")
        self.field.returnPressed.connect(self._send_chat)
        self.look_button.clicked.connect(self._cycle_look)
        self.yes.clicked.connect(lambda: self._answer_approval(True))
        self.no.clicked.connect(lambda: self._answer_approval(False))
        self.card.mousePressEvent = self._card_mouse  # type: ignore[method-assign]
        self._refresh_look_button()

        from PySide6.QtGui import QKeySequence, QShortcut

        self._esc = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.card)
        self._esc.activated.connect(self.dismiss)
        self.server = EyeServer(str(args.socket), self._inbox.put)
        self.timer = QTimer()
        self.timer.timeout.connect(self._tick)
        self._hotkey = None
        self._native = None
        self._hotkey_filter_type = QAbstractNativeEventFilter if sys.platform == "win32" else None

    def start(self) -> None:
        self._place_bubble()
        from PySide6.QtGui import QGuiApplication

        if QGuiApplication.platformName() != "offscreen":
            self._apply_mask()
        self.server.start()
        self._apply_visibility(True)
        if self._hotkey_filter_type is not None:
            self._install_windows_hotkey(self._hotkey_filter_type)
        self.timer.start(40)

    def _place_bubble(self) -> None:
        displays, bottom, _screens = self._desktop()
        from PySide6.QtGui import QCursor

        pos = QCursor.pos()
        mouse = qt_point_to_appkit(pos.x(), pos.y(), bottom) if displays else None
        main = displays[0] if displays else None
        placed = restore_bubble(
            load_placement(self.position_path),
            displays,
            mouse,
            main,
            self.panel_w,
            self.panel_h,
        )
        qx, qy = appkit_origin_to_qt(placed.x, placed.y, self.panel_h, bottom)
        self.bubble.move(int(qx), int(qy))

    def _desktop(self):
        from PySide6.QtGui import QGuiApplication

        screens = list(QGuiApplication.screens())
        primary = QGuiApplication.primaryScreen()
        if primary is None and screens:
            primary = screens[0]
        if primary is None:
            fake = QtRect(0, 0, 1440, 900)
            bottom = primary_bottom(fake)
            display = qt_screen_to_display(fake, fake, bottom)
            return [display], bottom, [fake]
        pgeo = primary.geometry()
        bottom = primary_bottom(QtRect(pgeo.x(), pgeo.y(), pgeo.width(), pgeo.height()))
        displays = []
        frames = []
        for screen in screens:
            full = _qrect(screen.geometry())
            visible = _qrect(screen.availableGeometry())
            frames.append(full)
            displays.append(qt_screen_to_display(full, visible, bottom))
        if not displays:
            fake = QtRect(0, 0, 1440, 900)
            displays = [qt_screen_to_display(fake, fake, bottom)]
            frames = [fake]
        return displays, bottom, frames

    def _bubble_qt(self) -> QtRect:
        geo = self.bubble.geometry()
        return QtRect(geo.x(), geo.y(), geo.width(), geo.height())

    def _visible_for_bubble(self, displays):
        bubble = self._bubble_qt()
        _displays, bottom, _frames = self._desktop()
        ax, ay = qt_origin_to_appkit(bubble.x, bubble.y, bubble.h, bottom)
        hit = display_for_rect(displays, ax, ay, bubble.w, bubble.h)
        if hit is None and displays:
            hit = displays[0]
        return hit, bottom

    def _apply_mask(self) -> None:
        from PySide6.QtGui import QRegion

        width = int(self.panel_w)
        height = int(self.panel_h)
        step = 3
        region = QRegion()
        for y in range(0, height, step):
            run = None
            x = 0
            while x < width:
                if self.hit_bubble(x + 0.5, y + 0.5):
                    if run is None:
                        run = x
                elif run is not None:
                    region += QRegion(run, y, x - run, step)
                    run = None
                x += step
            if run is not None:
                region += QRegion(run, y, width - run, step)
        if not region.isEmpty():
            self.bubble.setMask(region)

    def follow_drag(self) -> None:
        if self._mode != "closed":
            self._place_card()

    def drag_end(self) -> None:
        displays, bottom, _frames = self._desktop()
        bubble = self._bubble_qt()
        ax, ay = qt_origin_to_appkit(bubble.x, bubble.y, bubble.h, bottom)
        hit = display_for_rect(displays, ax, ay, bubble.w, bubble.h)
        screen = hit.frame if hit is not None else None
        try:
            save_position(self.position_path, ax, ay, screen)
        except OSError:
            pass
        self.follow_drag()

    def focus_host(self) -> None:
        if sys.platform == "win32":
            _focus_windows_console()
            return
        pid = self.host.pid or 0
        if not pid and self._ancestors:
            pid = self._ancestors[0]
        if pid:
            _run_quiet(["xdotool", "search", "--onlyvisible", "--pid", str(pid), "windowactivate"])

    def _tick(self) -> None:
        now = time.monotonic()
        if now - self._parent_at > 0.8:
            self._parent_at = now
            if helper_should_exit(
                quit_requested=self.quit_requested,
                parent_alive=pid_alive(self.parent_pid),
            ):
                self._shutdown()
                return
        self._drain()
        dt = min(0.25, max(0.0, now - self.last_tick))
        self.last_tick = now
        self._phase = (now - self.state_t0) * PHASE_PER_SEC
        from PySide6.QtGui import QCursor

        pos = QCursor.pos()
        mx, my = float(pos.x()), float(pos.y())
        moved = abs(mx - self._mx) + abs(my - self._my) > 0.8
        self._mx, self._my = mx, my
        if moved:
            self._mouse_moved_until = now + 0.35
        if now - self._focus_at > 0.35:
            self._focus_at = now
            self._poll_focus()
        bubble = self._bubble_qt()
        rects = [(bubble.x, bubble.y, bubble.w, bubble.h)]
        if self.card.isVisible():
            geo = self.card.geometry()
            rects.append((geo.x(), geo.y(), geo.width(), geo.height()))
        inside = pointer_inside(mx, my, rects)
        focused = self.card.isActiveWindow() or self.field.hasFocus()
        self.hover_open, self.away, self.suppressed = step_hover(
            open_now=self.hover_open,
            inside=inside,
            focused=focused,
            away_for=self.away,
            suppressed=self.suppressed,
            dt=dt,
        )
        if self.summary_text:
            self.summary_left, self.summary_showing = step_summary_timer(
                self.summary_left,
                inside,
                dt,
                self.summary_showing,
            )
            if not self.summary_showing:
                self.summary_text = ""
        self._sync_card()
        pupil = None
        blink = 0.0
        if self.track and self.eye_visible:
            cx = bubble.x + bubble.w / 2.0
            cy = bubble.y + bubble.h / 2.0
            gaze = gaze_from_points(cx, -cy, mx, -my)
            base = pupil_offset(self.state, self._phase)
            pupil = tracked_pupil(base, gaze, (0.0, 0.0))
            _offset, blink = typing_reaction(None, self._phase)
        self._pupil = pupil
        self._blink_extra = blink
        if self.eye_visible:
            self.bubble.update()
        interval = tick_interval(
            visible=self.eye_visible,
            state=self.state,
            phase=self._phase,
            tracking=self.track,
            pointer_near_eye=inside,
            mouse_moved=now < self._mouse_moved_until,
            typing=False,
            skin=self.skin,
        )
        msec = max(16, int(interval * 1000))
        if self.timer.interval() != msec:
            self.timer.setInterval(msec)

    def _poll_focus(self) -> None:
        probe = _probe(self.host, self._ancestors, os.getpid())
        self.detection_ok = probe.available and probe.is_terminal is not None
        self._last_terminal = probe.is_terminal
        if probe.reason == "wayland" and not self._wayland_noted:
            self._wayland_noted = True
            print(
                f"iheye: Wayland cannot see the focused window. The bubble stays visible. "
                f"{HOTKEY_LABEL} hides it when the desktop delivers that hotkey.",
                file=sys.stderr,
            )
        show = eye_visible(is_terminal=self._last_terminal, manual_hidden=self.manual_hidden)
        if show != self.eye_visible:
            self._apply_visibility(show)

    def toggle_hotkey(self) -> None:
        self.manual_hidden = toggle_manual_hidden(
            self.manual_hidden,
            detection_ok=self.detection_ok,
        )
        self._apply_visibility(
            eye_visible(is_terminal=self._last_terminal, manual_hidden=self.manual_hidden)
        )

    def _apply_visibility(self, show: bool) -> None:
        self.eye_visible = show
        if show:
            self.bubble.show()
            self.bubble.raise_()
        else:
            self.bubble.hide()
            self.hover_open = False
            self.card.hide()
            self._mode = "closed"
        self._sync_card()

    def _drain(self) -> None:
        while True:
            try:
                msg = self._inbox.get_nowait()
            except queue.Empty:
                return
            self._handle(msg)

    def _handle(self, msg: dict) -> None:
        if not isinstance(msg, dict):
            return
        if msg.get("cmd") == "quit":
            self.quit_requested = True
            self._shutdown()
            return
        changed = False
        if "state" in msg:
            state = str(msg.get("state") or "idle")
            self.state = state
            self.state_t0 = time.monotonic()
            if state == "thinking":
                self.approval = None
                self.summary_text = ""
                self.summary_showing = False
                self.reply_text = ""
                self.working = False
                self.failed = False
            changed = True
        if "working" in msg:
            self.working = bool(msg.get("working"))
            changed = True
        if "failed" in msg:
            self.failed = bool(msg.get("failed"))
            changed = True
        if "reply" in msg:
            self.reply_text = str(msg.get("reply") or "")
            changed = True
        if "summary" in msg:
            text = str(msg.get("summary") or "").strip()
            if self.summary_enabled and text and self.eye_visible:
                self.summary_text = text
                self.summary_left = SUMMARY_SECONDS
                self.summary_showing = True
            changed = True
        if "approval" in msg:
            payload = msg.get("approval")
            if isinstance(payload, dict) and str(payload.get("tool") or "").strip():
                self.approval = payload
            else:
                self.approval = None
            changed = True
        if "track" in msg:
            self.track = bool(msg.get("track"))
        if "summary_enabled" in msg:
            self.summary_enabled = bool(msg.get("summary_enabled"))
            if not self.summary_enabled:
                self.summary_text = ""
                self.summary_showing = False
            changed = True
        if "look_enabled" in msg:
            self.look_enabled = bool(msg.get("look_enabled"))
            self._refresh_look_button()
            changed = True
        if changed:
            self.bubble.update()
            self._sync_card()

    def _sync_card(self) -> None:
        if not self.eye_visible:
            if self.card.isVisible():
                self.card.hide()
            self._mode = "closed"
            return
        mode = drop_contents(
            hover=self.hover_open,
            approval=self.approval is not None,
            summary=bool(self.summary_text) and self.summary_showing and self.summary_enabled,
        )
        if mode == "closed":
            if self.card.isVisible():
                self.card.hide()
            self._mode = "closed"
            self._shown = None
            return
        if mode != self._mode or not self.card.isVisible():
            self._mode = mode
            self._place_card()
            self._shown = None
            self.card.show()
            self.card.raise_()
        self._mode = mode
        token = (self.reply_text, self.summary_text, self._approval_token())
        if token != self._shown:
            self._shown = token
            self._fill_card(mode)

    def _place_card(self) -> None:
        if self._mode == "closed":
            return
        displays, bottom, _frames = self._desktop()
        hit, bottom = self._visible_for_bubble(displays)
        if hit is None:
            return
        width, height = drop_size(self._mode)
        card = place_card(self._bubble_qt(), width, height, hit.visible, bottom)
        self.card.setFixedSize(int(width), int(height))
        self.card.move(int(card.x), int(card.y))
        frames = layout_drop(self._mode, width, height)
        self._place_widget(self.reply, frames.get("reply"), height)
        self._place_widget(self.field, frames.get("field"), height)
        self._place_widget(self.look_button, frames.get("look"), height)
        self._place_widget(self.note, frames.get("note"), height)
        self._place_widget(self.yes, frames.get("yes"), height)
        self._place_widget(self.no, frames.get("no"), height)
        chat = self._mode in {"chat", "chat-approval"}
        approval = self._mode in {"approval", "chat-approval"}
        self.field.setVisible(chat)
        self.look_button.setVisible(chat)
        self.note.setVisible(chat)
        self.yes.setVisible(approval)
        self.no.setVisible(approval)
        self.reply.setVisible(True)

    def _place_widget(self, widget, rect, height: float) -> None:
        if widget is None:
            return
        if rect is None:
            widget.hide()
            return
        # layout_drop is bottom-left. Qt is top-left.
        top = height - (rect.y + rect.h)
        widget.setGeometry(int(rect.x), int(top), int(rect.w), int(rect.h))
        widget.show()

    def _fill_card(self, mode: str) -> None:
        tool = ""
        preview = ""
        if isinstance(self.approval, dict):
            tool = str(self.approval.get("tool") or "")
            preview = str(self.approval.get("preview") or "")
        if mode == "summary":
            body = self.summary_text + "\n\nClick to open ihagent"
        elif mode == "approval":
            body = approval_body(tool, preview)
        elif mode == "chat-approval":
            lead = self.reply_text or "Reply shows here"
            body = lead + "\n" + approval_body(tool, preview)
        else:
            body = self.reply_text or "Reply shows here"
        self.reply.setText(body)

    def _approval_token(self):
        if not isinstance(self.approval, dict):
            return None
        return (str(self.approval.get("tool") or ""), str(self.approval.get("preview") or ""))

    def _send_chat(self) -> None:
        text = str(self.field.text() or "").strip()
        if not text:
            return
        self.field.clear()
        requested = should_capture(text, enabled=bool(self.look_enabled), pin=self.look_pin)
        capture = None
        if requested:
            capture = self._capture_now()
        self._deliver(text, capture)

    def _capture_now(self) -> LookCapture | None:
        """One foreground-window picture. Not called from the animation timer."""
        image_path = os.path.join(tempfile.gettempdir(), f"hfagent-iheye-look-{os.getpid()}.png")
        if sys.platform == "win32":
            hwnd, pid, title = _windows_foreground_target()
            return capture_explicit_window(
                requested=True,
                hwnd=hwnd,
                pid=pid,
                title=title,
                host=self.host,
                helper_pid=os.getpid(),
                image_path=image_path,
                grab=_grab_hwnd_qt,
            )
        return _capture_linux_window(self.host, image_path)

    def _deliver(self, text: str, capture: LookCapture | None) -> None:
        payload = {"cmd": "chat", "text": text}
        indicator = ""
        if capture is not None:
            hint = str(capture.hint or "").strip()
            if hint:
                print(hint, file=sys.stderr)
                self.server.send({"cmd": "hint", "text": hint})
            public = capture.public()
            if public and not public.get("skipped"):
                payload["look"] = public
            indicator = capture_indicator(capture)
        self.note.setText(indicator)
        self.server.send(payload)

    def _cycle_look(self) -> None:
        if not self.look_enabled:
            self._refresh_look_button()
            return
        self.look_pin = cycle_look_pin(self.look_pin)
        self._refresh_look_button()

    def _refresh_look_button(self) -> None:
        if not self.look_enabled:
            self.look_button.setText("eye off")
            return
        title = look_button_title(self.look_pin).replace("👁", "eye")
        self.look_button.setText(title)

    def _answer_approval(self, yes: bool) -> None:
        self.server.send({"cmd": "approve", "yes": bool(yes)})
        self.approval = None
        self._sync_card()

    def _card_mouse(self, event) -> None:
        if self._mode == "summary":
            self.focus_host()
            return
        event.ignore()

    def dismiss(self) -> None:
        self.suppressed = True
        self.hover_open = False
        self._sync_card()

    def paint_bubble(self, painter) -> None:
        speaking = self.state == "thinking" and bool(str(self.reply_text or "").strip())
        paint_skin(
            painter,
            self.skin,
            self.bubble.width(),
            self.bubble.height(),
            state=self.state,
            phase=self._phase,
            pupil=self._pupil,
            blink_extra=self._blink_extra,
            speaking=speaking,
            working=self.working,
            failed=self.failed,
            reduce_motion=self.reduce_motion,
        )

    def hit_bubble(self, x: float, y_qt: float) -> bool:
        width = float(self.bubble.width())
        height = float(self.bubble.height())
        y = height - y_qt
        skin = normalize_skin(self.skin)
        if skin == "bot":
            return point_in_bot(x, y, width, height)
        if skin == "grok":
            return point_in_face(x, y, width, height)
        if skin == "robot":
            outline = (load_layout() or {}).get("outline") or []
            if outline:
                return point_in_frame(x, y, width, height, outline)
        return True

    def _shutdown(self) -> None:
        from PySide6.QtWidgets import QApplication

        self.timer.stop()
        try:
            self.server.stop()
        except Exception:
            pass
        app = QApplication.instance()
        if app is not None:
            app.quit()

    def _install_windows_hotkey(self, filter_type) -> None:
        import ctypes
        from ctypes import wintypes

        from PySide6.QtWidgets import QApplication

        user32 = ctypes.windll.user32
        MOD_ALT = 0x0001
        MOD_CONTROL = 0x0002
        hwnd = int(self.bubble.winId() or 0)
        if not user32.RegisterHotKey(hwnd or None, 1, MOD_ALT | MOD_CONTROL, 0x49):
            return

        class _Filter(filter_type):
            def __init__(self, callback):
                super().__init__()
                self._callback = callback

            def nativeEventFilter(self, event_type, message):
                if event_type not in {b"windows_generic_MSG", b"windows_dispatcher_MSG"}:
                    return False, 0
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == 0x0312:
                    self._callback()
                    return True, 0
                return False, 0

        self._native = _Filter(self.toggle_hotkey)
        app = QApplication.instance()
        if app is not None:
            app.installNativeEventFilter(self._native)


class _Bubble:
    """Built as a function so the QWidget subclass is created after PySide6 imports."""

    def __new__(cls, desktop: Desktop):
        from PySide6.QtCore import Qt
        from PySide6.QtGui import QPainter
        from PySide6.QtWidgets import QWidget

        class Bubble(QWidget):
            def __init__(self, owner: Desktop) -> None:
                super().__init__()
                self.owner = owner
                self._press = None
                self._origin = None
                self._moved = False

            def paintEvent(self, _event) -> None:
                painter = QPainter(self)
                painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
                self.owner.paint_bubble(painter)
                painter.end()

            def mousePressEvent(self, event) -> None:
                if event.button() != Qt.MouseButton.LeftButton:
                    return
                local = event.position()
                if not self.owner.hit_bubble(float(local.x()), float(local.y())):
                    event.ignore()
                    return
                self._press = event.globalPosition()
                self._origin = self.pos()
                self._moved = False

            def mouseMoveEvent(self, event) -> None:
                if self._press is None or self._origin is None:
                    return
                delta = event.globalPosition() - self._press
                if abs(delta.x()) > 3 or abs(delta.y()) > 3:
                    self._moved = True
                self.move(self._origin + delta.toPoint())
                self.owner.follow_drag()

            def mouseReleaseEvent(self, _event) -> None:
                if self._press is None:
                    return
                if self._moved:
                    self.owner.drag_end()
                else:
                    self.owner.focus_host()
                self._press = None

        return Bubble(desktop)


def _qrect(rect) -> QtRect:
    return QtRect(float(rect.x()), float(rect.y()), float(rect.width()), float(rect.height()))


def _control_style(filled: bool) -> str:
    if not filled:
        return "color: #d5e8ea; background: transparent;"
    return (
        "background: #12202a; color: #e7f6f4; border: 1px solid #2a4a52; "
        "border-radius: 6px; padding: 2px 6px;"
    )


def _probe(host: HostInfo, ancestors: tuple[int, ...], helper_pid: int):
    from .foreground import FocusProbe

    pids = terminal_pid_set(
        host_pid=host.pid,
        ancestor_pids=tuple(host.ancestor_pids) + tuple(ancestors),
    )
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        foreground, console_hwnd, console_pid = read_windows_foreground(user32, kernel32)
        matched = foreground_is_terminal(
            foreground_hwnd=foreground.hwnd,
            foreground_pid=foreground.pid,
            console_hwnd=console_hwnd,
            console_pid=console_pid,
            terminal_pids=pids,
            helper_pid=helper_pid,
        )
        return FocusProbe(True, matched, "ok")
    mode = linux_session_mode()
    active = linux_active_pid() if mode == "probe" else None
    return interpret_linux_focus(
        mode=mode,
        active_pid=active,
        terminal_pids=pids,
        helper_pid=helper_pid,
    )


def _windows_parent(pid: int) -> int:
    """Best-effort parent pid. Tests inject their own walker; this is the live one."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return 0
    TH32CS_SNAPPROCESS = 0x00000002

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", ctypes.c_long),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", ctypes.c_wchar * 260),
        ]

    kernel = ctypes.windll.kernel32
    snap = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if not snap:
        return 0
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    parent = 0
    try:
        ok = kernel.Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            if int(entry.th32ProcessID) == int(pid):
                parent = int(entry.th32ParentProcessID)
                break
            ok = kernel.Process32NextW(snap, ctypes.byref(entry))
    finally:
        kernel.CloseHandle(snap)
    return parent


def _windows_foreground_target() -> tuple[int, int, str]:
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    foreground, _console_hwnd, _console_pid = read_windows_foreground(user32, kernel32)
    title = ""
    if foreground.hwnd:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetWindowTextW(foreground.hwnd, buf, 512)
        title = buf.value
    return foreground.hwnd, foreground.pid, title


def _grab_hwnd_qt(hwnd: int, path: str) -> ShotResult:
    """Copy one window. Refuses hwnd 0 so this cannot become a full-screen grab."""
    if int(hwnd or 0) <= 0:
        return ShotResult(False, "no window", 0)
    try:
        from PySide6.QtGui import QGuiApplication
    except ImportError:
        return ShotResult(False, "PySide6 missing", 0)
    screen = QGuiApplication.primaryScreen()
    if screen is None:
        return ShotResult(False, "no screen", 0)
    pixmap = screen.grabWindow(int(hwnd))
    if pixmap.isNull():
        return ShotResult(False, "empty capture", 0)
    if pixmap.width() > 1600 or pixmap.height() > 1600:
        pixmap = pixmap.scaled(1600, 1600)
    if not pixmap.save(path, "PNG"):
        return ShotResult(False, "could not write png", 0)
    try:
        size = os.path.getsize(path)
    except OSError:
        size = 0
    return ShotResult(ok=size > 100, stderr="", size=size)


def _capture_linux_window(host: HostInfo, image_path: str) -> LookCapture | None:
    """Active X11 window only, via xdotool. Never a full desktop grab."""
    wid = _run_quiet(["xdotool", "getactivewindow"])
    if not wid or not wid.strip():
        return LookCapture(skipped="no-window", source="window", hint="iheye: could not see the focused window.")
    window = wid.strip().split()[-1]
    if window in {"0", "0x0"}:
        return LookCapture(skipped="no-window", source="window")
    pid_text = _run_quiet(["xdotool", "getwindowpid", window]) or ""
    try:
        pid = int(pid_text.strip().split()[-1])
    except (ValueError, IndexError):
        pid = 0
    name = (_run_quiet(["xdotool", "getwindowname", window]) or "").strip()

    def grab(_hwnd: int, path: str) -> ShotResult:
        # import(1) captures one X window. A missing tool is a hint, not a desktop shot.
        out = _run_quiet(["import", "-window", window, path])
        if out is None and not os.path.isfile(path):
            return ShotResult(False, "import is not available", 0)
        try:
            size = os.path.getsize(path) if os.path.isfile(path) else 0
        except OSError:
            size = 0
        return ShotResult(ok=size > 100, stderr="" if size > 100 else "import failed", size=size)

    return capture_explicit_window(
        requested=True,
        hwnd=1,
        pid=pid,
        title=name,
        host=host,
        helper_pid=os.getpid(),
        image_path=image_path,
        grab=grab,
    )


def _focus_windows_console() -> None:
    import ctypes

    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    hwnd = int(kernel32.GetConsoleWindow() or 0)
    if not hwnd:
        return
    user32.ShowWindow(hwnd, 9)
    user32.SetForegroundWindow(hwnd)


def _run_quiet(argv: list[str]) -> str | None:
    import subprocess

    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=2, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout or ""
