"""Agent loop tests, driven by a fake client replaying recorded turns.

No API key, no network, no tokens. This is the half of the computer-use path that cannot be
tested against Xvfb, and it is where the subtle bugs live: dropping a tool_result, failing
to echo the assistant turn back, pruning the wrong blocks, looping forever.
"""

from __future__ import annotations

import pytest

from deskwork.agent import RunResult, prune_images, run
from deskwork.config import Config
from deskwork.tools.base import ToolResult

PNG = "iVBORw0KGgo="


def config(**overrides) -> Config:
    base = {
        "api_key": "test",
        "model": "claude-opus-5",
        "effort": "high",
        "max_steps": 10,
        "max_images": 3,
        "display_width": 1024,
        "display_height": 768,
        "portal_url": "http://portal:8000",
        "database_url": "postgresql://x/y",
        "embed_model": "BAAI/bge-small-en-v1.5",
        "top_k": 4,
    }
    return Config(**{**base, **overrides})


class FakeTool:
    """Records how it was called and returns whatever it was told to."""

    def __init__(self, name: str, result: ToolResult | None = None):
        self.name = name
        self.calls: list[dict] = []
        self.result = result or ToolResult(output="ok", base64_image=PNG)

    def to_params(self) -> dict:
        return {"name": self.name, "description": "", "input_schema": {"type": "object"}}

    def __call__(self, **payload) -> ToolResult:
        self.calls.append(payload)
        return self.result


class FakeClient:
    """Replays a scripted list of responses and records every request it received."""

    def __init__(self, responses: list[dict]):
        self._responses = list(responses)
        self.requests: list[dict] = []
        self.beta = self  # so `client.beta.messages.create` resolves
        self.messages = self

    def create(self, **kwargs) -> dict:
        self.requests.append(kwargs)
        if not self._responses:
            raise AssertionError("loop asked for more turns than the script provides")
        return self._responses.pop(0)


def text_turn(text: str) -> dict:
    return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn"}


def tool_turn(name: str, payload: dict, block_id: str = "toolu_1", text: str = "") -> dict:
    blocks: list[dict] = []
    if text:
        blocks.append({"type": "text", "text": text})
    blocks.append({"type": "tool_use", "id": block_id, "name": name, "input": payload})
    return {"content": blocks, "stop_reason": "tool_use"}


# ----------------------------------------------------------------------- the basic loop


def test_a_turn_with_no_tool_calls_ends_the_run():
    client = FakeClient([text_turn("All done. Reference QIR-00007.")])
    result = run(client, config(), [FakeTool("computer")], "do the thing")
    assert result.stopped_because == "model_finished"
    assert "QIR-00007" in result.final_text
    assert len(client.requests) == 1


def test_tool_is_invoked_with_the_models_arguments():
    tool = FakeTool("computer")
    client = FakeClient(
        [tool_turn("computer", {"action": "left_click", "coordinate": [10, 20]}), text_turn("done")]
    )
    run(client, config(), [tool], "task")
    assert tool.calls == [{"action": "left_click", "coordinate": [10, 20]}]


def test_tool_results_are_threaded_back_as_one_user_message():
    client = FakeClient([tool_turn("computer", {"action": "screenshot"}), text_turn("done")])
    result = run(client, config(), [FakeTool("computer")], "task")

    # user task, assistant tool_use, user tool_result, assistant text
    assert [m["role"] for m in result.messages] == ["user", "assistant", "user", "assistant"]
    tool_results = result.messages[2]["content"]
    assert len(tool_results) == 1
    assert tool_results[0]["type"] == "tool_result"
    assert tool_results[0]["tool_use_id"] == "toolu_1"


def test_parallel_tool_calls_return_in_a_single_message():
    """Splitting results across messages teaches the model to stop batching."""
    turn = {
        "content": [
            {"type": "tool_use", "id": "a", "name": "computer", "input": {"action": "screenshot"}},
            {"type": "tool_use", "id": "b", "name": "search_regulations", "input": {"query": "x"}},
        ],
        "stop_reason": "tool_use",
    }
    client = FakeClient([turn, text_turn("done")])
    result = run(client, config(), [FakeTool("computer"), FakeTool("search_regulations")], "task")
    results_message = result.messages[2]
    assert len(results_message["content"]) == 2
    assert {b["tool_use_id"] for b in results_message["content"]} == {"a", "b"}


def test_assistant_turn_is_echoed_back_verbatim():
    """Thinking blocks carry signatures and must round-trip unmodified."""
    turn = {
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig-abc"},
            {"type": "tool_use", "id": "t", "name": "computer", "input": {"action": "screenshot"}},
        ],
        "stop_reason": "tool_use",
    }
    client = FakeClient([turn, text_turn("done")])
    result = run(client, config(), [FakeTool("computer")], "task")
    echoed = result.messages[1]["content"]
    assert echoed[0] == {"type": "thinking", "thinking": "", "signature": "sig-abc"}


def test_unknown_tool_is_reported_to_the_model_not_raised():
    client = FakeClient([tool_turn("nonexistent", {}), text_turn("recovered")])
    result = run(client, config(), [FakeTool("computer")], "task")
    block = result.messages[2]["content"][0]
    assert block["is_error"] is True
    assert "No tool named" in block["content"][0]["text"]


