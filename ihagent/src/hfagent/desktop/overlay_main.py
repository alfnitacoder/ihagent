"""ihmouse overlay process. Stdin is a stream of JSON commands.

The window covers every display, stays above other apps, and ignores the
pointer. It never grabs the screen. AppKit on macOS, Qt elsewhere. If the
toolkit or a display is missing, the process reports that and exits.
"""

from __future__ import annotations

import argparse
import os
import queue
import sys
import threading
import time

from ..iheye.lifecycle import helper_should_exit, pid_alive
from .overlay_draw import describe_scene
from .overlay_geom import (
    appkit_window_frame,
    monitors_from_appkit_frames,
    monitors_from_qt_rects,
    place_ops,
    qt_window_geometry,
    virtual_rect,
)
from .overlay_host import decode_message, encode_message
from .overlay_motion import OverlayShow


def emit(payload: dict) -> None:
    sys.stdout.write(encode_message(payload))
    sys.stdout.flush()


def _stdin_loop(inbox: queue.Queue) -> None:
    for line in sys.stdin:
        message = decode_message(line)
        if message is not None:
            inbox.put(message)
        if message and message.get("op") == "quit":
            return
    inbox.put({"op": "quit"})


class _Runner:
    def __init__(self, parent_pid: int) -> None:
        self.parent_pid = parent_pid
        self.inbox: queue.Queue = queue.Queue()
        self.show = OverlayShow()
        self.virtual = None
        self.primary_height = 0.0
        self.last = time.monotonic()
        self._screen_tick = 0
        self.ops: list[dict] = []
        self.opacity = 0.0
        self.pending_events: list[dict] = []

    def start_reader(self) -> None:
        threading.Thread(target=_stdin_loop, args=(self.inbox,), name="ihmouse-stdin", daemon=True).start()

    def drain(self) -> bool:
        """Apply queued commands. True when the helper should exit.

        Events are held until the matching frame has been painted, so a
        screenshot wait does not race the hide.
        """
        self.pending_events = []
        while True:
            try:
                message = self.inbox.get_nowait()
            except queue.Empty:
                break
            for event in self.show.handle(message):
                if event.get("event") == "quit":
                    return True
                self.pending_events.append(event)
        if helper_should_exit(
            quit_requested=self.show.quit,
            parent_alive=pid_alive(self.parent_pid),
        ):
            return True
        return False

    def emit_pending(self) -> None:
        for event in self.pending_events:
            emit(event)
        self.pending_events = []

    def step(self) -> tuple[list[dict], float]:
        now = time.monotonic()
        dt = min(0.1, max(0.0, now - self.last))
        self.last = now
        frame, events = self.show.tick(dt)
        self.pending_events.extend(events)
        ops = describe_scene(frame, self.virtual)
        if self.virtual is not None:
            ops = place_ops(ops, self.virtual)
        self.ops = ops
        self.opacity = frame.opacity
        return ops, frame.opacity


def run_appkit(parent_pid: int) -> int:
    try:
        import objc
        from AppKit import (
            NSApplication,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSBezierPath,
            NSColor,
            NSFloatingWindowLevel,
            NSMakeRect,
            NSObject,
            NSPanel,
            NSScreen,
            NSTimer,
            NSView,
            NSViewHeightSizable,
            NSViewWidthSizable,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
            NSWindowSharingNone,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
        )
        from PyObjCTools import AppHelper
    except ImportError as exc:
        emit({"event": "failed", "error": f"ihmouse overlay needs PyObjC ({exc}). The system pointer still moves."})
        return 0

    from .overlay_paint import paint_appkit
    from .overlay_window import apply_appkit_panel

    state = _Runner(parent_pid)
    state.start_reader()

    class IhmouseView(NSView):
        def isFlipped(self):
            return True

        def drawRect_(self, _dirty):
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(self.bounds())
            try:
                paint_appkit(list(state.ops), float(state.opacity))
            except Exception:
                return

    class Ticker(NSObject):
        def initWithState_(self, runner):
            self = objc.super(Ticker, self).init()
            if self is None:
                return None
            self.runner = runner
            self.view = None
            self.panel = None
            return self

        def tick_(self, _timer):
            if self.runner.drain():
                AppHelper.stopEventLoop()
                return
            self.runner._screen_tick += 1
            if self.runner.virtual is None or self.runner._screen_tick % 30 == 0:
                _place(self.panel, self.runner)
            self.runner.step()
            self.view.display()
            self.runner.emit_pending()

    def _place(panel, runner: _Runner) -> None:
        screens = list(NSScreen.screens() or [])
        if not screens:
            return
        frames = []
        scales = []
        for screen in screens:
            frame = screen.frame()
            frames.append((float(frame.origin.x), float(frame.origin.y), float(frame.size.width), float(frame.size.height)))
            scales.append(float(screen.backingScaleFactor() or 1.0))
        monitors, primary = monitors_from_appkit_frames(frames, scales)
        virtual = virtual_rect(monitors)
        if virtual is None:
            return
        runner.virtual = virtual
        runner.primary_height = primary
        x, y, width, height = appkit_window_frame(virtual, primary)
        panel.setFrame_display_(NSMakeRect(x, y, width, height), True)

    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
        NSMakeRect(0, 0, 10, 10),
        style,
        NSBackingStoreBuffered,
        False,
    )
    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorIgnoresCycle
    )
    apply_appkit_panel(
        panel,
        clear_color=NSColor.clearColor(),
        floating_level=NSFloatingWindowLevel,
        sharing_none=NSWindowSharingNone,
        behavior=behavior,
    )
    view = IhmouseView.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
    view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
    panel.setContentView_(view)
    _place(panel, state)
    panel.orderFrontRegardless()
    panel.setIgnoresMouseEvents_(True)
    ticker = Ticker.alloc().initWithState_(state)
    ticker.view = view
    ticker.panel = panel
    NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0 / 60.0, ticker, "tick:", None, True
    )
    emit({"event": "ready"})
    AppHelper.runEventLoop()
    return 0


