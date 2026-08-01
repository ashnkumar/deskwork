"""Computer tool tests. No model, no API key, no tokens spent.

Two layers:

* A recording runner asserts the exact xdotool invocations for every action. These run
  anywhere, including CI on macOS, and they are what catch a wrong button number or a
  missing `--clearmodifiers`.
* `@pytest.mark.display` tests drive a real Xvfb and assert that input actually lands.

The whole point is that the computer-use path is exercisable without burning API credit.
"""

from __future__ import annotations

import base64
import io
import shutil
import subprocess

import pytest
from PIL import Image

from deskwork.tools.base import ToolResult
from deskwork.tools.computer import TOOL_TYPE, ComputerTool

WIDTH, HEIGHT = 1024, 768


class RecordingRunner:
    """Stands in for the shell. Records commands; fakes scrot by writing a real PNG."""

    def __init__(self, path: str, size: tuple[int, int] = (WIDTH, HEIGHT), fail: bool = False):
        self.commands: list[str] = []
        self.path = path
        self.size = size
        self.fail = fail

    def __call__(self, command: str) -> tuple[int, str, str]:
        self.commands.append(command)
        if self.fail:
            return 1, "", "boom"
        if command.startswith("scrot"):
            Image.new("RGB", self.size, (240, 244, 248)).save(self.path)
        return 0, "", ""

    @property
    def last(self) -> str:
        return self.commands[-1]

    @property
    def xdotool(self) -> list[str]:
        return [c for c in self.commands if c.startswith("xdotool")]


@pytest.fixture
def tool(tmp_path):
    path = str(tmp_path / "screen.png")
    runner = RecordingRunner(path)
    instance = ComputerTool(width=WIDTH, height=HEIGHT, runner=runner, screenshot_path=path)
    return instance, runner


def decode(result: ToolResult) -> Image.Image:
    assert result.base64_image is not None
    return Image.open(io.BytesIO(base64.b64decode(result.base64_image)))


# ------------------------------------------------------------------- tool definition


def test_tool_params_declare_the_current_version(tool):
    instance, _ = tool
    params = instance.to_params()
    assert params["type"] == TOOL_TYPE == "computer_20251124"
    assert params["name"] == "computer"
    assert params["display_width_px"] == WIDTH
    assert params["display_height_px"] == HEIGHT
    assert params["enable_zoom"] is True


def test_zoom_can_be_declined(tmp_path):
    path = str(tmp_path / "s.png")
    instance = ComputerTool(runner=RecordingRunner(path), enable_zoom=False, screenshot_path=path)
    assert "enable_zoom" not in instance.to_params()


# -------------------------------------------------------------------------- actions


def test_screenshot_returns_a_png_of_the_declared_size(tool):
    instance, _ = tool
    result = instance(action="screenshot")
    assert not result.is_error
    assert decode(result).size == (WIDTH, HEIGHT)


def test_left_click_moves_then_clicks(tool):
    instance, runner = tool
    result = instance(action="left_click", coordinate=[500, 300])
    assert not result.is_error
    assert "xdotool mousemove 500 300 click --repeat 1 1" in runner.xdotool[0]


@pytest.mark.parametrize(
    ("action", "button"),
    [("left_click", 1), ("middle_click", 2), ("right_click", 3)],
)
def test_each_mouse_button_maps_correctly(tool, action, button):
    instance, runner = tool
    instance(action=action, coordinate=[10, 10])
    assert f"click --repeat 1 {button}" in runner.xdotool[0]


@pytest.mark.parametrize(("action", "repeat"), [("double_click", 2), ("triple_click", 3)])
def test_multi_clicks_repeat(tool, action, repeat):
    instance, runner = tool
    instance(action=action, coordinate=[10, 10])
    assert f"click --repeat {repeat} 1" in runner.xdotool[0]


def test_modifier_on_click_is_held_not_typed(tool):
    """`text` on a click is a modifier key. Typing it would enter a stray character."""
    instance, runner = tool
    instance(action="left_click", coordinate=[5, 5], text="ctrl+shift")
    command = runner.xdotool[0]
    assert "keydown ctrl shift" in command
    assert "keyup ctrl shift" in command
    assert "type" not in command


