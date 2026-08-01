"""The agent loop.

Request → tool calls → tool results → repeat, until the model stops asking for tools or a
step budget runs out. About a hundred lines, no framework, and the shape is visible in one
screen — which is the point of the repository.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, Protocol

from .config import Config
from .prompts import system_prompt
from .tools.base import ToolResult
from .tools.computer import BETA_FLAG

MAX_TOKENS = 16000

# A turn ending with one of these was truncated or paused — it is not a completed task.
# Treating them as success is how a run reports done after being cut off mid-sentence.
TRUNCATING_STOP_REASONS = frozenset(
    {"max_tokens", "model_context_window_exceeded", "pause_turn", "refusal"}
)


class Tool(Protocol):
    name: str

    def to_params(self) -> dict: ...
    def __call__(self, **payload) -> ToolResult: ...


@dataclass
class Step:
    """One turn, recorded for the transcript."""

    index: int
    text: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    stop_reason: str | None = None


@dataclass
class RunResult:
    steps: list[Step]
    messages: list[dict]
    stopped_because: str

    @property
    def final_text(self) -> str:
        for step in reversed(self.steps):
            if step.text.strip():
                return step.text.strip()
        return ""

    @property
    def tool_call_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for step in self.steps:
            for name, _ in step.tool_calls:
                counts[name] = counts.get(name, 0) + 1
        return counts


def prune_images(messages: list[dict], keep: int) -> int:
    """Drop all but the `keep` most recent screenshots, in place.

    Screenshots dominate token spend in a computer-use loop, and an image from fifteen
    turns ago is nearly worthless — the screen has moved on. Removing them keeps a long run
    affordable. The image block is replaced with a short text note rather than deleted, so
    the transcript still reads coherently and no tool_result is left with empty content.

    Only tool_result content is touched. Thinking blocks carry signatures and must be
    echoed back byte-identical, so they are never rewritten.
    """
    image_positions: list[tuple[dict, int]] = []
    for message in messages:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_result":
                continue
            inner = block.get("content")
            if not isinstance(inner, list):
                continue
            for position, piece in enumerate(inner):
                if isinstance(piece, dict) and piece.get("type") == "image":
                    image_positions.append((block, position))

    removable = len(image_positions) - keep
    if removable <= 0:
        return 0

    for block, position in image_positions[:removable]:
        block["content"][position] = {
            "type": "text",
            "text": "[screenshot removed to save context]",
        }
    return removable


def _to_tool_result_block(tool_use_id: str, result: ToolResult) -> dict:
    content: list[dict] = []
    if result.output:
        content.append({"type": "text", "text": result.output})
    if result.base64_image:
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": result.base64_image,
                },
            }
        )
    if not content:
        content.append({"type": "text", "text": "(no output)"})
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content,
        "is_error": result.is_error,
    }


def run(
    client: Any,
    config: Config,
    tools: Iterable[Tool],
    task: str,
    on_step: Callable[[Step], None] | None = None,
) -> RunResult:
    """Drive the loop until the model stops calling tools, or the budget runs out.

    `client` is anything exposing `.beta.messages.create(...)`. In tests that is a fake
    replaying recorded turns, which is how the loop is exercised without spending credit.
    """
    by_name = {tool.name: tool for tool in tools}
    messages: list[dict] = [{"role": "user", "content": task}]
    steps: list[Step] = []
    stopped_because = "max_steps"

    for index in range(1, config.max_steps + 1):
        prune_images(messages, config.max_images)

        response = client.beta.messages.create(
            model=config.model,
            max_tokens=MAX_TOKENS,
            betas=[BETA_FLAG],
            system=system_prompt(config.display_width, config.display_height),
            tools=[tool.to_params() for tool in by_name.values()],
            # Adaptive thinking is the only supported mode on current models; effort is
            # what actually controls depth and spend.
            thinking={"type": "adaptive"},
            output_config={"effort": config.effort},
            messages=messages,
        )

        blocks = _as_blocks(response)
        step = Step(index=index, text=_text_of(blocks), stop_reason=_stop_reason(response))

        # Echo the assistant turn back verbatim. Thinking blocks must round-trip unchanged.
        messages.append({"role": "assistant", "content": blocks})

        tool_uses = [b for b in blocks if _get(b, "type") == "tool_use"]
        reason = step.stop_reason or "end_turn"

        # A truncated or paused turn is not a finished task. Executing a tool call parsed
        # out of a response that was cut off mid-emission is worse still, so stop first.
        if reason in TRUNCATING_STOP_REASONS:
            steps.append(step)
            if on_step:
                on_step(step)
            stopped_because = reason
            break

        if not tool_uses:
            step.stop_reason = reason
            steps.append(step)
            if on_step:
                on_step(step)
            stopped_because = "model_finished"
            break

        results: list[dict] = []
        for block in tool_uses:
            name = str(_get(block, "name"))
            payload = _get(block, "input") or {}
            step.tool_calls.append((name, dict(payload)))

            tool = by_name.get(name)
            if tool is None:
                result = ToolResult.error(f"No tool named {name!r} is available.")
            else:
                started = time.monotonic()
                try:
                    result = tool(**payload)
                except Exception as exc:
                    # Every tool_use block must get a tool_result or the conversation is
                    # malformed and the next request is rejected. A tool that raises —
                    # a dropped Postgres connection, an embedding failure — would
                    # otherwise take the whole run down with it. Report and continue;
                    # the model can retry or route around it.
                    result = ToolResult.error(f"{type(exc).__name__}: {exc}")
                if time.monotonic() - started > 30:
                    # Not fatal, but worth seeing in a transcript.
                    result = ToolResult(
                        output=f"{result.output or ''}\n(took {time.monotonic() - started:.0f}s)",
                        base64_image=result.base64_image,
                        is_error=result.is_error,
                    )
            results.append(_to_tool_result_block(str(_get(block, "id")), result))

        # All results for one assistant turn go back in a single user message.
        messages.append({"role": "user", "content": results})
        steps.append(step)
        if on_step:
            on_step(step)

    return RunResult(steps=steps, messages=messages, stopped_because=stopped_because)


# The SDK returns objects; tests replay plain dicts. These readers accept both so the loop
# does not need to care which it is looking at.


def _get(block: Any, key: str) -> Any:
    if isinstance(block, dict):
        return block.get(key)
    return getattr(block, key, None)


def _as_blocks(response: Any) -> list[Any]:
    content = _get(response, "content")
    return list(content) if content else []


def _stop_reason(response: Any) -> str | None:
    reason = _get(response, "stop_reason")
    return str(reason) if reason is not None else None


def _text_of(blocks: Iterable[Any]) -> str:
    return "\n".join(
        str(_get(b, "text")) for b in blocks if _get(b, "type") == "text" and _get(b, "text")
    )