def run_qt(parent_pid: int) -> int:
    try:
        from PySide6.QtCore import Qt, QTimer
        from PySide6.QtGui import QColor, QGuiApplication, QPainter
        from PySide6.QtWidgets import QApplication, QWidget
    except ImportError as exc:
        emit({"event": "failed", "error": f"ihmouse overlay needs PySide6 ({exc}). The system pointer still moves."})
        return 0

    from .overlay_paint import paint_qt
    from .overlay_window import apply_qt_overlay, apply_windows_extended

    if sys.platform == "win32":
        try:
            from .screens import ensure_dpi_aware

            ensure_dpi_aware()
        except Exception:
            pass

    try:
        app = QApplication.instance() or QApplication(sys.argv[:1] or ["ihmouse"])
    except Exception as exc:
        extra = ""
        if os.environ.get("WAYLAND_DISPLAY"):
            extra = " On Wayland, /mouse overlay off if clicks cannot pass through."
        emit({"event": "failed", "error": f"ihmouse overlay did not open ({exc}).{extra} The system pointer still moves."})
        return 0

    state = _Runner(parent_pid)
    state.start_reader()

    class Canvas(QWidget):
        def __init__(self):
            super().__init__()
            self.ops: list[dict] = []
            self.opacity = 1.0

        def paintEvent(self, _event):
            painter = QPainter(self)
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
            painter.fillRect(self.rect(), QColor(0, 0, 0, 0))
            painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
            try:
                paint_qt(painter, self.ops, self.opacity)
            except Exception:
                pass
            painter.end()

    window = Canvas()
    apply_qt_overlay(window, Qt)
    window.setWindowTitle("ihmouse")

    def place() -> None:
        screens = list(QGuiApplication.screens() or [])
        rects = []
        scales = []
        for screen in screens:
            geo = screen.geometry()
            rects.append((float(geo.x()), float(geo.y()), float(geo.width()), float(geo.height())))
            scales.append(float(screen.devicePixelRatio() or 1.0))
        monitors = monitors_from_qt_rects(rects, scales)
        virtual = virtual_rect(monitors)
        if virtual is None:
            return
        state.virtual = virtual
        x, y, width, height = qt_window_geometry(virtual)
        window.setGeometry(int(round(x)), int(round(y)), max(1, int(round(width))), max(1, int(round(height))))

    def tick() -> None:
        if state.drain():
            app.quit()
            return
        state._screen_tick += 1
        if state.virtual is None or state._screen_tick % 30 == 0:
            place()
        state.step()
        window.ops = state.ops
        window.opacity = state.opacity
        window.repaint()
        state.emit_pending()

    place()
    window.show()
    window.raise_()
    if sys.platform == "win32":
        apply_windows_extended(int(window.winId()))
    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(16)
    emit({"event": "ready"})
    app.exec()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ihmouse")
    parser.add_argument("--parent", type=int, default=0)
    args = parser.parse_args(argv)
    if not pid_alive(args.parent) and args.parent > 0:
        return 0
    try:
        if sys.platform == "darwin":
            return run_appkit(args.parent)
        return run_qt(args.parent)
    except Exception as exc:
        try:
            emit({"event": "failed", "error": f"ihmouse overlay failed ({exc}). The system pointer still moves."})
        except Exception:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