def test_type_uses_a_delay_and_quotes_its_argument(tool):
    instance, runner = tool
    instance(action="type", text="QI-2025-014; rm -rf /")
    command = runner.xdotool[0]
    assert "--delay 12" in command
    # The payload must be a single quoted argument, not interpretable shell.
    assert "'QI-2025-014; rm -rf /'" in command


def test_key_clears_modifiers(tool):
    """--clearmodifiers matters: a modifier left down by an earlier action corrupts the key."""
    instance, runner = tool
    instance(action="key", text="ctrl+s")
    assert runner.xdotool[0] == "xdotool key --clearmodifiers -- ctrl+s"


def test_key_argument_is_shell_safe(tool):
    instance, runner = tool
    instance(action="key", text="a; touch /tmp/pwned")
    assert "'a; touch /tmp/pwned'" in runner.xdotool[0]


@pytest.mark.parametrize(
    ("direction", "button"), [("up", 4), ("down", 5), ("left", 6), ("right", 7)]
)
def test_scroll_directions_map_to_x11_buttons(tool, direction, button):
    instance, runner = tool
    instance(action="scroll", coordinate=[100, 100], scroll_direction=direction, scroll_amount=3)
    assert f"click --repeat 3 {button}" in runner.xdotool[0]


def test_drag_presses_moves_and_releases(tool):
    instance, runner = tool
    instance(action="left_click_drag", coordinate=[400, 400])
    assert runner.xdotool[0] == "xdotool mousedown 1 mousemove 400 400 mouseup 1"


def test_wait_is_capped(tool):
    """A model that asks to wait ten minutes has lost the plot."""
    instance, _ = tool
    result = instance(action="wait", duration=9999)
    assert not result.is_error
    assert "10.0s" in (result.output or "")


def test_actions_return_a_fresh_screenshot(tool):
    """Perform-then-look, so the model is never acting blind."""
    instance, _ = tool
    result = instance(action="left_click", coordinate=[1, 1])
    assert result.base64_image is not None
    assert decode(result).size == (WIDTH, HEIGHT)


# ----------------------------------------------------------------------------- zoom


def test_zoom_crops_to_the_requested_region(tool):
    instance, _ = tool
    result = instance(action="zoom", region=[100, 200, 400, 350])
    assert not result.is_error
    assert decode(result).size == (300, 150)


def test_zoom_clamps_a_region_that_overruns_the_edge(tool):
    """Rejecting a slightly-oversized region would waste a turn for no reason."""
    instance, _ = tool
    result = instance(action="zoom", region=[900, 700, 5000, 5000])
    assert not result.is_error
    assert decode(result).size == (WIDTH - 900, HEIGHT - 700)


@pytest.mark.parametrize("region", [[10, 10, 10, 50], [1, 2, 3], "nope", [5, 5, 1, 1]])
def test_zoom_rejects_a_degenerate_region(tool, region):
    instance, _ = tool
    assert instance(action="zoom", region=region).is_error


# ----------------------------------------------------------------- errors are reported


def test_unknown_action_is_reported_not_raised(tool):
    instance, _ = tool
    result = instance(action="teleport")
    assert result.is_error
    assert "Unsupported action" in (result.output or "")


@pytest.mark.parametrize("coordinate", [[-1, 10], [10, 5000], [10], "x", None])
def test_bad_coordinates_are_reported(tool, coordinate):
    """Including a missing coordinate — a click with nowhere to go must not fall back
    to the current pointer position and misclick silently."""
    instance, _ = tool
    assert instance(action="left_click", coordinate=coordinate).is_error


def test_empty_text_is_reported(tool):
    instance, _ = tool
    assert instance(action="type", text="").is_error
    assert instance(action="key", text=None).is_error


def test_bad_scroll_direction_is_reported(tool):
    instance, _ = tool
    result = instance(action="scroll", coordinate=[1, 1], scroll_direction="sideways")
    assert result.is_error


