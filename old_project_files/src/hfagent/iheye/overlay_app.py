"""macOS overlay: a borderless face, a hover chat card, and completion notices.

AppKit is imported inside ``run_overlay`` so this module can be imported on
Linux and in tests. The helper process is the only caller.

Mouse tracking uses ``NSEvent.mouseLocation``, which needs no permission.
Keyboard glances use a global key monitor that only records activity. If
Accessibility permission is missing, the monitor is skipped and a hint is
printed; the pupil still follows the pointer.
"""

from __future__ import annotations

import math
import os
import queue
import sys
import tempfile
import threading
import time
import traceback

from .appearance import eye_geometry
from .face import describe_face, face_pose, point_in_face
from .focus import FrontApp, activation_plan, eye_should_show, same_host
from .host import HostInfo, front_tty_script, run_osascript
from .lifecycle import helper_should_exit, pid_alive, watch_parent_exit_async
from .look import (
    FrontTarget,
    capture_front_native,
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
    should_restore_front,
    step_hover,
    step_summary_timer,
)
from .position import clamp_position, default_position, drop_origin, load_position, save_position
from .protocol import EyeServer
from .robot import (
    ASSETS,
    CLASSIC_H,
    CLASSIC_W,
    assets_ready,
    describe_frame,
    load_layout,
    normalize_skin,
    panel_size,
    point_in_frame,
    resolve_skin,
    robot_pose,
)
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

