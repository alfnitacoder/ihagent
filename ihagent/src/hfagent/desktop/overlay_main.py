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
from .geometry import Rect
from .overlay_geom import (
    monitors_from_appkit_frames,
    monitors_from_qt_rects,
    place_ops,
    qt_window_geometry,
    virtual_rect,
)
from .overlay_host import decode_message, encode_message
from .overlay_motion import OverlayShow


# Order inside ``run_appkit``. The stdin thread must not exist before
# NSApplication: AppKit then decides the main thread is not the main
# thread, and the panel never appears.
APPKIT_STARTUP_STEPS = (
    "shared_application",
    "accessory_policy",
    "panels_per_screen",
    "order_front",
    "stdin_thread",
    "timer",
    "run_loop",
)


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
        self.pointer_ops: list[dict] = []
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
        pointer_ops = describe_scene(frame, self.virtual)
        self.pointer_ops = pointer_ops
        # Qt still uses one virtual-desktop window. AppKit places each
        # screen from ``pointer_ops`` so Retina scales stay per display.
        if self.virtual is not None:
            self.ops = place_ops(pointer_ops, self.virtual)
        else:
            self.ops = []
        self.opacity = frame.opacity
        return self.ops, frame.opacity


def _mark_agent_process() -> None:
    """Accessory UI before AppKit starts, so macOS will show our panels."""
    try:
        from Foundation import NSBundle

        info = NSBundle.mainBundle().infoDictionary()
        if info is not None:
            info["LSUIElement"] = True
            info["LSBackgroundOnly"] = False
    except Exception:
        return


