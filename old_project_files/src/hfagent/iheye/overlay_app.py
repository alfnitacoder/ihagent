"""macOS overlay: a borderless eye, a hover chat card, and completion notices.

AppKit is imported inside ``run_overlay`` so this module can be imported on
Linux and in tests. The helper process is the only caller.

Mouse tracking uses ``NSEvent.mouseLocation``, which needs no permission.
Keyboard glances use a global key monitor that only records activity. If
Accessibility permission is missing, the monitor is skipped and a hint is
printed; the pupil still follows the pointer.
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import traceback

from .appearance import eye_geometry
from .focus import FrontApp, activation_plan, eye_should_show, same_host
from .host import HostInfo, front_tty_script, run_osascript
from .lifecycle import helper_should_exit, pid_alive, watch_parent_exit_async
from .panels import (
    SUMMARY_SECONDS,
    approval_body,
    drop_contents,
    drop_size,
    layout_drop,
    pointer_inside,
    should_restore_front,
    step_hover,
    step_summary_timer,
)
from .position import clamp_position, default_position, drop_origin, load_position, save_position
from .protocol import EyeServer
from .supervisor import INSTALL_HINT
from .appearance import pupil_offset
from .tracking import (
    PHASE_PER_SEC,
    gaze_from_points,
    hint_for_monitor,
    key_activity_handler,
    pointer_near,
    tick_interval,
    tracked_pupil,
    typing_reaction,
)

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
            NSButton,
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
            NSTextField,
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
        print(INSTALL_HINT, file=sys.stderr)
        return 0

    key_mask = getattr(NSEvent, "NSEventMaskKeyDown", None)
    if key_mask is None:
        key_mask = 1 << 10

    center = NSMutableParagraphStyle.alloc().init()
    center.setAlignment_(1)  # NSTextAlignmentCenter

    class KeyPanel(NSPanel):
        """Key-capable, but it does not activate this accessory app."""

        def canBecomeKeyWindow(self):
            return True

        def canBecomeMainWindow(self):
            return False

    class ChatField(NSTextField):
        def acceptsFirstMouse_(self, _event):
            return True

        def mouseDown_(self, event):
            window = self.window()
            if window is not None:
                window.makeKeyAndOrderFront_(None)
                window.makeFirstResponder_(self)
            objc.super(ChatField, self).mouseDown_(event)

    class EyeView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(EyeView, self).initWithFrame_(frame)
            if self is None:
                return None
            self._state = "idle"
            self._phase = 0.0
            self._pupil = None
            self._blink_extra = 0.0
            self._drag = None
            self._moved = False
            self.onClick = None
            self.onDrag = None
            self.onDragEnd = None
            return self

        def isOpaque(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def setEyeState_(self, state):
            self._state = str(state)
            self.setNeedsDisplay_(True)

        def setMotion_(self, phase, pupil, blink_extra):
            self._phase = float(phase)
            self._pupil = None if pupil is None else (float(pupil[0]), float(pupil[1]))
            self._blink_extra = float(blink_extra or 0.0)
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
            geo = eye_geometry(
                self._state,
                self._phase,
                cx=PANEL_W / 2,
                cy=58,
                pupil_override=self._pupil,
                blink_extra=self._blink_extra,
            )
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
            if self.onDrag is not None:
                origin = self.window().frame().origin
                self.onDrag(origin.x, origin.y)

        def mouseUp_(self, _event):
            if self._moved and self.onDragEnd is not None:
                origin = self.window().frame().origin
                self.onDragEnd(origin.x, origin.y)
            elif not self._moved and self.onClick is not None:
                self.onClick()
            self._drag = None

    class DropView(NSView):
        def initWithFrame_(self, frame):
            self = objc.super(DropView, self).initWithFrame_(frame)
            if self is None:
                return None
            self.mode = "closed"
            self.onActivate = None
            return self

        def isOpaque(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def drawRect_(self, _dirty):
            bounds = self.bounds()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(bounds)
            card = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSMakeRect(1, 1, bounds.size.width - 2, bounds.size.height - 2),
                14,
                14,
            )
            NSColor.colorWithCalibratedRed_green_blue_alpha_(0.07, 0.10, 0.15, 0.96).set()
            card.fill()

        def hitTest_(self, point):
            if self.mode == "summary":
                return self
            return objc.super(DropView, self).hitTest_(point)

        def mouseDown_(self, event):
            if self.mode != "summary":
                objc.super(DropView, self).mouseDown_(event)

        def mouseUp_(self, _event):
            if self.mode == "summary" and self.onActivate is not None:
                self.onActivate()

    class EyeController(NSObject):
        def init(self):
            self = objc.super(EyeController, self).init()
            if self is None:
                return None
            self.panel = None
            self.drop = None
            self.drop_view = None
            self.view = None
            self.reply = None
            self.field = None
            self.yes = None
            self.no = None
            self.server = None
            self.host = host
            self.querying = False
            self.quit_requested = False
            self.parent_pid = int(args.parent_pid)
            self.track = str(getattr(args, "track", "1")) != "0"
            self.summary_enabled = str(getattr(args, "summary", "1")) != "0"
            self._state = "idle"
            self._state_t0 = time.monotonic()
            self._last_tick = self._state_t0
            self._mx = 0.0
            self._my = 0.0
            self._mouse_moved_until = 0.0
            self.last_key_at = None
            self.hover_open = False
            self._away = 0.0
            self._suppressed = False
            self.approval = None
            self.summary_text = ""
            self._summary_left = 0.0
            self._summary_showing = False
            self.reply_text = ""
            self._mode = "closed"
            self._shown = None
            self._eye_visible = False
            self._previous_app = None
            self._timer = None
            self._interval = 0.0
            self._key_monitor = None
            self._key_failed = False
            self._inbox = queue.Queue()
            return self

        def noteTyping(self):
            self.last_key_at = time.monotonic()

        def checkParent_(self, _timer):
            if helper_should_exit(
                quit_requested=self.quit_requested,
                parent_alive=pid_alive(self.parent_pid),
            ):
                NSApplication.sharedApplication().terminate_(None)

        def enqueue_(self, message):
            self._inbox.put(message)

        def drain_(self, _timer):
            while True:
                try:
                    msg = self._inbox.get_nowait()
                except queue.Empty:
                    return
                self._handle(msg)

        def _handle(self, msg):
            if not isinstance(msg, dict):
                return
            if msg.get("cmd") == "quit":
                self.quit_requested = True
                NSApplication.sharedApplication().terminate_(None)
                return
            changed = False
            if "state" in msg:
                state = str(msg.get("state") or "idle")
                self._state = state
                self._state_t0 = time.monotonic()
                if self.view is not None:
                    self.view.setEyeState_(state)
                if state == "thinking":
                    self.approval = None
                    self.summary_text = ""
                    self._summary_showing = False
                    self.reply_text = ""
                changed = True
            if "reply" in msg:
                self.reply_text = str(msg.get("reply") or "")
                changed = True
            if "summary" in msg:
                text = str(msg.get("summary") or "").strip()
                visible = self.panel is not None and bool(self.panel.isVisible())
                if self.summary_enabled and text and visible:
                    self.summary_text = text
                    self._summary_left = SUMMARY_SECONDS
                    self._summary_showing = True
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
                if self.track:
                    self._key_failed = False
                self.installKeys()
            if "summary_enabled" in msg:
                self.summary_enabled = bool(msg.get("summary_enabled"))
                if not self.summary_enabled:
                    self.summary_text = ""
                    self._summary_showing = False
                changed = True
            if changed:
                self._sync_drop()

        def animTick_(self, _timer):
            now = time.monotonic()
            dt = min(0.25, max(0.0, now - self._last_tick))
            self._last_tick = now
            phase = (now - self._state_t0) * PHASE_PER_SEC
            loc = NSEvent.mouseLocation()
            mx, my = float(loc.x), float(loc.y)
            moved = abs(mx - self._mx) + abs(my - self._my) > 0.8
            self._mx, self._my = mx, my
            if moved:
                self._mouse_moved_until = now + 0.35
            mouse_moved = now < self._mouse_moved_until
            eye_frame = self.panel.frame() if self.panel is not None else None
            if eye_frame is not None:
                cx = float(eye_frame.origin.x) + PANEL_W / 2.0
                cy = float(eye_frame.origin.y) + 58.0
            else:
                cx, cy = mx, my
            near = pointer_near(mx, my, cx, cy)
            age = None if self.last_key_at is None else now - self.last_key_at
            typing = age is not None and age < 1.4
            rects = []
            if eye_frame is not None:
                rects.append(
                    (
                        float(eye_frame.origin.x),
                        float(eye_frame.origin.y),
                        float(eye_frame.size.width),
                        float(eye_frame.size.height),
                    )
                )
            if self.drop is not None and self.drop.isVisible():
                frame = self.drop.frame()
                rects.append(
                    (
                        float(frame.origin.x),
                        float(frame.origin.y),
                        float(frame.size.width),
                        float(frame.size.height),
                    )
                )
            inside = pointer_inside(mx, my, rects)
            focused = self.drop is not None and bool(self.drop.isKeyWindow())
            self.hover_open, self._away, self._suppressed = step_hover(
                open_now=self.hover_open,
                inside=inside,
                focused=focused,
                away_for=self._away,
                suppressed=self._suppressed,
                dt=dt,
            )
            if self.summary_text:
                self._summary_left, self._summary_showing = step_summary_timer(
                    self._summary_left,
                    inside,
                    dt,
                    self._summary_showing,
                )
                if not self._summary_showing:
                    self.summary_text = ""
            self._remember_front()
            self._sync_drop()
            pupil = None
            blink = 0.0
            if self.track and self._eye_visible:
                gaze = gaze_from_points(cx, cy, mx, my)
                offset, blink = typing_reaction(age, phase)
                base = pupil_offset(self._state, phase)
                pupil = tracked_pupil(base, gaze, offset)
            if self.view is not None and self._eye_visible:
                self.view.setMotion_(phase, pupil, blink)
            interval = tick_interval(
                visible=self._eye_visible,
                state=self._state,
                phase=phase,
                tracking=self.track,
                pointer_near_eye=near,
                mouse_moved=mouse_moved,
                typing=typing and self.track,
            )
            self._arm(interval)

        def _arm(self, interval):
            if self._timer is not None and abs(interval - self._interval) < 0.004:
                return
            if self._timer is not None:
                self._timer.invalidate()
            self._interval = interval
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                interval, self, "animTick:", None, True
            )

        def _remember_front(self):
            if self.drop is not None and self.drop.isKeyWindow():
                return
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return
            if int(app.processIdentifier()) == os.getpid():
                return
            self._previous_app = app

        def _restore_front(self):
            if self.drop is not None:
                self.drop.makeFirstResponder_(None)
            prev = self._previous_app
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            front_pid = int(front.processIdentifier()) if front is not None else 0
            prev_pid = int(prev.processIdentifier()) if prev is not None else None
            if should_restore_front(front_pid, os.getpid(), prev_pid) and prev is not None:
                prev.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

        def _approval_token(self):
            if not isinstance(self.approval, dict):
                return None
            return (str(self.approval.get("tool") or ""), str(self.approval.get("preview") or ""))

        def _sync_drop(self):
            if self.drop is None:
                return
            if not self._eye_visible:
                if self.drop.isVisible():
                    self.drop.orderOut_(None)
                self._mode = "closed"
                self._shown = None
                return
            mode = drop_contents(
                hover=self.hover_open,
                approval=self.approval is not None,
                summary=bool(self.summary_text) and self._summary_showing and self.summary_enabled,
            )
            if mode == "closed":
                if self.drop.isVisible():
                    self.drop.orderOut_(None)
                self._mode = "closed"
                self._shown = None
                return
            if mode != self._mode or not self.drop.isVisible():
                self._mode = mode
                self._place_drop()
                self._shown = None
                self.drop.orderFrontRegardless()
            self._mode = mode
            text_token = (self.reply_text, self.summary_text, self._approval_token())
            if text_token != self._shown:
                self._shown = text_token
                self._fill_drop(mode)

        def _place_drop(self):
            if self.panel is None or self.drop is None or self._mode == "closed":
                return
            width, height = drop_size(self._mode)
            eye = self.panel.frame()
            sx, sy, sw, sh = _screen_rect()
            x, y = drop_origin(
                float(eye.origin.x),
                float(eye.origin.y),
                PANEL_W,
                PANEL_H,
                width,
                height,
                sx,
                sy,
                sw,
                sh,
            )
            self.drop.setFrame_display_(NSMakeRect(x, y, width, height), True)
            if self.drop_view is not None:
                self.drop_view.setFrame_(NSMakeRect(0, 0, width, height))
            frames = layout_drop(self._mode, width, height)
            _place_view(self.reply, frames.get("reply"))
            _place_view(self.field, frames.get("field"))
            _place_view(self.yes, frames.get("yes"))
            _place_view(self.no, frames.get("no"))

        def _fill_drop(self, mode):
            if self.drop_view is not None:
                self.drop_view.mode = mode
            if self.reply is None:
                return
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
            self.reply.setStringValue_(body)

        def followDrag(self):
            if self._mode != "closed":
                self._place_drop()

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
                self._eye_visible = True
                # Wake the redraw loop so gaze and hover are not stuck on the hidden interval.
                self._arm(1.0 / 30.0)
            else:
                self.panel.orderOut_(None)
                self._eye_visible = False
                self.hover_open = False
            self._sync_drop()

        def focusHost_(self, _sender):
            plan = activation_plan(self.host)

            def work():
                if plan.applescript:
                    run_osascript(plan.applescript, timeout=1.5)
                _activate(plan)

            threading.Thread(target=work, name="iheye-focus", daemon=True).start()

        def sendChat_(self, _sender):
            if self.field is None or self.server is None:
                return
            text = str(self.field.stringValue() or "").strip()
            if not text:
                return
            self.field.setStringValue_("")
            self.server.send({"cmd": "chat", "text": text})
            self._restore_front()

        def dismissHover_(self, _sender):
            self._suppressed = True
            self.hover_open = False
            self._restore_front()
            self._sync_drop()

        def control_textView_doCommandBySelector_(self, _control, _text_view, command):
            if str(command) == "cancelOperation:":
                self.dismissHover_(None)
                return True
            return False

        def approveYes_(self, _sender):
            self._answer_approval(True)

        def approveNo_(self, _sender):
            self._answer_approval(False)

        def _answer_approval(self, yes):
            if self.server is not None:
                self.server.send({"cmd": "approve", "yes": bool(yes)})
            self.approval = None
            self._sync_drop()

        def installKeys(self):
            if not self.track:
                self.removeKeys()
                return
            if self._key_monitor is not None or self._key_failed:
                return
            token = None
            try:
                token = NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(
                    key_mask,
                    key_activity_handler(self.noteTyping),
                )
            except Exception:
                token = None
            hint = hint_for_monitor(token)
            if hint:
                self._key_failed = True
                print(hint, file=sys.stderr)
                if self.server is not None:
                    self.server.send({"cmd": "hint", "text": hint})
                return
            self._key_monitor = token

        def removeKeys(self):
            token = self._key_monitor
            self._key_monitor = None
            if token is None:
                return
            try:
                NSEvent.removeMonitor_(token)
            except Exception:
                pass

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

    def _screen_rect():
        screen = NSScreen.mainScreen()
        frame = screen.visibleFrame() if screen is not None else None
        if frame is None:
            return 0.0, 0.0, 1440.0, 900.0
        return (
            float(frame.origin.x),
            float(frame.origin.y),
            float(frame.size.width),
            float(frame.size.height),
        )

    def _place_view(view, rect):
        if view is None:
            return
        if rect is None:
            view.setHidden_(True)
            return
        view.setHidden_(False)
        view.setFrame_(NSMakeRect(rect.x, rect.y, rect.w, rect.h))

    def _label():
        field = NSTextField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        field.setEditable_(False)
        field.setSelectable_(False)
        field.setBezeled_(False)
        field.setBordered_(False)
        field.setDrawsBackground_(False)
        field.setUsesSingleLineMode_(False)
        field.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.95, 1))
        field.setFont_(NSFont.systemFontOfSize_(12))
        cell = field.cell()
        cell.setWraps_(True)
        cell.setScrollable_(False)
        cell.setLineBreakMode_(0)
        return field

    def _button(title, action):
        button = NSButton.alloc().initWithFrame_(NSMakeRect(0, 0, 72, 26))
        button.setTitle_(title)
        button.setBezelStyle_(1)
        button.setFont_(NSFont.boldSystemFontOfSize_(13))
        button.setTarget_(controller)
        button.setAction_(action)
        return button

    try:
        application = NSApplication.sharedApplication()
        application.setActivationPolicy_(NSApplicationActivationPolicyAccessory)

        x, y = _place_panel(str(args.position))
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, PANEL_W, PANEL_H),
            style,
            NSBackingStoreBuffered,
            False,
        )
        panel.setLevel_(NSFloatingWindowLevel)
        panel.setCollectionBehavior_(behavior)
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
        view.onDrag = lambda _px, _py: controller.followDrag()
        view.onDragEnd = lambda px, py: save_position(args.position, px, py)
        panel.setContentView_(view)
        controller.panel = panel
        controller.view = view

        drop_w, drop_h = drop_size("chat")
        drop = KeyPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y - drop_h - 6, drop_w, drop_h),
            style,
            NSBackingStoreBuffered,
            False,
        )
        drop.setLevel_(NSFloatingWindowLevel)
        drop.setCollectionBehavior_(behavior)
        drop.setFloatingPanel_(True)
        drop.setHidesOnDeactivate_(False)
        drop.setCanHide_(False)
        drop.setOpaque_(False)
        drop.setBackgroundColor_(NSColor.clearColor())
        drop.setHasShadow_(True)
        drop.setBecomesKeyOnlyIfNeeded_(True)
        drop.setWorksWhenModal_(True)
        drop.setMovableByWindowBackground_(False)
        drop_view = DropView.alloc().initWithFrame_(NSMakeRect(0, 0, drop_w, drop_h))
        drop_view.onActivate = lambda: controller.focusHost_(None)
        reply = _label()
        field = ChatField.alloc().initWithFrame_(NSMakeRect(0, 0, 10, 10))
        field.setEditable_(True)
        field.setBezeled_(True)
        field.setBezelStyle_(1)
        field.setUsesSingleLineMode_(True)
        field.setFont_(NSFont.systemFontOfSize_(13))
        field.setPlaceholderString_("Message ihagent")
        field.setTarget_(controller)
        field.setAction_("sendChat:")
        field.setDelegate_(controller)
        yes = _button("y", "approveYes:")
        no = _button("N", "approveNo:")
        drop_view.addSubview_(reply)
        drop_view.addSubview_(field)
        drop_view.addSubview_(yes)
        drop_view.addSubview_(no)
        drop.setContentView_(drop_view)
        controller.drop = drop
        controller.drop_view = drop_view
        controller.reply = reply
        controller.field = field
        controller.yes = yes
        controller.no = no
        panel.orderOut_(None)
        drop.orderOut_(None)

        watcher = Watcher.alloc().initWithController_(controller)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            watcher,
            "appActivated:",
            "NSWorkspaceDidActivateApplicationNotification",
            None,
        )
        controller.watcher = watcher

        def on_message(msg):
            controller.enqueue_(msg)

        server = EyeServer(str(args.socket), on_message)
        controller.server = server
        server.start()
        try:
            controller.installKeys()
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.1, controller, "drain:", None, True
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.5, controller, "checkParent:", None, True
            )
            NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                0.4, controller, "refreshFocus:", None, True
            )
            controller._arm(0.5)
            watch_parent_exit_async(
                int(args.parent_pid),
                lambda: AppHelper.callAfter(application.terminate_, None),
            )
            controller.refreshFocus_(None)
            application.run()
        finally:
            try:
                controller.removeKeys()
            except Exception:
                pass
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