PANEL_W = CLASSIC_W
PANEL_H = CLASSIC_H


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
            NSCompositingOperationSourceOver,
            NSEvent,
            NSGraphicsContext,
            NSImage,
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
            NSZeroRect,
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
            self._skin = "grok"
            self._speaking = False
            self._layout = None
            self._frame_image = None
            self._iris_image = None
            return self

        def isOpaque(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def setEyeState_(self, state):
            self._state = str(state)
            self.setNeedsDisplay_(True)

        @objc.python_method
        def set_speaking(self, speaking):
            flag = bool(speaking)
            if flag == self._speaking:
                return
            self._speaking = flag
            self.setNeedsDisplay_(True)

        @objc.python_method
        def set_motion(self, phase, pupil, blink_extra):
            self._phase = float(phase)
            self._pupil = None if pupil is None else (float(pupil[0]), float(pupil[1]))
            self._blink_extra = float(blink_extra or 0.0)
            self.setNeedsDisplay_(True)

        def hitTest_(self, point):
            local = self.convertPoint_fromView_(point, self.superview())
            bounds = self.bounds()
            x, y = float(local.x), float(local.y)
            width, height = float(bounds.size.width), float(bounds.size.height)
            if self._skin == "robot" and self._layout:
                outline = self._layout.get("outline") or []
                if outline and not point_in_frame(x, y, width, height, outline):
                    return None
            elif self._skin == "grok" and not point_in_face(x, y, width, height):
                return None
            return objc.super(EyeView, self).hitTest_(point)

        @objc.python_method
        def use_skin(self, skin):
            self._skin = normalize_skin(skin)
            self._speaking = False
            if self._skin != "robot":
                self._layout = None
                self.setNeedsDisplay_(True)
                return
            self._layout = load_layout()
            self._load_art()
            if self._frame_image is None or self._iris_image is None:
                print("iheye: robot art missing; using classic", file=sys.stderr)
                self._skin = "classic"
            # Every skin paints in drawRect_. Hosting a layer tree crashed the
            # robot eye on launch (SIGTRAP), so this view never does that.
            self.setNeedsDisplay_(True)

        @objc.python_method
        def _load_art(self):
            frame = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "frame.png"))
            iris = NSImage.alloc().initWithContentsOfFile_(str(ASSETS / "iris.png"))
            logical = panel_size("robot")
            if frame is not None:
                frame.setSize_((logical[0], logical[1]))
            if iris is not None and self._layout:
                home = self._layout.get("iris") or {}
                iris.setSize_((logical[0] * float(home.get("w", 0.3)), logical[1] * float(home.get("h", 0.2))))
            self._frame_image = frame
            self._iris_image = iris

        @objc.python_method
        def _current_pose(self):
            return robot_pose(
                self._state,
                self._phase,
                pupil=self._pupil,
                blink_extra=self._blink_extra,
                layout=self._layout,
            )

        @objc.python_method
        def _iris_rect(self):
            bounds = self.bounds()
            home = (self._layout or {}).get("iris") or {}
            width = float(bounds.size.width)
            height = float(bounds.size.height)
            w = width * float(home.get("w", 0.36))
            h = height * float(home.get("h", 0.28))
            x = width * float(home.get("x", 0.32))
            # layout y grows downward; AppKit y grows upward
            y = height - (float(home.get("y", 0.36)) + float(home.get("h", 0.28))) * height
            return x, y, w, h

        @objc.python_method
        def _socket_rect(self):
            bounds = self.bounds()
            socket = (self._layout or {}).get("socket") or {}
            width = float(bounds.size.width)
            height = float(bounds.size.height)
            cx = width * float(socket.get("cx", 0.5))
            cy = height * (1.0 - float(socket.get("cy", 0.5)))
            rx = width * float(socket.get("rx", 0.24))
            ry = height * float(socket.get("ry", 0.16))
            return cx, cy, rx, ry

        @objc.python_method
        def _paint_robot(self):
            """Robot eye in drawRect_. Same ops as describe_frame."""
            pose = self._current_pose()
            bounds = self.bounds()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(bounds)
            if self._frame_image is not None:
                self._frame_image.drawInRect_fromRect_operation_fraction_(
                    bounds, NSZeroRect, NSCompositingOperationSourceOver, 1.0
                )
            ctx = NSGraphicsContext.currentContext()
            if ctx is not None:
                ctx.saveGraphicsState()
            cx, cy, rx, ry = self._socket_rect()
            NSBezierPath.bezierPathWithOvalInRect_(
                NSMakeRect(cx - rx, cy - ry, rx * 2, ry * 2)
            ).addClip()
            ix, iy, iw, ih = self._iris_rect()
            if self._iris_image is not None:
                self._iris_image.drawInRect_fromRect_operation_fraction_(
                    NSMakeRect(ix + pose.iris_dx, iy + pose.iris_dy, iw, ih),
                    NSZeroRect,
                    NSCompositingOperationSourceOver,
                    min(1.0, pose.glow),
                )
            center_x = ix + iw / 2.0 + pose.iris_dx
            center_y = iy + ih / 2.0 + pose.iris_dy
            # Rings and the core fit the short axis so a rotating circle stays in the almond.
            minor = max(8.0, min(iw, ih))
            for op in describe_frame(pose):
                if op["op"] == "ring":
                    self._stroke_ring(op, center_x, center_y, minor)
                elif op["op"] == "core":
                    radius = minor * 0.22 * op["scale"]
                    NSColor.colorWithCalibratedWhite_alpha_(1.0, op["alpha"]).set()
                    NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(center_x - radius, center_y - radius, radius * 2, radius * 2)
                    ).fill()
                elif op["op"] == "tint":
                    rgb = op["rgb"]
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(
                        rgb[0], rgb[1], rgb[2], op["alpha"] * 0.55
                    ).set()
                    NSBezierPath.bezierPathWithOvalInRect_(
                        NSMakeRect(center_x - minor * 0.55, center_y - minor * 0.4, minor * 1.1, minor * 0.8)
                    ).fill()
                elif op["op"] == "lids":
                    rgb = op["rgb"]
                    NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], 0.96).set()
                    cover = (ry * 2.0) * op["amount"] * 0.5
                    NSBezierPath.fillRect_(NSMakeRect(cx - rx, cy + ry - cover, rx * 2, cover))
                    NSBezierPath.fillRect_(NSMakeRect(cx - rx, cy - ry, rx * 2, cover))
            if ctx is not None:
                ctx.restoreGraphicsState()

        @objc.python_method
        def _stroke_ring(self, op, cx, cy, side):
            rgba = op["rgba"]
            NSColor.colorWithCalibratedRed_green_blue_alpha_(rgba[0], rgba[1], rgba[2], rgba[3]).set()
            radius = side * 0.5 * op["radius"]
            span = math.tau / max(1, int(op["dashes"]))
            duty = float(op["duty"])
            rotation = float(op["rotation"])
            path = NSBezierPath.alloc().init()
            path.setLineWidth_(float(op["width"]))
            path.setLineCapStyle_(1)
            steps = 3
            for dash in range(int(op["dashes"])):
                a0 = rotation + dash * span
                a1 = a0 + span * duty
                for step in range(steps):
                    t0 = a0 + (a1 - a0) * step / steps
                    t1 = a0 + (a1 - a0) * (step + 1) / steps
                    p0 = NSMakePoint(cx + math.cos(t0) * radius, cy + math.sin(t0) * radius)
                    p1 = NSMakePoint(cx + math.cos(t1) * radius, cy + math.sin(t1) * radius)
                    if step == 0:
                        path.moveToPoint_(p0)
                    path.lineToPoint_(p1)
            path.stroke()

        @objc.python_method
        def _paint_face(self):
            """Grok-style face in drawRect_. Same ops as describe_face."""
            pose = face_pose(
                self._state,
                self._phase,
                pupil=self._pupil,
                blink_extra=self._blink_extra,
                speaking=self._speaking,
            )
            bounds = self.bounds()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(bounds)
            for op in describe_face(pose, float(bounds.size.width), float(bounds.size.height)):
                self._paint_face_op(op)

        @objc.python_method
        def _paint_face_op(self, op):
            kind = op.get("op")
            rgb = op.get("rgb") or (1.0, 1.0, 1.0)
            alpha = float(op.get("alpha", 1.0))
            color = NSColor.colorWithCalibratedRed_green_blue_alpha_(rgb[0], rgb[1], rgb[2], alpha)
            ctx = NSGraphicsContext.currentContext()
            clipped = kind in {"sheen", "shade"} and ctx is not None
            if clipped:
                ctx.saveGraphicsState()
                clip = op.get("clip") or (op["cx"], op["cy"])
                clip_r = float(op.get("clip_r") or 1.0)
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(clip[0] - clip_r, clip[1] - clip_r, clip_r * 2.0, clip_r * 2.0)
                ).addClip()
            color.set()
            if kind == "disc":
                r = float(op["r"])
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(op["cx"] - r, op["cy"] - r, r * 2.0, r * 2.0)
                ).fill()
            elif kind == "rim":
                r = float(op["r"])
                path = NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(op["cx"] - r, op["cy"] - r, r * 2.0, r * 2.0)
                )
                path.setLineWidth_(float(op.get("width") or 2.0))
                path.stroke()
            elif kind == "eye":
                rx, ry = float(op["rx"]), float(op["ry"])
                radius = min(rx, ry)
                NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                    NSMakeRect(op["cx"] - rx, op["cy"] - ry, rx * 2.0, ry * 2.0),
                    radius,
                    radius,
                ).fill()
            elif kind in {"pupil", "catch", "sheen", "shade", "mouth_blob"}:
                if kind in {"pupil", "catch"}:
                    r = float(op["r"])
                    rect = NSMakeRect(op["cx"] - r, op["cy"] - r, r * 2.0, r * 2.0)
                else:
                    rx, ry = float(op["rx"]), float(op["ry"])
                    rect = NSMakeRect(op["cx"] - rx, op["cy"] - ry, rx * 2.0, ry * 2.0)
                NSBezierPath.bezierPathWithOvalInRect_(rect).fill()
            elif kind == "mouth_arc":
                path = NSBezierPath.alloc().init()
                path.setLineWidth_(float(op.get("width") or 2.4))
                path.setLineCapStyle_(1)
                path.appendBezierPathWithArcWithCenter_radius_startAngle_endAngle_clockwise_(
                    NSMakePoint(op["cx"], op["cy"]),
                    float(op["radius"]),
                    float(op["start"]),
                    float(op["end"]),
                    False,
                )
                path.stroke()
            elif kind == "mouth_line":
                path = NSBezierPath.alloc().init()
                path.setLineWidth_(float(op.get("width") or 2.4))
                path.setLineCapStyle_(1)
                path.moveToPoint_(NSMakePoint(op["x0"], op["y0"]))
                path.curveToPoint_controlPoint1_controlPoint2_(
                    NSMakePoint(op["x2"], op["y2"]),
                    NSMakePoint(op["x1"], op["y1"]),
                    NSMakePoint(op["x1"], op["y1"]),
                )
                path.stroke()
            if clipped:
                ctx.restoreGraphicsState()

        def drawRect_(self, _dirty):
            if self._skin == "robot":
                self._paint_robot()
                return
            if self._skin == "grok":
                self._paint_face()
                return
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
            self.look_button = None
            self.note = None
            self.server = None
            self.host = host
            self.querying = False
            self.quit_requested = False
            self.parent_pid = int(args.parent_pid)
            self.track = str(getattr(args, "track", "1")) != "0"
            self.summary_enabled = str(getattr(args, "summary", "1")) != "0"
            self.look_enabled = str(getattr(args, "look", "1")) != "0"
            self.look_pin = None
            self.skin = skin
            self.panel_w, self.panel_h = eye_w, eye_h
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

        @objc.python_method
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
            if self.view is not None and ("state" in msg or "reply" in msg):
                talking = self._state == "thinking" and bool(str(self.reply_text or "").strip())
                self.view.set_speaking(talking)
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
            if "look_enabled" in msg:
                self.look_enabled = bool(msg.get("look_enabled"))
                self._refresh_look_button()
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
                cx = float(eye_frame.origin.x) + self.panel_w / 2.0
                if self.skin == "classic":
                    cy = float(eye_frame.origin.y) + 58.0
                else:
                    cy = float(eye_frame.origin.y) + self.panel_h / 2.0
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
                self.view.set_motion(phase, pupil, blink)
            interval = tick_interval(
                visible=self._eye_visible,
                state=self._state,
                phase=phase,
                tracking=self.track,
                pointer_near_eye=near,
                mouse_moved=mouse_moved,
                typing=typing and self.track,
                skin=self.skin,
            )
            self._arm(interval)

        @objc.python_method
        def _arm(self, interval):
            if self._timer is not None and abs(interval - self._interval) < 0.004:
                return
            if self._timer is not None:
                self._timer.invalidate()
            self._interval = interval
            self._timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
                interval, self, "animTick:", None, True
            )

        @objc.python_method
        def _remember_front(self):
            if self.drop is not None and self.drop.isKeyWindow():
                return
            app = NSWorkspace.sharedWorkspace().frontmostApplication()
            if app is None:
                return
            if int(app.processIdentifier()) == os.getpid():
                return
            self._previous_app = app

        @objc.python_method
        def _restore_front(self):
            if self.drop is not None:
                self.drop.makeFirstResponder_(None)
            prev = self._previous_app
            front = NSWorkspace.sharedWorkspace().frontmostApplication()
            front_pid = int(front.processIdentifier()) if front is not None else 0
            prev_pid = int(prev.processIdentifier()) if prev is not None else None
            if should_restore_front(front_pid, os.getpid(), prev_pid) and prev is not None:
                prev.activateWithOptions_(NSApplicationActivateIgnoringOtherApps)

        @objc.python_method
        def _approval_token(self):
            if not isinstance(self.approval, dict):
                return None
            return (str(self.approval.get("tool") or ""), str(self.approval.get("preview") or ""))

        @objc.python_method
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

        @objc.python_method
        def _place_drop(self):
            if self.panel is None or self.drop is None or self._mode == "closed":
                return
            width, height = drop_size(self._mode)
            eye = self.panel.frame()
            sx, sy, sw, sh = _screen_rect()
            x, y = drop_origin(
                float(eye.origin.x),
                float(eye.origin.y),
                self.panel_w,
                self.panel_h,
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
            _place_view(self.look_button, frames.get("look"))
            _place_view(self.note, frames.get("note"))
            _place_view(self.yes, frames.get("yes"))
            _place_view(self.no, frames.get("no"))

        @objc.python_method
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

        @objc.python_method
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
            self._look_then_send(text)

        def toggleLook_(self, _sender):
            self._cycle_look()

        @objc.python_method
        def _should_look(self, text):
            return should_capture(
                text,
                enabled=bool(self.look_enabled),
                pin=self.look_pin,
            )

        @objc.python_method
        def _capture_target(self):
            front = _frontmost()
            if front is not None and int(front.pid) != os.getpid():
                return FrontTarget(front.pid, front.name, front.bundle_id)
            prev = self._previous_app
            if prev is not None:
                try:
                    return FrontTarget(
                        int(prev.processIdentifier()),
                        str(prev.localizedName() or ""),
                        str(prev.bundleIdentifier() or ""),
                    )
                except Exception:
                    return None
            return None

        @objc.python_method
        def _look_then_send(self, text):
            """Capture only for this send. Never from the animation timer."""
            if not self._should_look(text):
                self._deliver_chat(text, None)
                return
            target = self._capture_target()
            if target is None:
                self._deliver_chat(text, None)
                return
            if self.field is not None:
                self.field.setEnabled_(False)
            image_path = os.path.join(
                tempfile.gettempdir(),
                f"hfagent-iheye-look-{os.getpid()}.png",
            )

            def work(target=target, image_path=image_path, text=text):
                capture = None
                try:
                    capture = capture_front_native(
                        target,
                        self.host,
                        image_path,
                        helper_pid=os.getpid(),
                    )
                except Exception:
                    capture = None
                AppHelper.callAfter(self._deliver_chat, text, capture)

            threading.Thread(target=work, name="iheye-look", daemon=True).start()

        @objc.python_method
        def _deliver_chat(self, text, capture):
            if self.field is not None:
                self.field.setEnabled_(True)
            if self.server is None:
                self._restore_front()
                return
            payload = {"cmd": "chat", "text": text}
            indicator = ""
            if capture is not None:
                hint = str(getattr(capture, "hint", "") or "").strip()
                if hint:
                    print(hint, file=sys.stderr)
                    self.server.send({"cmd": "hint", "text": hint})
                public = capture.public() if hasattr(capture, "public") else {}
                if public and not public.get("skipped"):
                    payload["look"] = public
                indicator = capture_indicator(capture)
            if self.note is not None:
                self.note.setStringValue_(indicator)
            self.server.send(payload)
            self._restore_front()

        @objc.python_method
        def _cycle_look(self):
            if not self.look_enabled:
                self._refresh_look_button()
                return
            self.look_pin = cycle_look_pin(self.look_pin)
            self._refresh_look_button()

        @objc.python_method
        def _refresh_look_button(self):
            if self.look_button is None:
                return
            if not self.look_enabled:
                self.look_button.setTitle_("👁 off")
                return
            self.look_button.setTitle_(look_button_title(self.look_pin))

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

        @objc.python_method
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

        skin = _overlay_skin(args)
        eye_w, eye_h = panel_size(skin)
        x, y = _place_panel(str(args.position), eye_w, eye_h)
        style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel
        behavior = (
            NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorStationary
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorIgnoresCycle
        )
        panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(x, y, eye_w, eye_h),
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
        view = EyeView.alloc().initWithFrame_(NSMakeRect(0, 0, eye_w, eye_h))
        view.use_skin(skin)
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
        look = _button("👁", "toggleLook:")
        look.setToolTip_("Use what I'm viewing")
        look.setFont_(NSFont.systemFontOfSize_(12))
        note = _label()
        note.setFont_(NSFont.systemFontOfSize_(11))
        note.setTextColor_(NSColor.colorWithCalibratedWhite_alpha_(0.72, 1))
        drop_view.addSubview_(reply)
        drop_view.addSubview_(field)
        drop_view.addSubview_(look)
        drop_view.addSubview_(note)
        drop_view.addSubview_(yes)
        drop_view.addSubview_(no)
        drop.setContentView_(drop_view)
        controller.drop = drop
        controller.drop_view = drop_view
        controller.reply = reply
        controller.field = field
        controller.yes = yes
        controller.no = no
        controller.look_button = look
        controller.note = note
        controller._refresh_look_button()
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


def _overlay_skin(args) -> str:
    skin = resolve_skin(getattr(args, "skin", None))
    if skin == "robot" and not assets_ready():
        print("iheye: robot skin assets missing; using classic", file=sys.stderr)
        return "classic"
    return skin


def _place_panel(position_path: str, eye_w: float, eye_h: float) -> tuple[float, float]:
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
        return default_position(origin_x, origin_y, width, height, eye_w, eye_h)
    return clamp_position(
        saved[0], saved[1], origin_x, origin_y, width, height, eye_w, eye_h
    )