def test_display_size_mismatch_is_loud(tmp_path):
    """Silently misclicking is far worse than refusing to run."""
    path = str(tmp_path / "s.png")
    runner = RecordingRunner(path, size=(800, 600))
    instance = ComputerTool(width=WIDTH, height=HEIGHT, runner=runner, screenshot_path=path)
    result = instance(action="screenshot")
    assert result.is_error
    assert "would not line up" in (result.output or "")


def test_failed_shell_command_is_reported(tmp_path):
    path = str(tmp_path / "s.png")
    runner = RecordingRunner(path, fail=True)
    instance = ComputerTool(runner=runner, screenshot_path=path)
    assert instance(action="screenshot").is_error


# -------------------------------------------------------- against a real X display


requires_display = pytest.mark.skipif(
    not all(shutil.which(binary) for binary in ("Xvfb", "xdotool", "scrot", "xterm")),
    reason="needs Xvfb, xdotool, scrot and xterm on PATH",
)


@pytest.fixture
def xvfb(tmp_path):
    display = ":97"
    server = subprocess.Popen(
        ["Xvfb", display, "-screen", "0", f"{WIDTH}x{HEIGHT}x24", "-nolisten", "tcp"]
    )
    try:
        for _ in range(50):
            probe = subprocess.run(
                ["xdpyinfo", "-display", display], capture_output=True, check=False
            )
            if probe.returncode == 0:
                break
            subprocess.run(["sleep", "0.1"], check=False)
        yield display
    finally:
        server.terminate()
        server.wait(timeout=5)


@requires_display
@pytest.mark.display
def test_screenshot_against_a_real_display(xvfb, tmp_path):
    from deskwork.tools.computer import CommandRunner

    path = str(tmp_path / "real.png")
    instance = ComputerTool(
        width=WIDTH, height=HEIGHT, runner=CommandRunner(display=xvfb), screenshot_path=path
    )
    result = instance(action="screenshot")
    assert not result.is_error, result.output
    assert decode(result).size == (WIDTH, HEIGHT)


@requires_display
@pytest.mark.display
def test_typing_reaches_a_real_window(xvfb, tmp_path):
    """The end-to-end proof that the tool actually drives X: type into xterm, read it back."""
    from deskwork.tools.computer import CommandRunner

    marker = tmp_path / "typed.txt"
    term = subprocess.Popen(
        ["xterm", "-display", xvfb, "-geometry", "80x24+0+0", "-e", f"cat > {marker}"]
    )
    try:
        subprocess.run(["sleep", "2"], check=False)
        path = str(tmp_path / "real.png")
        instance = ComputerTool(
            width=WIDTH, height=HEIGHT, runner=CommandRunner(display=xvfb), screenshot_path=path
        )
        assert not instance(action="left_click", coordinate=[200, 200]).is_error
        assert not instance(action="type", text="QI-2025-014").is_error
        assert not instance(action="key", text="Return").is_error
        subprocess.run(["sleep", "1"], check=False)
    finally:
        term.terminate()
        term.wait(timeout=5)

    assert marker.exists(), "xterm never wrote the file"
    assert "QI-2025-014" in marker.read_text()


def test_command_runner_inherits_the_environment(monkeypatch):
    """Running outside the container needs XAUTHORITY and the caller's PATH.

    Replacing the environment with a hardcoded PATH is tidy and wrong: xdotool installed
    somewhere unusual becomes invisible, and without XAUTHORITY neither xdotool nor scrot
    can talk to a real X server.
    """
    from deskwork.tools import computer as computer_module

    captured = {}

    def fake_run(command, **kwargs):
        captured.update(kwargs)

        class Done:
            returncode, stdout, stderr = 0, "", ""

        return Done()

    monkeypatch.setattr(computer_module.subprocess, "run", fake_run)
    monkeypatch.setenv("XAUTHORITY", "/home/someone/.Xauthority")
    monkeypatch.setenv("PATH", "/opt/weird/bin")

    computer_module.CommandRunner(display=":42")("xdotool getdisplaygeometry")

    env = captured["env"]
    assert env["DISPLAY"] == ":42", "DISPLAY must be overridden"
    assert env["XAUTHORITY"] == "/home/someone/.Xauthority", "XAUTHORITY must survive"
    assert env["PATH"] == "/opt/weird/bin", "the caller's PATH must survive"
