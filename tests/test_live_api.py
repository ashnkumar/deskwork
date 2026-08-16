"""The tier that spends money.

Everything else in this suite runs against a fake client, which proves the loop threads tool
results correctly but proves nothing about whether the API still accepts the request it
builds. That contract is owned by someone else and changes without warning: a tool version
is retired, a thinking mode stops being valid for a model, a beta header is renamed. The
offline suite would stay green through every one of those.

These tests are deselected by default (`addopts = -m 'not live'`) and skip without a key.
They are deliberately tiny — a few hundred output tokens each — because their job is to
check that a request shape is still legal, not to exercise a run.

    uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from deskwork.agent import MAX_TOKENS  # noqa: F401  (documents where the real cap lives)
from deskwork.config import Config
from deskwork.prompts import system_prompt
from deskwork.tools.computer import BETA_FLAG, ComputerTool
from deskwork.tools.search import SearchRegulationsTool

pytestmark = pytest.mark.live

anthropic = pytest.importorskip("anthropic")


@pytest.fixture(scope="module")
def client():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        pytest.skip("ANTHROPIC_API_KEY is not set")
    return anthropic.Anthropic(api_key=key)


@pytest.fixture(scope="module")
def tool_params():
    """The exact `tools` array `deskwork run` sends: one server-defined computer tool and
    one ordinary custom tool, in the same array."""
    config = Config.from_env()
    computer = ComputerTool(width=config.display_width, height=config.display_height, display=":99")
    # `to_params()` never touches the connection, so the retrieval tool can be described
    # without a database.
    search = SearchRegulationsTool(None, config.embed_model, config.top_k)  # type: ignore[arg-type]
    return [computer.to_params(), search.to_params()]


def test_the_pinned_request_shape_is_accepted(client, tool_params):
    """One real call with exactly what the loop sends.

    Model, beta flag, adaptive thinking, effort, and both tools together. If any part of
    that combination stops being valid, this is where it surfaces — rather than in the first
    paid run someone attempts after cloning.
    """
    config = Config.from_env()
    response = client.beta.messages.create(
        model=config.model,
        max_tokens=1024,
        betas=[BETA_FLAG],
        system=system_prompt(config.display_width, config.display_height),
        tools=tool_params,
        thinking={"type": "adaptive"},
        output_config={"effort": config.effort},
        messages=[
            {
                "role": "user",
                "content": ("Reply with the single word READY. Do not call any tool."),
            }
        ],
    )
    assert response.content, "the API returned a message with no content blocks"
    assert response.stop_reason is not None


def test_the_search_tool_is_reachable_alongside_computer_use(client, tool_params):
    """A bounded turn that should produce a `search_regulations` call.

    The point of the project is that a custom retrieval tool sits in the same `tools` array
    as computer use and the model picks between them with no router. This asserts the model
    can actually reach the custom tool in that arrangement — the arrangement itself is the
    claim being demonstrated.
    """
    config = Config.from_env()
    response = client.beta.messages.create(
        model=config.model,
        max_tokens=2048,
        betas=[BETA_FLAG],
        system=system_prompt(config.display_width, config.display_height),
        tools=tool_params,
        thinking={"type": "adaptive"},
        output_config={"effort": config.effort},
        messages=[
            {
                "role": "user",
                "content": (
                    "What is the compliance date of final rule CMS-0055-F? Look it up "
                    "rather than answering from memory. Do not take a screenshot."
                ),
            }
        ],
    )
    called = [block.name for block in response.content if getattr(block, "type", "") == "tool_use"]
    assert "search_regulations" in called, (
        f"expected a search_regulations call, got {called or 'no tool calls'}"
    )


def test_adaptive_thinking_is_still_rejected_on_an_extended_only_model(client, tool_params):
    """Why `.env.example` does not list claude-opus-4-5.

    That model supports the computer tool, so it looks interchangeable with the others, but
    it is extended-thinking-only and rejects the adaptive config this loop always sends. The
    SDK's own types accept both — the model string and the adaptive union are both valid
    Python — so nothing catches this until the request is made. Pinning it here means the
    day the vendor changes that behavior, the reason for the omission gets re-examined
    instead of silently outliving its cause.
    """
    with pytest.raises(anthropic.BadRequestError) as excinfo:
        client.beta.messages.create(
            model="claude-opus-4-5",
            max_tokens=512,
            betas=[BETA_FLAG],
            tools=tool_params,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": "Reply with READY."}],
        )
    assert "adaptive" in str(excinfo.value).lower()
