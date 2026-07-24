"""LLM evaluator for guided flag resolution (MT3-27).

Fills the ``EvaluatorResolver`` seam from ``resolver.py``: a distinct model, separate
from the working agent (so it has no task-completion incentive to clear flags for
convenience), that turns a flagged node's evidence into human-readable guidance — an
explanation of the conflict, one deciding question, and a recommended resolution. Its
verdicts are journaled (``manual_review`` events, ``source="evaluator"``) by the GUI
endpoint that requests them; it advises, the human decides, and only the human surface
(``gui_api.py``) applies resolutions. Nothing here mutates the graph.

Configuration: ``ANTHROPIC_API_KEY`` (from the environment — keep it in a chmod-600
env file, never in a service unit) enables the LLM path via ``LLMEvaluator.from_env``;
``MEMORY_EVALUATOR_MODEL`` overrides the model. Without a key the evaluator degrades to
deterministic template guidance derived from the rules verdict and severity, so the
guided review flow works identically offline.
"""

import json
import os
from typing import Literal

from pydantic import BaseModel

from .resolver import ResolverVerdict
from .schema import Event, Node

_DEFAULT_MODEL = "claude-haiku-4-5"

_ACTIONS = ("still_valid", "superseded", "wrong", "needs_correction", "defer")

# What the LLM must return; ``source`` is stamped server-side, never model-chosen.
_GUIDANCE_SCHEMA = {
    "type": "object",
    "properties": {
        "explanation": {
            "type": "string",
            "description": "Plain-language explanation of the conflict, quoting the conflicting claims.",
        },
        "question": {
            "type": "string",
            "description": "One concrete question whose answer decides the resolution.",
        },
        "recommended_action": {"type": "string", "enum": list(_ACTIONS)},
        "recommended_reason": {"type": "string"},
        "suggested_body": {
            "type": ["string", "null"],
            "description": "Corrected text, only when recommending needs_correction.",
        },
    },
    "required": [
        "explanation",
        "question",
        "recommended_action",
        "recommended_reason",
        "suggested_body",
    ],
    "additionalProperties": False,
}

_SYSTEM_PROMPT = (
    "You are an independent evaluator for an agentic memory graph — a distinct model "
    "from the working agent that wrote these notes, with no incentive to clear flags "
    "for convenience. A stored node has been flagged as contradicted. Explain the "
    "conflict in plain language for a human reviewer, quoting the conflicting claims. "
    "Pose ONE concrete question whose answer decides the resolution. Then recommend "
    "exactly one action: still_valid (the contradiction is a false alarm), superseded "
    "(a newer node replaces this one), wrong (the content is incorrect or obsolete), "
    "needs_correction (fixable with an edit — propose the corrected text in "
    "suggested_body), or defer (not enough information to decide). You advise; the "
    "human decides."
)


class Guidance(BaseModel):
    explanation: str
    question: str
    recommended_action: Literal[
        "still_valid", "superseded", "wrong", "needs_correction", "defer"
    ]
    recommended_reason: str
    suggested_body: str | None = None
    source: Literal["llm", "template"]


def _render_context(context: dict) -> str:
    """Flatten the review context into the evaluator's user turn."""
    node = context["node"]
    lines = [
        f"## Flagged node {node['id']}",
        f"type={node['type']} tier={node['tier']} path={node['path']}",
        f"severity={context['severity']} rules_verdict={context['rules_verdict']}",
        "body:",
        node["body"],
        "",
        "## Contradicting nodes",
    ]
    for c in context["contradictors"] or [{"id": "(none recorded)", "body": ""}]:
        lines += [f"- {c['id']}: {c.get('body') or c.get('preview', '')}"]
    lines.append("")
    lines.append("## Dependents (blast radius — nodes that rely on this one)")
    for d in context["dependents"] or [{"id": "(none)", "preview": ""}]:
        lines += [f"- {d['id']}: {d.get('preview', '')}"]
    lines.append("")
    lines.append("## Journal (most recent events)")
    for e in context["events"]:
        lines += [
            f"- {e['created_at']} {e['type']} polarity={e['polarity']} "
            f"weight={e['weight']} source={e['source']}: {e['reason']}"
        ]
    return "\n".join(lines)


