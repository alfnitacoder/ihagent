"""macOS overlay: a borderless eye, a hover chat card, and completion notices.

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
    METAL_LID,
    assets_ready,
    describe_frame,
    load_layout,
    normalize_skin,
    panel_size,
    point_in_frame,
    resolve_skin,
    ring_specs,
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
            NSAffineTransform,
            NSBackingStoreBuffered,
            NSBezierPath,
            NSBitmapImageRep,
            NSButton,
            NSCalibratedRGBColorSpace,
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
            self._skin = "classic"
            self._layout = None
            self._layer_mode = False
            self._ring_cache = {}
            self._core_contents = None
            self._frame_image = None
            self._iris_image = None
            self._anim_state = None
            self._layers = None
            return self

        def isOpaque(self):
            return False

        def acceptsFirstMouse_(self, _event):
            return True

        def setEyeState_(self, state):
            self._state = str(state)
            if self._skin == "robot":
                self._robot_refresh(True)
            else:
                self.setNeedsDisplay_(True)

        @objc.python_method
        def set_motion(self, phase, pupil, blink_extra):
            self._phase = float(phase)
            self._pupil = None if pupil is None else (float(pupil[0]), float(pupil[1]))
            self._blink_extra = float(blink_extra or 0.0)
            if self._skin == "robot":
                self._robot_refresh(False)
            else:
                self.setNeedsDisplay_(True)

        def hitTest_(self, point):
            if self._skin == "robot" and self._layout:
                local = self.convertPoint_fromView_(point, self.superview())
                bounds = self.bounds()
                outline = self._layout.get("outline") or []
                if outline and not point_in_frame(
                    float(local.x),
                    float(local.y),
                    float(bounds.size.width),
                    float(bounds.size.height),
                    outline,
                ):
                    return None
            return objc.super(EyeView, self).hitTest_(point)

        @objc.python_method
        def use_skin(self, skin):
            self._skin = normalize_skin(skin)
            self._layer_mode = False
            self._layers = None
            if self._skin != "robot":
                self.setNeedsDisplay_(True)
                return
            self._layout = load_layout()
            self._load_art()
            if self._frame_image is None or self._iris_image is None:
                print("iheye: robot art missing; using classic", file=sys.stderr)
                self._skin = "classic"
                self.setNeedsDisplay_(True)
                return
            # CALayer hosting aborts the helper on some macOS/PyObjC builds.
            # Paint the robot skin in drawRect_ instead; motion still works.
            self._layer_mode = False
            self._layers = None
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
        def _robot_refresh(self, state_changed):
            if self._layout is None:
                return
            pose = self._current_pose()
            if self._layer_mode and self._layers is not None:
                changed = state_changed or pose.state != self._anim_state
                self._sync_layers(pose, changed)
                self._anim_state = pose.state
                return
            self.setNeedsDisplay_(True)

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
        def _install_layers(self):
            from Quartz import (
                CABasicAnimation,
                CALayer,
                CAShapeLayer,
                CGColorCreateGenericRGB,
                CGPathAddEllipseInRect,
                CGPathCreateMutable,
                CGRectMake,
            )

            self._quartz = {
                "CABasicAnimation": CABasicAnimation,
                "CGColorCreateGenericRGB": CGColorCreateGenericRGB,
            }
            bounds = self.bounds()
            width = float(bounds.size.width)
            height = float(bounds.size.height)
            root = CALayer.layer()
            root.setFrame_(NSMakeRect(0, 0, width, height))
            root.setContentsScale_(2.0)
            frame_layer = CALayer.layer()
            frame_layer.setFrame_(NSMakeRect(0, 0, width, height))
            frame_layer.setContentsScale_(2.0)
            frame_cg = self._cgimage(self._frame_image)
            iris_cg = self._cgimage(self._iris_image)
            if frame_cg is None or iris_cg is None:
                raise RuntimeError("robot art could not be decoded")
            frame_layer.setContents_(frame_cg)
            root.addSublayer_(frame_layer)

            socket = CALayer.layer()
            socket.setFrame_(NSMakeRect(0, 0, width, height))
            cx, cy, rx, ry = self._socket_rect()
            mask = CAShapeLayer.layer()
            path = CGPathCreateMutable()
            CGPathAddEllipseInRect(path, None, CGRectMake(cx - rx, cy - ry, rx * 2, ry * 2))
            mask.setPath_(path)
            mask.setFrame_(NSMakeRect(0, 0, width, height))
            socket.setMask_(mask)

            ix, iy, iw, ih = self._iris_rect()
            holder = CALayer.layer()
            holder.setBounds_(NSMakeRect(0, 0, max(iw, ih), max(iw, ih)))
            holder.setPosition_((ix + iw / 2.0, iy + ih / 2.0))
            holder.setContentsScale_(2.0)

            glow = CALayer.layer()
            glow.setBounds_(NSMakeRect(0, 0, iw, ih))
            glow.setPosition_((max(iw, ih) / 2.0, max(iw, ih) / 2.0))
            glow.setContentsScale_(2.0)
            glow.setContents_(iris_cg)
            holder.addSublayer_(glow)

            ring_layers = []
            ring_side = max(8.0, min(iw, ih))
            holder_side = max(iw, ih)
            for index, spec in enumerate(ring_specs("idle")):
                ring = CALayer.layer()
                ring.setBounds_(NSMakeRect(0, 0, ring_side, ring_side))
                ring.setPosition_((holder_side / 2.0, holder_side / 2.0))
                ring.setContentsScale_(2.0)
                holder.addSublayer_(ring)
                ring_layers.append((ring, index, spec))

            core = CALayer.layer()
            core_side = max(8.0, min(iw, ih) * 0.42)
            core.setBounds_(NSMakeRect(0, 0, core_side, core_side))
            core.setPosition_((max(iw, ih) / 2.0, max(iw, ih) / 2.0))
            core.setContentsScale_(2.0)
            core.setContents_(self._core_contents_image(core_side))
            holder.addSublayer_(core)

            tint = CALayer.layer()
            tint.setBounds_(NSMakeRect(0, 0, iw, ih))
            tint.setPosition_((max(iw, ih) / 2.0, max(iw, ih) / 2.0))
            tint.setCornerRadius_(min(iw, ih) / 2.0)
            holder.addSublayer_(tint)
            socket.addSublayer_(holder)

            lid_top = CALayer.layer()
            lid_bottom = CALayer.layer()
            color = CGColorCreateGenericRGB(METAL_LID[0], METAL_LID[1], METAL_LID[2], 1.0)
            for lid in (lid_top, lid_bottom):
                lid.setBackgroundColor_(color)
                lid.setOpacity_(0.0)
                socket.addSublayer_(lid)
            root.addSublayer_(socket)

            self.setLayer_(root)
            self.setWantsLayer_(True)
            # NSViewLayerContentsRedrawNever — the layers animate; drawRect stays idle.
            self.setLayerContentsRedrawPolicy_(0)
            self._layers = {
                "holder": holder,
                "rings": ring_layers,
                "core": core,
                "tint": tint,
                "lid_top": lid_top,
                "lid_bottom": lid_bottom,
                "home": (ix + iw / 2.0, iy + ih / 2.0),
                "socket": (cx, cy, rx, ry),
            }
            self._layer_mode = True

        @objc.python_method
        def _cgimage(self, image):
            if image is None:
                return None
            return image.CGImageForProposedRect_context_hints_(None, None, None)

        @objc.python_method
        def _core_contents_image(self, side):
            if self._core_contents is not None:
                return self._core_contents
            pixels = max(16, int(side * 2))
            rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, pixels, pixels, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
            )
            ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(ctx)
            transform = NSAffineTransform.transform()
            transform.scaleBy_(2.0)
            transform.concat()
            center = side / 2.0
            for radius, alpha in ((side * 0.48, 0.18), (side * 0.28, 0.45), (side * 0.12, 0.95)):
                NSColor.colorWithCalibratedWhite_alpha_(1.0, alpha).set()
                NSBezierPath.bezierPathWithOvalInRect_(
                    NSMakeRect(center - radius, center - radius, radius * 2, radius * 2)
                ).fill()
            NSGraphicsContext.restoreGraphicsState()
            image = NSImage.alloc().initWithSize_((side, side))
            image.addRepresentation_(rep)
            self._core_contents = image.CGImageForProposedRect_context_hints_(None, None, None)
            return self._core_contents

        @objc.python_method
        def _ring_contents(self, state, index, spec, side):
            key = (state, index)
            cached = self._ring_cache.get(key)
            if cached is not None:
                return cached
            pixels = max(16, int(side * 2))
            rep = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
                None, pixels, pixels, 8, 4, True, False, NSCalibratedRGBColorSpace, 0, 0
            )
            ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.setCurrentContext_(ctx)
            transform = NSAffineTransform.transform()
            transform.scaleBy_(2.0)
            transform.concat()
            NSColor.clearColor().set()
            NSBezierPath.fillRect_(NSMakeRect(0, 0, side, side))
            rgba = spec.rgba
            NSColor.colorWithCalibratedRed_green_blue_alpha_(rgba[0], rgba[1], rgba[2], rgba[3]).set()
            radius = side * 0.5 * spec.radius
            cx = cy = side / 2.0
            span = math.tau / spec.dashes
            path = NSBezierPath.alloc().init()
            path.setLineWidth_(spec.width)
            path.setLineCapStyle_(1)
            steps = 3
            for dash in range(spec.dashes):
                a0 = dash * span
                a1 = a0 + span * spec.duty
                for step in range(steps):
                    t0 = a0 + (a1 - a0) * step / steps
                    t1 = a0 + (a1 - a0) * (step + 1) / steps
                    p0 = NSMakePoint(cx + math.cos(t0) * radius, cy + math.sin(t0) * radius)
                    p1 = NSMakePoint(cx + math.cos(t1) * radius, cy + math.sin(t1) * radius)
                    if step == 0:
                        path.moveToPoint_(p0)
                    path.lineToPoint_(p1)
            path.stroke()
            NSGraphicsContext.restoreGraphicsState()
            image = NSImage.alloc().initWithSize_((side, side))
            image.addRepresentation_(rep)
            contents = image.CGImageForProposedRect_context_hints_(None, None, None)
            self._ring_cache[key] = contents
            return contents

        @objc.python_method
        def _sync_layers(self, pose, state_changed):
            layers = self._layers
            quartz = self._quartz or {}
            home_x, home_y = layers["home"]
            layers["holder"].setPosition_((home_x + pose.iris_dx, home_y + pose.iris_dy))
            self._place_lids(pose)
            if not state_changed:
                return
            color = quartz["CGColorCreateGenericRGB"]
            tint = layers["tint"]
            rgb = pose.tint_rgb
            tint.setBackgroundColor_(color(rgb[0], rgb[1], rgb[2], 1.0))
            tint.setOpacity_(pose.tint_alpha)
            layers["core"].setOpacity_(min(1.0, pose.breath * pose.glow))
            self._animate_core(pose)
            ix, iy, iw, ih = self._iris_rect()
            ring_side = max(8.0, min(iw, ih))
            for ring, index, _old in layers["rings"]:
                spec = ring_specs(pose.state)[index]
                contents = self._ring_contents(pose.state, index, spec, ring_side)
                if contents is not None:
                    ring.setContents_(contents)
                self._animate_spin(ring, pose.spin_period / max(0.2, abs(spec.spin)), spec.spin < 0)

        @objc.python_method
        def _animate_spin(self, layer, period, reverse):
            quartz = self._quartz or {}
            animation = quartz["CABasicAnimation"].animationWithKeyPath_("transform.rotation.z")
            turn = math.tau
            animation.setFromValue_(0.0 if not reverse else turn)
            animation.setToValue_(-turn if reverse else turn)
            animation.setDuration_(max(0.4, float(period)))
            animation.setRepeatCount_(1e6)
            animation.setRemovedOnCompletion_(False)
            layer.addAnimation_forKey_(animation, "spin")

        @objc.python_method
        def _animate_core(self, pose):
            quartz = self._quartz or {}
            animation = quartz["CABasicAnimation"].animationWithKeyPath_("opacity")
            if pose.state == "thinking":
                animation.setFromValue_(0.72)
                animation.setToValue_(1.0)
                animation.setDuration_(0.7)
            else:
                animation.setFromValue_(0.45)
                animation.setToValue_(0.95)
                animation.setDuration_(1.6)
            animation.setAutoreverses_(True)
            animation.setRepeatCount_(1e6)
            animation.setRemovedOnCompletion_(False)
            self._layers["core"].addAnimation_forKey_(animation, "breath")

        @objc.python_method
        def _place_lids(self, pose):
            cx, cy, rx, ry = self._layers["socket"]
            cover = (ry * 2.0) * max(0.0, min(1.0, pose.blink)) * 0.5
            top = self._layers["lid_top"]
            bottom = self._layers["lid_bottom"]
            if cover < 0.4:
                top.setOpacity_(0.0)
                bottom.setOpacity_(0.0)
                return
            top.setOpacity_(1.0)
            bottom.setOpacity_(1.0)
            top.setFrame_(NSMakeRect(cx - rx, cy + ry - cover, rx * 2.0, cover))
            bottom.setFrame_(NSMakeRect(cx - rx, cy - ry, rx * 2.0, cover))

        @objc.python_method
        def _paint_robot(self):
            """Vector fallback when layer hosting is unavailable. Same ops as describe_frame."""
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

        def drawRect_(self, _dirty):
            if self._skin == "robot":
                if not self._layer_mode:
                    self._paint_robot()
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
                if self.skin == "robot":
                    cy = float(eye_frame.origin.y) + self.panel_h / 2.0
                else:
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
