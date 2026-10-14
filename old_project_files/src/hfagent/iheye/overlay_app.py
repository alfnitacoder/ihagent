"""macOS overlay: a borderless, non-activating eye that floats above other apps.

AppKit is imported inside ``run_overlay`` so this module can be imported on
Linux and in tests. The helper process is the only caller.
"""

from __future__ import annotations

import threading
import traceback

from .appearance import eye_geometry
from .focus import FrontApp, activation_plan, eye_should_show, same_host
from .host import HostInfo, front_tty_script, run_osascript
from .lifecycle import helper_should_exit, pid_alive, watch_parent_exit_async
from .position import clamp_position, default_position, load_position, save_position
from .protocol import EyeServer
from .supervisor import INSTALL_HINT

PANEL_W = 88.0
PANEL_H = 100.0


def run_overlay(args, host: HostInfo) -> int:
    """Show the bubble until ihagent exits. Returns 0 even when PyObjC is missing."""
    if not pid_alive(int(args.parent_pid)):
        return 0
    try:
        import objc
        from AppKit import (
            NSApplication,
            NSApplicationActivateIgnoringOtherApps,
            NSApplicationActivationPolicyAccessory,
            NSBackingStoreBuffered,
            NSBezierPath,
            NSColor,
            NSEvent,
            NSFloatingWindowLevel,
            NSFont,
            NSFontAttributeName,
            NSForegroundColorAttributeName,
            NSPanel,
            NSParagraphStyleAttributeName,
            NSRunningApplication,
            NSScreen,
            NSView,
            NSWindowCollectionBehaviorCanJoinAllSpaces,
            NSWindowCollectionBehaviorFullScreenAuxiliary,
            NSWindowCollectionBehaviorIgnoresCycle,
            NSWindowCollectionBehaviorStationary,
            NSWindowStyleMaskBorderless,
            NSWindowStyleMaskNonactivatingPanel,
            NSWorkspace,
        )
        from Foundation import (
            NSMakePoint,
            NSMakeRect,
            NSMutableParagraphStyle,
            NSObject,
            NSString,
            NSTimer,
        )
        from PyObjCTools import AppHelper
    except ImportError:
        print(INSTALL_HINT, file=__import__("sys").stderr)
        return 0

    center = NSMutableParagraphStyle.alloc().init()
    center.setAlignment_(1)  # NSTextAlignmentCenter

    class EyeView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(EyeView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._state = "idle"
            self._phase = 0.0
            self._drag = None
            self._moved = False
            self.onClick = None
            self.onDragEnd = None
            return self

        def isOpaque(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def setEyeState_(self, state):
            self._state = str(state)
            self._phase = 0.0
            self.setNeedsDisplay_(True)

        def tick_(self, _timer):
            self._phase += 0.12
            self.setNeedsDisplay_(True)

        def drawRect_(self, _dirty):
            bounds = self.bounds()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(bounds)
            bubble = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(4, 4, PANEL_W - 8, PANEL_H - 8),
                22,
                22,
            )
            geo = eye_geometry(self._state, self._phase, cx=PANEL_W / 2, cy=58)
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.06, 0.09, 0.14, 0.94).set()
            bubble.fill()
            if geo.pulse > 0.02:
                ring = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(2, 2, PANEL_W - 4, PANEL_H - 4),
                    24,
                    24,
                )
                ring.setLineWidth_(3.0)
                glow = geo.glow_rgb
                NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    glow[0], glow[1], glow[2], min(0.95, geo.pulse)
                ).set()
                ring.stroke()

            mid_y = (geo.top + geo.bottom) / 2
            mid_x = (geo.left + geo.right) / 2
            eye = NSBezierPath.alloc().init()
            eye.moveToPoint_(NSMakePoint(geo.left, mid_y))
            eye.curveToPoint_controlPoint1_controlPoint2_(
                NSMakePoint(geo.right, mid_y),
                NSMakePoint(mid_x, geo.top),
                NSMakePoint(mid_x, geo.top),
            )
            eye.curveToPoint_controlPoint1_controlPoint2_(
                NSMakePoint(geo.left, mid_y),
                NSMakePoint(mid_x, geo.bottom),
                NSMakePoint(mid_x, geo.bottom),
            )
            eye.closePath()
            NSColor.colorWithCalibratedWhite_alpha_(0.96, 1.0).set()
            eye.fill()
            iris_rgb = geo.iris_rgb
            NSColor.colorWithCalibratedRed_green_blue_alpha_(
                iris_rgb[0], iris_rgb[1], iris_rgb[2], 1.0
            ).set()
            eye.setLineWidth_(1.4)
            eye.stroke()

            icx, icy, ir = geo.iris
            iris = NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(icx - ir, icy - ir, ir * 2, ir * 2)
            )
            iris.fill()
            pcx, pcy, pr = geo.pupil
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.05, 0.07, 0.10, 1).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(pcx - pr, pcy - pr, pr * 2, pr * 2)
            ).fill()
            hx, hy, hr = geo.highlight
            NSColor.colorWithCalibratedWhite_alpha_(0.95, 1).set()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(hx - hr, hy - hr, hr * 2, hr * 2)
            ).fill()

            label = NSString.stringWithString_("iheye")
            attrs = {
                NSFontAttributeName: NSFont.boldSystemFontOfSize_(11),
                NSForegroundColorAttributeName: NSColor.colorWithCalibratedRed_green_blue_alpha_(
                    0.75, 0.93, 0.90, 1
                ),
                NSParagraphStyleAttributeName: center,
            }
            label.drawInRect_withAttributes_(NSMakeRect(8, 12, PANEL_W - 16, 16), attrs)

        def mouseDown_(self, _event):
            loc = NSEvent.mouseLocation()
            origin = self.window().frame().origin
            self._drag = (loc.x, loc.y, origin.x, origin.y)
            self._moved = False

        def mouseDragged_(self, _event):
            if not self._drag:
                return
            loc = NSEvent.mouseLocation()
            sx, sy, ox, oy = self._drag
            dx = loc.x - sx
            dy = loc.y - sy
            if abs(dx) > 3 or abs(dy) > 3:
                self._moved = True
            self.window().setFrameOrigin_(NSMakePoint(ox + dx, oy + dy))

        def mouseUp_(self, _event):
            if self._moved and self.onDragEnd is not None:
                origin = self.window().frame().origin
                self.onDragEnd(origin.x, origin.y)
            elif not self._moved and self.onClick is not None:
                self.onClick()
            self._drag = None

    class EyeController(NSObject):
        def init(self):
            self = objc.super(EyeController, self).init()
            if self is None:
                return None
            self.panel = None
            self.view = None
            self.host = host
            self.querying = False
            self.quit_requested = False
            self.parent_pid = int(args.parent_pid)
            return self

        def checkParent_(self, _timer):
            if helper_should_exit(
                quit_requested=self.quit_requested,
                parent_alive=pid_alive(self.parent_pid),
            ):
                NSApplication.sharedApplication().terminate_(None)

        def applyMessage_(self, message):
            text = str(message)
            if text == "quit":
                self.quit_requested = True
                NSApplication.sharedApplication().terminate_(None)
                return
            if self.view is not None:
                self.view.setEyeState_(text)

        def refreshFocus_(self, _timer):
            if self.querying:
                return
            front = _frontmost()
            if front is not None and same_host(front, self.host) and self.host.window_level():
                self.querying = True

                def work(front_app=front):
                    script = front_tty_script(self.host.kind)
                    tty = run_osascript(script, timeout=0.8) if script else ""
                    AppHelper.callAfter(self._apply_focus, front_app, tty)

                threading.Thread(target=work, name="iheye-tty", daemon=True).start()
                return
            self._apply_focus(front, None)

        def _apply_focus(self, front, front_tty):
            self.querying = False
            if self.panel is None:
                return
            if eye_should_show(front, self.host, front_tty):
                self.panel.orderFrontRegardless()
            else:
                self.panel.orderOut_(None)

        def focusHost_(self, _sender):
            plan = activation_plan(self.host)

            def work():
                if plan.applescript:
                    run_osascript(plan.applescript, timeout=1.5)
                _activate(plan)

            threading.Thread(target=work, name="iheye-focus", daemon=True).start()

    class Watcher(NSObject):
        def initWithController_(self, controller):
            self = objc.super(Watcher, self).init()
            if self is None:
                return None
            self.controller = controller
            return self

        def appActivated_(self, _note):
            self.controller.refreshFocus_(None)

    def _frontmost():
        app = NSWorkspace.sharedWorkspace().frontmostApplication()
        if app is None:
            return None
        return FrontApp(
            pid=int(app.processIdentifier()),
            bundle_id=str(app.bundleIdentifier() or ""),
            name=str(app.localizedName() or ""),
        )

    def _activate(plan):
        app = None
        if plan.pid:
            app = NSRunningApplication.runningApplicationWithProcessIdentifier_(int(plan.pid))
        if app is None and plan.bundle_id:
            found = NSRunningApplication.runningApplicationsWithBundleIdentifier_(plan.bundle_id)
            if found:
                app = found[0]
        if app is not None:
            app.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

    try:
        application = NSApplication.sharedApplication()
        application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        x, y = _place_panel(str(args.position))
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, PANEL_W, PANEL_H),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        panel.setFloatingPanel_(True)
        panel.setHidesOnDeactivate_(False)
        panel.setCanHide_(False)
        panel.setOpaque_(False)
        panel.setBackgroundColor_(NSColor.clearColor())
        panel.setHasShadow_(True)
        panel.setBecomesKeyOnlyIfNeeded_(True)
        panel.setWorksWhenModal_(True)
        panel.setMovableByWindowBackground_(False)

        controller = EyeController.alloc().init()
        view = EyeView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
        view.onClick = lambda: controller.focusHost_(None)
        view.onDragEnd = lambda px, py: save_position(args.position, px, py)
        panel.setContentView_(view)
        controller.panel = panel
        controller.view = view
        panel.orderOut_(None)

        watcher = Watcher.alloc().initWithController_(controller)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            watcher,
            "appActivated:",
            "NSWorkspaceDidActivateApplicationNotification",
            None,
        )
        controller.watcher = watcher

        def on_message(msg):
            if msg.get("cmd") == "quit":
                controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                    "applyMessage:", NSString.stringWithString_("quit"), False
                )
                return
            state = msg.get("state")
            if not state:
                return
            controller.performSelectorOnMainThread_withObject_waitUntilDone_(
                "applyMessage:", NSString.stringWithString_(str(state)), False
            )

        server = EyeServer(str(args.socket), on_message)
        server.start()
        try:
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                1.0 / 30.0, view, "tick:", None, True
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.5, controller, "checkParent:", None, True
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.4, controller, "refreshFocus:", None, True
            )
            watch_parent_exit_async(
                int(args.parent_pid),
                lambda: AppHelper.callAfter(application.terminate_, None),
            )
            controller.refreshFocus_(None)
            application.run()
        finally:
            server.stop()
        return 0
    except Exception:
        traceback.print_exc()
        return 0


def _place_panel(position_path: str) -> tuple[float, float]:
    from AppKit import NSScreen

    screen = NSScreen.mainScreen()
    frame = screen.visibleFrame() if screen is not None else None
    if frame is None:
        return 40.0, 40.0
    origin_x = float(frame.origin.x)
    origin_y = float(frame.origin.y)
    width = float(frame.size.width)
    height = float(frame.size.height)
    saved = load_position(position_path)
    if saved is None:
        return default_position(origin_x, origin_y, width, height, PANEL_W, PANEL_H)
    return clamp_position(
        saved[0], saved[1], origin_x, origin_y, width, height, PANEL_W, PANEL_H
    )