def _template_guidance(context: dict) -> Guidance:
    """Deterministic fallback — same wizard, no LLM. Derived from the rules verdict."""
    node = context["node"]
    contradictors = context["contradictors"]
    who = (
        ", ".join(c["id"] for c in contradictors)
        if contradictors
        else "a CONTRADICTED event (no contradicting node recorded)"
    )
    verdict = context["rules_verdict"]
    if verdict == "auto_clear":
        action, reason = (
            "still_valid",
            "confirmations balance or outweigh the contradiction — the rules tier "
            "would clear this as a false alarm.",
        )
    else:
        action, reason = (
            "defer",
            "the contradiction evidence outweighs confirmations; a human judgment "
            "call is needed.",
        )
    return Guidance(
        explanation=(
            f"Node {node['id']} ({node['path']}) was contradicted by {who} with "
            f"severity {context['severity']}. The deterministic rules verdict is "
            f"'{verdict}'. No LLM evaluator is configured, so this guidance is "
            "template-generated from the journal evidence."
        ),
        question=(
            "Is the flagged statement still true as written — and if not, is it "
            "replaced by newer knowledge, simply wrong, or fixable with an edit?"
        ),
        recommended_action=action,
        recommended_reason=reason,
        suggested_body=None,
        source="template",
    )


class LLMEvaluator:
    """The MT3-27 evaluator tier. Template mode unless given a client.

    ``LLMEvaluator()`` is deterministic template mode (what tests use);
    ``LLMEvaluator.from_env()`` constructs an ``AsyncAnthropic`` client when
    ``ANTHROPIC_API_KEY`` is set, template mode otherwise. Guidance for a node is
    cached against its latest journal event, so re-opening a node in the GUI never
    re-bills; any new event (including the resolution itself) invalidates.
    """

    def __init__(self, client=None, model: str | None = None) -> None:
        self._client = client
        self._model = model or os.environ.get("MEMORY_EVALUATOR_MODEL", _DEFAULT_MODEL)
        self._cache: dict[tuple[str, str | None], Guidance] = {}

    @classmethod
    def from_env(cls) -> "LLMEvaluator":
        if os.environ.get("ANTHROPIC_API_KEY"):
            from anthropic import AsyncAnthropic

            return cls(client=AsyncAnthropic())
        return cls()

    @staticmethod
    def _cache_key(node: Node, events: list[Event]) -> tuple[str, str | None]:
        # Ignore the evaluator's own journal entries: journaling a verdict must not
        # invalidate the cache that gates journaling it (that loop would re-bill and
        # re-journal on every page load).
        latest = next(
            (e.id for e in reversed(events) if e.source != "evaluator"), None
        )
        return (node.id, latest)

    async def guidance(
        self, node: Node, events: list[Event], context: dict
    ) -> tuple[Guidance, bool]:
        """Return (guidance, freshly_llm_generated).

        The second element is True only when the LLM produced a new verdict this
        call — the caller journals the evaluator verdict exactly then, so a page
        refresh can't flood the journal and template guidance (not an evaluator
        opinion) is never journaled.
        """
        key = self._cache_key(node, events)
        if key in self._cache:
            return self._cache[key], False
        if self._client is None:
            return _template_guidance(context), False
        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1500,
                system=_SYSTEM_PROMPT,
                output_config={
                    "format": {"type": "json_schema", "schema": _GUIDANCE_SCHEMA}
                },
                messages=[{"role": "user", "content": _render_context(context)}],
            )
            text = next(b.text for b in response.content if b.type == "text")
            parsed = json.loads(text)
            parsed["source"] = "llm"
            result = Guidance.model_validate(parsed)
        except Exception:
            # The GUI must never 500 because the evaluator is down — degrade to
            # the same deterministic guidance the no-key path produces.
            return _template_guidance(context), False
        self._cache[key] = result
        return result, True

    def resolve(self, node: Node, events: list[Event]) -> ResolverVerdict:
        """Conform to the ``Resolver`` protocol for ``LadderResolver`` wiring.

        Deliberately conservative: the guided flow exists to put the decision in
        front of a human, so a flagged node always escalates to the human tier.
        """
        if not node.needs_review:
            return ResolverVerdict.auto_clear
        return ResolverVerdict.needs_human
