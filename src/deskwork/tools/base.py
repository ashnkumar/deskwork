"""Shared tool result type."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ToolResult:
    """What a tool hands back to the agent loop.

    `output` and `base64_image` both flow into the tool_result content blocks. `is_error`
    marks the result so the model is told the tool failed rather than being handed an error
    string it has to infer meaning from.
    """

    output: str | None = None
    base64_image: str | None = None
    is_error: bool = False

    @classmethod
    def error(cls, message: str) -> ToolResult:
        return cls(output=message, is_error=True)
