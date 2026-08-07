"""README.md and SPEC.md are checked against the code, not proofread.

Both documents make claims that are cheap to verify mechanically and expensive to notice
going stale — a tool type string, an embedding dimension, a measured chunk size, the two
prompt rules the whole design rests on. Each assertion below is one such claim.

A failure here means the document and the code have moved apart and the pair needs
reconciling. It does not mean the assertion should be relaxed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from deskwork import ingest, prompts
from deskwork.db import EMBED_DIM
from deskwork.tools.computer import BETA_FLAG, TOOL_TYPE, ComputerTool
from deskwork.tools.search import SearchRegulationsTool

_ROOT = Path(__file__).resolve().parents[1]
README = (_ROOT / "README.md").read_text(encoding="utf-8")
SPEC = (_ROOT / "SPEC.md").read_text(encoding="utf-8")
ARCHITECTURE = (_ROOT / "docs" / "architecture.html").read_text(encoding="utf-8")
HOW_IT_WORKS = (_ROOT / "docs" / "how-it-works.html").read_text(encoding="utf-8")

TOOL_NAMES = (ComputerTool.name, SearchRegulationsTool.name)
MODULES = sorted(
    path.stem for path in (_ROOT / "src" / "deskwork").glob("*.py") if path.stem != "__init__"
)

# Images the README itself ships, as opposed to the badge row. A badge is written
# `[![alt](img)](link)`, so the lookbehind is what separates the two.
CONTENT_IMAGES = re.findall(r"(?<!\[)!\[([^\]]*)\]\(([^)]+)\)", README)


def _prose(text: str) -> str:
    """Markdown emphasis and line wrapping are not meaning. Normalize both away."""
    return re.sub(r"\s+", " ", text.replace("**", "").replace("*", "")).lower()


@pytest.mark.parametrize("tool", TOOL_NAMES)
def test_readme_names_every_tool(tool: str) -> None:
    """The agent has exactly two tools and the README's claim rests on there being two."""
    assert f"`{tool}`" in README


def test_readme_quotes_the_current_computer_tool_type() -> None:
    """A stale tool version is the failure that killed the implementation this replaces."""
    assert f"`{TOOL_TYPE}`" in README


def test_architecture_diagram_quotes_the_current_tool_type_and_beta() -> None:
    """A diagram is as capable of going stale as prose, and harder to notice."""
    assert TOOL_TYPE in ARCHITECTURE
    assert BETA_FLAG in ARCHITECTURE


def test_readme_states_the_real_embedding_dimension() -> None:
    assert f"{EMBED_DIM}-dim" in README


def test_diagrams_state_the_real_embedding_dimension() -> None:
    assert f"{EMBED_DIM}-dim" in ARCHITECTURE


def test_readme_quotes_the_tuned_chunk_size() -> None:
    """500 is a measurement, not a preference. See test_chunk_size_is_tuned."""
    assert str(ingest.CHUNK_CHARS) in README


@pytest.mark.parametrize(
    "rule",
    [
        "Never state a regulatory fact from memory",
        "Never assume what is on screen",
    ],
)
def test_readme_quotes_the_prompt_rules_verbatim(rule: str) -> None:
    """Both documents present these as quotations of the system prompt. They must be."""
    assert rule in prompts.SYSTEM_PROMPT
    assert _prose(rule) in _prose(README)


def test_readme_does_not_promise_the_rules_are_enforced() -> None:
    """Nothing inspects a typed string to check it came from a retrieved passage.

    The README has to say so; describing cooperation in the grammar of a guarantee is the
    most expensive kind of sentence a document like this can contain.
    """
    assert "Neither rule is enforced." in README
    assert "not prevented" in README


@pytest.mark.parametrize("module", MODULES)
def test_spec_names_every_shipped_module(module: str) -> None:
    """SPEC's component table is the only full enumeration of the package."""
    assert module in SPEC


def test_readme_ships_its_own_images() -> None:
    """No hotlinking. External hosts rot, and the repo should carry its own assets.

    The document this replaces embedded its only diagram from imgur, under a caption naming
    a different project.
    """
    assert CONTENT_IMAGES, "the README should carry at least one image"
    for _, target in CONTENT_IMAGES:
        assert not target.startswith(("http://", "https://")), target
        assert (_ROOT / target).exists(), target


def test_readme_images_have_real_alt_text() -> None:
    """Alt text that says "diagram" helps nobody who needs it."""
    for alt, target in CONTENT_IMAGES:
        assert len(alt) > 40, target


def test_readme_states_the_real_test_count() -> None:
    """The count in the README is the count pytest collects.

    Collection only, so this does not re-enter the suite. If it fails, the README is
    quoting a number from a previous version of the repository.
    """
    stated = re.search(r"# (\d+) tests, no API key", README)
    assert stated, "the Tests section should state how many tests there are"

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", str(_ROOT / "tests")],
        capture_output=True,
        text=True,
        cwd=_ROOT,
    )
    collected = re.search(r"(\d+) tests? collected", proc.stdout)
    assert collected, proc.stdout[-500:]
    assert int(stated.group(1)) == int(collected.group(1))


def test_how_it_works_diagram_matches_the_shipped_corpus() -> None:
    """Every filename the narrative diagram shows is a file that exists."""
    names = re.findall(r"[a-z0-9-]+\.pdf", HOW_IT_WORKS)
    assert names, "the diagram should name the corpus documents"
    for name in set(names):
        assert (_ROOT / "corpus" / name).exists(), name
