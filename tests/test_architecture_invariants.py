"""Invariants enforced by inspecting the codebase, not by hoping (D6, D13, NFR-030).

Some architectural claims cannot be tested behaviourally — you cannot write a
unit test proving that nobody will ever add a second LLM entry point. What you
can do is make the violation fail the build. Every check here corresponds to a
claim made in the PRD that would otherwise rest on everyone remembering.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MAX_LOC = 400

PY_FILES = sorted(
    p for p in list((ROOT / "pricing").rglob("*.py")) + list((ROOT / "commerce").rglob("*.py"))
    if "__pycache__" not in p.parts
)


def rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


# --- NFR-030: file size ---------------------------------------------------

def test_no_file_exceeds_the_loc_limit():
    oversized = [
        f"{rel(p)} ({len(p.read_text(encoding='utf-8').splitlines())} lines)"
        for p in PY_FILES
        if len(p.read_text(encoding="utf-8").splitlines()) > MAX_LOC
    ]
    assert not oversized, f"files over {MAX_LOC} LOC: {oversized}"


# --- D13: service boundary ------------------------------------------------

def test_the_platform_never_imports_the_commerce_package():
    """PRD 4.1 — business data is reached over HTTP only.

    A shortcut here would be invisible in behaviour and fatal to the claim, so
    it is checked structurally: the boundary is enforced, not asserted.
    """
    offenders = []
    for path in (ROOT / "pricing").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n == "commerce" or n.startswith("commerce.") for n in names):
                offenders.append(f"{rel(path)}:{node.lineno}")
    assert not offenders, (
        f"the platform imported the commerce package directly: {offenders}. "
        "The two services share no database and no code — only HTTP."
    )


def test_the_commerce_service_never_imports_the_platform():
    """The system of record must know nothing about AI."""
    offenders = []
    for path in (ROOT / "commerce").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        source = path.read_text(encoding="utf-8")
        if re.search(r"^\s*(from|import)\s+pricing\b", source, re.MULTILINE):
            offenders.append(rel(path))
    assert not offenders, f"commerce imported the platform: {offenders}"


# --- D6 / FR-043: single LLM entry point ----------------------------------

ALLOWED_LLM_CONSTRUCTORS = {"pricing/llm/grounded.py"}

# Chat-model constructors. Embedding constructors are deliberately absent:
# embeddings are the substrate retrieval is built on, and routing them through
# the grounding wrapper would recurse (the wrapper calls retrieval, retrieval
# calls embeddings). FR-043 governs chat calls.
CHAT_CONSTRUCTORS = {
    "init_chat_model", "ChatOpenAI", "ChatAnthropic",
    "ChatGoogleGenerativeAI", "ChatVertexAI",
}


def test_only_the_grounded_wrapper_constructs_a_chat_model():
    """FR-043 — 'always ground the call' must be verifiable, not policy.

    The wrapper is the sole place a chat model is built, which is what makes
    grounding injection, structured-output validation, caching and redaction
    guarantees rather than conventions.

    Resolved from the AST rather than matched in source text: a docstring
    explaining that chat models are built with `init_chat_model` is not a second
    entry point, and failing the build for saying so would train everyone to
    delete the explanation.
    """
    offenders = []
    for path in (ROOT / "pricing").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        if rel(path).replace("\\", "/") in ALLOWED_LLM_CONSTRUCTORS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            names: list[str] = []
            if isinstance(node, ast.ImportFrom):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Name):
                    names = [func.id]
                elif isinstance(func, ast.Attribute):
                    names = [func.attr]
            hits = CHAT_CONSTRUCTORS.intersection(names)
            if hits:
                offenders.append(f"{rel(path)}:{node.lineno} -> {sorted(hits)}")
    assert not offenders, (
        f"a second chat-model entry point appeared in {offenders}. All chat "
        "calls must route through pricing/llm/grounded.py."
    )


def test_no_raw_json_parsing_of_model_output():
    """PRD 4.3 — structured output is validated, never `json.loads`d.

    Scoped to the modules that handle model responses; `json.loads` on database
    or cache content elsewhere is expected and fine. Parsed from the AST rather
    than matched in the source text, so a docstring explaining the rule does not
    trip the rule.
    """
    suspects = [
        "pricing/pipeline/narration.py",
        "pricing/llm/schemas.py",
        "pricing/pipeline/rationale.py",
    ]
    offenders = []
    for name in suspects:
        path = ROOT / name
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in ("loads", "load")
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "json"
            ):
                offenders.append(f"{name}:{node.lineno}")
    assert not offenders, f"model output parsed by hand at {offenders}"


# --- Determinism: no LLM in the enforcement or push path ------------------

def test_no_llm_in_the_deterministic_packages():
    """FR-030, PRD 4.2 — analytics and rules are mathematics and law, not language.

    The rule engine holds a veto; a veto that is right 97% of the time is not a
    control. The push path likewise involves no judgement.

    Checked against **imports**, parsed from the AST, not against source text.
    A regex over the raw file matches the word "LLM" in a docstring explaining
    that no LLM is involved — which would fail the build for saying the right
    thing, and would train everyone to delete the explanation.
    """
    banned = ("pricing.llm", "langchain", "langgraph", "openai", "anthropic")
    deterministic = [
        *(ROOT / "pricing" / "analytics").glob("*.py"),
        *(ROOT / "pricing" / "rules").glob("*.py"),
        ROOT / "pricing" / "pipeline" / "execution.py",
    ]
    offenders = []
    for path in deterministic:
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            else:
                continue
            if any(n.startswith(b) for n in names for b in banned):
                offenders.append(f"{rel(path)}:{node.lineno} -> {names}")
    assert not offenders, (
        f"a language model reached a deterministic path: {offenders}"
    )


# --- NFR-018: centralised TLS bypass --------------------------------------

def test_tls_bypass_is_centralised():
    """NFR-018 — no scattered `verify=False`."""
    offenders = []
    for path in PY_FILES:
        if path.name == "tls.py":
            continue
        if re.search(r"verify\s*=\s*False", path.read_text(encoding="utf-8")):
            offenders.append(rel(path))
    assert not offenders, (
        f"TLS verification disabled outside pricing/core/tls.py: {offenders}"
    )


# --- FR-063: no emojis in the UI ------------------------------------------

EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF⬀-⯿]"
)


@pytest.mark.skipif(not (ROOT / "ui" / "src").exists(), reason="UI not present")
def test_no_emojis_anywhere_in_the_ui():
    """FR-063 — icons come from a vector library, never from the emoji table."""
    offenders = []
    for path in (ROOT / "ui" / "src").rglob("*"):
        if path.suffix not in {".tsx", ".ts", ".jsx", ".js", ".css", ".html"}:
            continue
        for number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if EMOJI.search(line):
                offenders.append(f"{path.relative_to(ROOT)}:{number}")
    assert not offenders, f"emoji found in the UI: {offenders}"


@pytest.mark.skipif(not (ROOT / "ui" / "src").exists(), reason="UI not present")
def test_every_compliance_rule_has_a_plain_language_explanation():
    """FR-096 in spirit: a reviewer has to understand what was checked.

    The rule engine emits bare codes like MARGIN_FLOOR with two unlabelled
    numbers. `ui/src/lib/ruleGuide.ts` turns those into something the primary
    persona — commercially sharp, not technical — can act on. This test fails
    when a rule is added to the engine without a corresponding explanation, so
    the two cannot drift apart silently.
    """
    engine = (ROOT / "pricing" / "rules" / "engine.py").read_text(encoding="utf-8")
    tree = ast.parse(engine)
    emitted = {
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "RuleEval"
        and node.args
        and isinstance(node.args[0], ast.Constant)
        and isinstance(node.args[0].value, str)
    }
    assert emitted, "no rule codes found — has RuleEval been renamed?"

    guide = (ROOT / "ui" / "src" / "lib" / "ruleGuide.ts").read_text(encoding="utf-8")
    documented = set(re.findall(r"^  ([A-Z][A-Z_]+):\s*\{", guide, re.MULTILINE))

    missing = sorted(emitted - documented)
    assert not missing, (
        f"rules with no plain-language explanation in ruleGuide.ts: {missing}. "
        "A reviewer sees only the code and two unlabelled numbers without one."
    )


@pytest.mark.skipif(not (ROOT / "ui" / "src").exists(), reason="UI not present")
def test_the_ui_never_holds_gateway_credentials():
    """FR-072, NFR-011 — the client never contacts the gateway or sees a key."""
    pattern = re.compile(r"llm_gateway_api_key|Authorization:\s*[`'\"]Bearer|openai\.com")
    offenders = [
        str(p.relative_to(ROOT)) for p in (ROOT / "ui" / "src").rglob("*.ts*")
        if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"the UI reached for gateway credentials: {offenders}"
