"""Shared plumbing for the Phase 5 structured-output agents.

Factored out because ``ClaudeKnowledgeTestAgent``, ``ClaudeHypothesisAgent``
and ``ClaudeStrategyCodeAgent`` all make the identical shape of call --
one non-streaming, non-tool request constrained to a Pydantic schema via
``client.messages.parse(output_format=...)`` -- unlike ``ClaudeResearchAgent``,
which needs its own web-search/``pause_turn`` handling and doesn't use this.
"""

from __future__ import annotations

from typing import TypeVar

import anthropic
import pydantic

from app.agents.base import AgentError

T = TypeVar("T", bound=pydantic.BaseModel)


def call_claude_structured(
    client: anthropic.Anthropic,
    *,
    system: str,
    user_content: str,
    output_format: type[T],
    max_tokens: int = 4096,
) -> T:
    """Make one structured-output call and return the validated instance.

    Raises ``AgentError`` on refusal, a malformed/unparseable response, or a
    response with no parseable text block -- never returns ``None``.
    """
    try:
        response = client.messages.parse(
            model="claude-opus-5",
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_content}],
            output_format=output_format,
        )
    except pydantic.ValidationError as exc:
        raise AgentError(
            f"The model's response did not match the expected schema: {exc}"
        ) from exc

    if response.stop_reason == "refusal":
        raise AgentError("Claude declined this request.")

    for block in response.content:
        if block.type == "text" and block.parsed_output is not None:
            return block.parsed_output

    raise AgentError("The model produced no parseable output.")
