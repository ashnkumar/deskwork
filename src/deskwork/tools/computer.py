"""The computer tool: screenshots in, X11 input out.

Implements `computer_20251124` against a Linux X display using `xdotool` and `scrot`. The
reference implementation this repo replaces drove the developer's macOS host through
`cliclick` and `screencapture`, which is neither reproducible nor containerisable.

The one design decision worth copying: **the X display is created at exactly the resolution
reported to the API**, so screenshots are never rescaled and coordinates map 1:1. The
reference carried a `scale_coordinates()` helper that converted between a Retina display and
a 1024x768 target in both directions — a permanent source of off-by-a-scale-factor bugs.
Sizing the display correctly deletes the problem instead of managing it.
"""

from __future__ import annotations

import base64
import io
import os
import shlex
import subprocess
import time
from dataclasses import dataclass

from PIL import Image

from .base import ToolResult

TOOL_TYPE = "computer_20251124"
BETA_FLAG = "computer-use-2025-11-24"

# xdotool's `type` sends keystrokes; too fast and applications drop characters.
TYPE_DELAY_MS = 12
# Give the UI a beat to repaint before the screenshot that follows an action.
SETTLE_SECONDS = 0.4

CLICK_BUTTONS = {
    "left_click": 1,
    "middle_click": 2,
    "right_click": 3,
}
SCROLL_BUTTONS = {"up": 4, "down": 5, "left": 6, "right": 7}


class ComputerToolError(Exception):
    """Raised for a malformed action. Reported back to the model, never fatal."""


@dataclass
class CommandRunner:
    """Runs shell commands against the X display.

    Injected rather than called directly so tests can substitute a recorder and assert on
    the exact xdotool invocations without needing a display.
    """

    display: str = ":99"
    timeout: float = 20.0

    def __call__(self, command: str) -> tuple[int, str, str]:
        # Inherit the environment and override only DISPLAY. Replacing it wholesale looks
        # tidier but breaks running outside the container: a hardcoded PATH misses xdotool
        # installed anywhere unusual, and dropping XAUTHORITY stops both xdotool and scrot
        # from talking to a real X server at all.
        env = {**os.environ, "DISPLAY": self.display}
        completed = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=self.timeout,
            env=env,
            check=False,
        )
        return completed.returncode, completed.stdout, completed.stderr