def test_tool_error_is_marked_so_the_model_knows_it_failed():
    tool = FakeTool("computer", ToolResult.error("Coordinate (9999, 1) is outside the display."))
    client = FakeClient([tool_turn("computer", {"action": "left_click"}), text_turn("ok")])
    result = run(client, config(), [tool], "task")
    assert result.messages[2]["content"][0]["is_error"] is True


def test_step_budget_stops_a_runaway_loop():
    """A confused model must not be able to bill indefinitely."""
    client = FakeClient([tool_turn("computer", {"action": "screenshot"})] * 20)
    result = run(client, config(max_steps=4), [FakeTool("computer")], "task")
    assert result.stopped_because == "max_steps"
    assert len(result.steps) == 4
    assert len(client.requests) == 4


def test_on_step_callback_fires_per_turn():
    seen = []
    client = FakeClient([tool_turn("computer", {"action": "screenshot"}), text_turn("done")])
    run(client, config(), [FakeTool("computer")], "task", on_step=seen.append)
    assert [s.index for s in seen] == [1, 2]


# ------------------------------------------------------------------------- the request


def test_request_carries_the_computer_use_beta_and_current_settings():
    client = FakeClient([text_turn("done")])
    run(client, config(model="claude-sonnet-5", effort="medium"), [FakeTool("computer")], "t")
    request = client.requests[0]
    assert request["betas"] == ["computer-use-2025-11-24"]
    assert request["model"] == "claude-sonnet-5"
    assert request["thinking"] == {"type": "adaptive"}
    assert request["output_config"] == {"effort": "medium"}
    assert "1024x768" in request["system"]


def test_system_prompt_forbids_answering_regulations_from_memory():
    """The load-bearing instruction. If this goes missing the demo proves nothing."""
    client = FakeClient([text_turn("done")])
    run(client, config(), [FakeTool("computer")], "t")
    system = client.requests[0]["system"].lower()
    assert "never state a regulatory fact from memory" in system
    assert "never assume what is on screen" in system


# ---------------------------------------------------------------------- image pruning


def _tool_result_with_image(marker: str) -> dict:
    return {
        "role": "user",
        "content": [
            {
                "type": "tool_result",
                "tool_use_id": marker,
                "content": [
                    {"type": "text", "text": marker},
                    {"type": "image", "source": {"type": "base64", "data": PNG}},
                ],
            }
        ],
    }


def test_prune_images_keeps_only_the_most_recent():
    messages = [_tool_result_with_image(f"m{i}") for i in range(5)]
    removed = prune_images(messages, keep=2)
    assert removed == 3

    kinds = [[piece["type"] for piece in message["content"][0]["content"]] for message in messages]
    assert kinds[:3] == [["text", "text"]] * 3, "old screenshots should be replaced by a note"
    assert kinds[3:] == [["text", "image"]] * 2, "recent screenshots should survive"


def test_prune_images_is_a_no_op_below_the_limit():
    messages = [_tool_result_with_image("a")]
    assert prune_images(messages, keep=5) == 0
    assert messages[0]["content"][0]["content"][1]["type"] == "image"


def test_prune_images_never_leaves_empty_tool_result_content():
    """An empty tool_result content array is rejected by the API."""
    messages = [_tool_result_with_image(f"m{i}") for i in range(4)]
    prune_images(messages, keep=0)
    for message in messages:
        assert message["content"][0]["content"], "tool_result content must never be empty"


def test_prune_images_leaves_thinking_blocks_alone():
    messages = [
        {
            "role": "assistant",
            "content": [{"type": "thinking", "thinking": "x", "signature": "s"}],
        },
        _tool_result_with_image("a"),
        _tool_result_with_image("b"),
    ]
    prune_images(messages, keep=1)
    assert messages[0]["content"][0] == {"type": "thinking", "thinking": "x", "signature": "s"}


def test_long_run_stays_within_the_image_budget():
    """The property that actually keeps a 40-step run affordable."""
    script = [tool_turn("computer", {"action": "screenshot"}, block_id=f"t{i}") for i in range(12)]
    client = FakeClient(script)
    result = run(client, config(max_steps=12, max_images=3), [FakeTool("computer")], "task")

    images = sum(
        1
        for message in result.messages
        if isinstance(message.get("content"), list)
        for block in message["content"]
        if isinstance(block, dict) and block.get("type") == "tool_result"
        for piece in block["content"]
        if piece.get("type") == "image"
    )
    # The final turn's screenshot is added after the last prune, hence keep + 1.
    assert images <= 4, f"expected at most 4 screenshots retained, found {images}"


# ------------------------------------------------------------------------- reporting


def test_run_result_summarises_tool_usage():
    client = FakeClient(
        [
            tool_turn("search_regulations", {"query": "a"}, block_id="s1"),
            tool_turn("computer", {"action": "screenshot"}, block_id="c1"),
            text_turn("done"),
        ]
    )
    result: RunResult = run(
        client, config(), [FakeTool("computer"), FakeTool("search_regulations")], "task"
    )
    assert result.tool_call_counts == {"search_regulations": 1, "computer": 1}


@pytest.mark.parametrize("effort", ["low", "medium", "high", "xhigh", "max"])
def test_effort_is_passed_through(effort):
    client = FakeClient([text_turn("done")])
    run(client, config(effort=effort), [FakeTool("computer")], "t")
    assert client.requests[0]["output_config"]["effort"] == effort