def run_appkit(parent_pid: int) -> int:
    """One click-through panel per display. See ``APPKIT_STARTUP_STEPS``.

    The previous helper started a thread before ``NSApplication``, drew
    with ``display()`` into a single window spanning every screen, and
    claimed the view was opaque. On macOS that panel never composited, so
    the arrow was invisible while the system pointer still moved.
    """
    # startup: shared_application — nothing else may run first.
    _mark_agent_process()
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
            NSStatusWindowLevel,
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
        from Foundation import NSRunLoop, NSRunLoopCommonModes
        from PyObjCTools import AppHelper
    except ImportError as exc:
        emit({"event": "failed", "error": f"ihmouse overlay needs PyObjC ({exc}). The system pointer still moves."})
        return 0

    from .overlay_paint import paint_appkit
    from .overlay_window import apply_appkit_panel, choose_overlay_level

    assistive = None
    try:
        from Quartz import CGWindowLevelForKey, kCGAssistiveTechHighWindowLevelKey

        assistive = int(CGWindowLevelForKey(kCGAssistiveTechHighWindowLevelKey))
    except Exception:
        assistive = None
    try:
        status_level = int(NSStatusWindowLevel)
    except Exception:
        status_level = 25
    level = choose_overlay_level(
        floating=int(NSFloatingWindowLevel),
        status=status_level,
        assistive=assistive,
    )

    state = _Runner(parent_pid)
    state.view_monitors = {}
    app = NSApplication.sharedApplication()
    # startup: accessory_policy
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

    class IhmouseView(NSView):
        def isFlipped(self):
            return True

        def isOpaque(self):
            return False

        def hitTest_(self, _point):
            return None

        def drawRect_(self, _dirty):
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(self.bounds())
            monitor = state.view_monitors.get(id(self))
            if monitor is None:
                return
            try:
                local = place_ops(
                    list(state.pointer_ops),
                    Rect(monitor.x, monitor.y, monitor.width, monitor.height),
                )
                paint_appkit(local, float(state.opacity))
            except Exception:
                return

    style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
    behavior = (
        NSWindowCollectionBehaviorCanJoinAllSpaces
        | NSWindowCollectionBehaviorStationary
        | NSWindowCollectionBehaviorFullScreenAuxiliary
        | NSWindowCollectionBehaviorIgnoresCycle
    )

    class Ticker(NSObject):
        def initWithState_(self, runner):
            self = objc.super(Ticker, self).init()
            if self is None:
                return None
            self.runner = runner
            self.panels = []
            self.signature = None
            self.level = level
            self._front_tick = 0
            return self

        # PyObjC registers ``_show`` as the selector ``_show`` (zero arguments).
        # The callable takes ``panel``, so creating this class raises
        # ``'_show' expects 0 arguments ... has 1 positional arguments`` and
        # the panel is never ordered front. ``python_method`` keeps helpers
        # off that table. Names with an internal underscore (``_match_scale``)
        # are already skipped, and are marked the same way.
        @objc.python_method
        def _close_panels(self):
            for panel, _view in self.panels:
                try:
                    panel.orderOut_(None)
                    panel.close()
                except Exception:
                    continue
            self.panels = []
            self.runner.view_monitors = {}

        @objc.python_method
        def _show(self, panel) -> None:
            panel.setLevel_(self.level)
            panel.orderFrontRegardless()
            panel.setIgnoresMouseEvents_(True)
            try:
                panel.setSharingType_(NSWindowSharingNone)
            except Exception:
                pass

        @objc.python_method
        def _match_scale(self, view) -> None:
            try:
                window = view.window()
                if window is None:
                    return
                layer = view.layer()
                if layer is None:
                    return
                layer.setContentsScale_(float(window.backingScaleFactor() or 1.0))
                layer.setOpaque_(False)
            except Exception:
                return

        # startup: panels_per_screen
        def sync_(self, _force=False) -> None:
            screens = list(NSScreen.screens() or [])
            frames = []
            scales = []
            for screen in screens:
                frame = screen.frame()
                frames.append(
                    (
                        float(frame.origin.x),
                        float(frame.origin.y),
                        float(frame.size.width),
                        float(frame.size.height),
                    )
                )
                try:
                    scales.append(float(screen.backingScaleFactor() or 1.0))
                except Exception:
                    scales.append(1.0)
            signature = tuple((tuple(round(v, 2) for v in frame), round(scale, 2)) for frame, scale in zip(frames, scales))
            monitors, primary = monitors_from_appkit_frames(frames, scales)
            virtual = virtual_rect(monitors)
            self.runner.virtual = virtual
            self.runner.primary_height = primary
            if signature == self.signature and self.panels:
                for (_panel, view), monitor in zip(self.panels, monitors):
                    self.runner.view_monitors[id(view)] = monitor
                return
            self._close_panels()
            self.signature = signature
            built = []
            for screen, monitor in zip(screens, monitors):
                frame = screen.frame()
                panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
                    frame,
                    style,
                    NSBackingStoreBuffered,
                    False,
                )
                apply_appkit_panel(
                    panel,
                    clear_color=NSColor.clearColor(),
                    floating_level=self.level,
                    sharing_none=NSWindowSharingNone,
                    behavior=behavior,
                )
                view = IhmouseView.alloc().initWithFrame_(
                    NSMakeRect(0, 0, float(frame.size.width), float(frame.size.height))
                )
                view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
                self.runner.view_monitors[id(view)] = monitor
                panel.setContentView_(view)
                panel.setIgnoresMouseEvents_(True)
                # AppKit-managed layer only, after the view has a window.
                # A custom CALayer tree SIGTRAPs the iheye robot; do not call setLayer_.
                view.setWantsLayer_(True)
                try:
                    from AppKit import NSViewLayerContentsRedrawOnSetNeedsDisplay

                    view.setLayerContentsRedrawPolicy_(NSViewLayerContentsRedrawOnSetNeedsDisplay)
                except Exception:
                    try:
                        view.setLayerContentsRedrawPolicy_(2)
                    except Exception:
                        pass
                # startup: order_front — again from the timer once the run loop is up.
                self._show(panel)
                self._match_scale(view)
                built.append((panel, view))
            self.panels = built

        def tick_(self, _timer):
            if self.runner.drain():
                AppHelper.stopEventLoop()
                return
            self.runner._screen_tick += 1
            self._front_tick += 1
            if not self.panels or self.runner.virtual is None or self.runner._screen_tick % 30 == 0:
                self.sync_(None)
            self.runner.step()
            visible = self.runner.opacity > 0.0 and bool(self.runner.pointer_ops)
            for panel, view in self.panels:
                if visible:
                    self._match_scale(view)
                    if (not panel.isVisible()) or self._front_tick % 30 == 0:
                        self._show(panel)
                    view.setNeedsDisplay_(True)
                else:
                    try:
                        if panel.isVisible():
                            view.setNeedsDisplay_(True)
                            panel.orderOut_(None)
                    except Exception:
                        pass
            self.runner.emit_pending()

    ticker = Ticker.alloc().initWithState_(state)
    ticker.sync_(None)
    # startup: stdin_thread — only after the app object and the panels exist.
    state.start_reader()
    # startup: timer. Common modes so the glide keeps painting during clicks.
    timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
        1.0 / 60.0, ticker, "tick:", None, True
    )
    # Common modes include the default mode. Registering both would tick twice.
    loop = NSRunLoop.currentRunLoop()
    loop.addTimer_forMode_(timer, NSRunLoopCommonModes)
    emit({"event": "ready"})
    # startup: run_loop
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