class ComputerTool:
    name = "computer"

    def __init__(
        self,
        width: int = 1024,
        height: int = 768,
        display: str = ":99",
        runner: CommandRunner | None = None,
        enable_zoom: bool = True,
        screenshot_path: str = "/tmp/deskwork-screen.png",
    ) -> None:
        self.width = width
        self.height = height
        self.enable_zoom = enable_zoom
        self.screenshot_path = screenshot_path
        self.run = runner or CommandRunner(display=display)

    # ------------------------------------------------------------------ tool definition

    def to_params(self) -> dict:
        params: dict = {
            "type": TOOL_TYPE,
            "name": self.name,
            "display_width_px": self.width,
            "display_height_px": self.height,
        }
        if self.enable_zoom:
            # New in computer_20251124. The portal's field labels and validation errors are
            # legible but small at 1024x768; without zoom the model guesses at them.
            params["enable_zoom"] = True
        return params

    # ------------------------------------------------------------------------ dispatch

    def __call__(self, **payload) -> ToolResult:
        action = payload.get("action")
        try:
            return self._dispatch(str(action), payload)
        except ComputerToolError as exc:
            return ToolResult.error(str(exc))
        except subprocess.TimeoutExpired:
            return ToolResult.error(f"Action {action!r} timed out.")

    def _dispatch(self, action: str, payload: dict) -> ToolResult:
        text = payload.get("text")
        coordinate = payload.get("coordinate")

        if action == "screenshot":
            return self.screenshot()

        if action == "zoom":
            return self._zoom(payload.get("region"))

        if action == "wait":
            duration = self._duration(payload.get("duration"))
            time.sleep(duration)
            return self._observe(f"Waited {duration}s.")

        if action == "mouse_move":
            x, y = self._point(coordinate)
            self._sh(f"xdotool mousemove {x} {y}")
            return self._observe(f"Moved pointer to ({x}, {y}).")

        if action in CLICK_BUTTONS:
            return self._click(CLICK_BUTTONS[action], coordinate, text, repeat=1, label=action)

        if action == "double_click":
            return self._click(1, coordinate, text, repeat=2, label=action)

        if action == "triple_click":
            return self._click(1, coordinate, text, repeat=3, label=action)

        if action in ("left_mouse_down", "left_mouse_up"):
            verb = "mousedown" if action.endswith("down") else "mouseup"
            if coordinate is not None:
                x, y = self._point(coordinate)
                self._sh(f"xdotool mousemove {x} {y} {verb} 1")
            else:
                self._sh(f"xdotool {verb} 1")
            return self._observe(f"{action} issued.")

        if action == "left_click_drag":
            x, y = self._point(coordinate)
            # Drags from wherever the pointer currently is to the given coordinate.
            self._sh(f"xdotool mousedown 1 mousemove {x} {y} mouseup 1")
            return self._observe(f"Dragged to ({x}, {y}).")

        if action == "type":
            if not isinstance(text, str) or text == "":
                raise ComputerToolError("`type` requires a non-empty `text`.")
            self._sh(f"xdotool type --delay {TYPE_DELAY_MS} -- {shlex.quote(text)}")
            return self._observe(f"Typed {len(text)} characters.")

        if action == "key":
            if not isinstance(text, str) or text == "":
                raise ComputerToolError("`key` requires a non-empty `text`.")
            self._sh(f"xdotool key --clearmodifiers -- {shlex.quote(text)}")
            return self._observe(f"Pressed {text}.")

        if action == "hold_key":
            if not isinstance(text, str) or text == "":
                raise ComputerToolError("`hold_key` requires a non-empty `text`.")
            duration = self._duration(payload.get("duration"))
            key = shlex.quote(text)
            self._sh(f"xdotool keydown -- {key}")
            time.sleep(duration)
            self._sh(f"xdotool keyup -- {key}")
            return self._observe(f"Held {text} for {duration}s.")

        if action == "scroll":
            return self._scroll(payload, coordinate, text)

        raise ComputerToolError(
            f"Unsupported action {action!r}. Supported: screenshot, zoom, wait, mouse_move, "
            "left_click, right_click, middle_click, double_click, triple_click, "
            "left_mouse_down, left_mouse_up, left_click_drag, type, key, hold_key, scroll."
        )

    # -------------------------------------------------------------------------- actions

    def _click(self, button: int, coordinate, text, repeat: int, label: str) -> ToolResult:
        # A click action always carries a coordinate. Treating a missing one as "click
        # wherever the pointer happens to be" turns a malformed call into a silent misclick
        # somewhere unpredictable, which is the single worst failure mode this tool has.
        x, y = self._point(coordinate)
        prefix = f"mousemove {x} {y} "
        # `text` on a click action is a modifier to hold, not something to type.
        if isinstance(text, str) and text:
            modifiers = " ".join(shlex.quote(k) for k in text.split("+"))
            clicks = " ".join([f"click {button}"] * repeat)
            self._sh(f"xdotool {prefix}keydown {modifiers} {clicks} keyup {modifiers}")
        else:
            self._sh(f"xdotool {prefix}click --repeat {repeat} {button}")
        return self._observe(f"{label} at {coordinate}.")

    def _scroll(self, payload: dict, coordinate, text) -> ToolResult:
        direction = payload.get("scroll_direction")
        if direction not in SCROLL_BUTTONS:
            raise ComputerToolError(
                f"`scroll_direction` must be one of {sorted(SCROLL_BUTTONS)}, got {direction!r}."
            )
        try:
            amount = int(payload.get("scroll_amount", 3))
        except (TypeError, ValueError) as exc:
            raise ComputerToolError("`scroll_amount` must be an integer.") from exc
        if amount < 1:
            raise ComputerToolError("`scroll_amount` must be at least 1.")

        prefix = ""
        if coordinate is not None:
            x, y = self._point(coordinate)
            prefix = f"mousemove {x} {y} "
        button = SCROLL_BUTTONS[direction]
        if isinstance(text, str) and text:
            modifiers = " ".join(shlex.quote(k) for k in text.split("+"))
            self._sh(
                f"xdotool {prefix}keydown {modifiers} click --repeat {amount} {button} "
                f"keyup {modifiers}"
            )
        else:
            self._sh(f"xdotool {prefix}click --repeat {amount} {button}")
        return self._observe(f"Scrolled {direction} by {amount}.")

    def screenshot(self) -> ToolResult:
        png = self._capture()
        return ToolResult(output=None, base64_image=base64.b64encode(png).decode())

    def _zoom(self, region) -> ToolResult:
        if not self.enable_zoom:
            raise ComputerToolError("Zoom is not enabled on this tool.")
        if not isinstance(region, (list, tuple)) or len(region) != 4:
            raise ComputerToolError("`region` must be [x1, y1, x2, y2].")
        try:
            x1, y1, x2, y2 = (int(v) for v in region)
        except (TypeError, ValueError) as exc:
            raise ComputerToolError("`region` values must be integers.") from exc
        if x2 <= x1 or y2 <= y1:
            raise ComputerToolError(f"`region` must have x2>x1 and y2>y1, got {list(region)}.")

        # Clamp rather than reject: a region that runs a few pixels off the edge is a
        # reasonable request, and failing it wastes a turn.
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(self.width, x2), min(self.height, y2)
        if x2 <= x1 or y2 <= y1:
            raise ComputerToolError("`region` is entirely outside the display.")

        image = Image.open(io.BytesIO(self._capture())).crop((x1, y1, x2, y2))
        buffer = io.BytesIO()
        image.save(buffer, format="PNG")
        return ToolResult(
            output=f"Zoomed to region [{x1}, {y1}, {x2}, {y2}].",
            base64_image=base64.b64encode(buffer.getvalue()).decode(),
        )

    # -------------------------------------------------------------------------- helpers

    def _capture(self) -> bytes:
        """Capture the root window as PNG bytes.

        scrot writes to a file rather than stdout, so this goes via /tmp. The screenshot is
        checked against the declared display size — a mismatch means every coordinate the
        model returns is wrong, and it is far better to say so than to silently misclick.
        """
        code, _, err = self.run(f"scrot --overwrite {shlex.quote(self.screenshot_path)}")
        if code != 0:
            raise ComputerToolError(f"screenshot failed: {err.strip() or f'scrot exited {code}'}")
        with open(self.screenshot_path, "rb") as handle:
            data = handle.read()

        image = Image.open(io.BytesIO(data))
        if image.size != (self.width, self.height):
            raise ComputerToolError(
                f"Display is {image.size[0]}x{image.size[1]} but the tool declares "
                f"{self.width}x{self.height}. Coordinates would not line up. "
                "Set DESKWORK_DISPLAY_WIDTH/HEIGHT to match the Xvfb geometry."
            )
        return data

    def _observe(self, note: str) -> ToolResult:
        """Perform-then-look: every action returns a fresh screenshot.

        Without this the model acts blind and has to spend a turn asking for a screenshot
        after every single click.
        """
        time.sleep(SETTLE_SECONDS)
        return ToolResult(output=note, base64_image=base64.b64encode(self._capture()).decode())

    def _sh(self, command: str) -> None:
        code, _, err = self.run(command)
        if code != 0:
            raise ComputerToolError(f"{command.split()[0]} failed: {err.strip() or code}")

    def _point(self, coordinate) -> tuple[int, int]:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
            raise ComputerToolError(f"`coordinate` must be [x, y], got {coordinate!r}.")
        try:
            x, y = int(coordinate[0]), int(coordinate[1])
        except (TypeError, ValueError) as exc:
            raise ComputerToolError("`coordinate` values must be integers.") from exc
        if not (0 <= x < self.width and 0 <= y < self.height):
            raise ComputerToolError(
                f"Coordinate ({x}, {y}) is outside the {self.width}x{self.height} display."
            )
        return x, y

    @staticmethod
    def _duration(raw) -> float:
        try:
            duration = float(raw if raw is not None else 1.0)
        except (TypeError, ValueError) as exc:
            raise ComputerToolError("`duration` must be a number of seconds.") from exc
        if duration < 0:
            raise ComputerToolError("`duration` must not be negative.")
        # A model that asks to wait a minute has usually lost the plot; cap it.
        return min(duration, 10.0)
