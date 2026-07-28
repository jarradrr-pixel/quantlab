"""Project lifecycle states and the permitted transitions between them.

The transition table is data, not code paths, so that it can be tested
exhaustively and reviewed at a glance. Anything absent from the table is
forbidden: the machine cannot skip a stage.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from itertools import pairwise
from types import MappingProxyType
from typing import Final


class ProjectState(StrEnum):
    """The eleven pipeline stages plus two control states."""

    RESEARCHING = "RESEARCHING"
    KNOWLEDGE_REVIEW = "KNOWLEDGE_REVIEW"
    KNOWLEDGE_TESTING = "KNOWLEDGE_TESTING"
    HYPOTHESIS_DEVELOPMENT = "HYPOTHESIS_DEVELOPMENT"
    STRATEGY_SPECIFICATION = "STRATEGY_SPECIFICATION"
    CODE_GENERATION = "CODE_GENERATION"
    CODE_VALIDATION = "CODE_VALIDATION"
    BACKTESTING = "BACKTESTING"
    RISK_REVIEW = "RISK_REVIEW"
    AWAITING_HUMAN_APPROVAL = "AWAITING_HUMAN_APPROVAL"
    PAPER_TRADING = "PAPER_TRADING"
    PERFORMANCE_REVIEW = "PERFORMANCE_REVIEW"
    PAUSED = "PAUSED"
    REJECTED = "REJECTED"


#: The linear happy path. Used by tests to assert no stage can be skipped.
PIPELINE_ORDER: Final[tuple[ProjectState, ...]] = (
    ProjectState.RESEARCHING,
    ProjectState.KNOWLEDGE_REVIEW,
    ProjectState.KNOWLEDGE_TESTING,
    ProjectState.HYPOTHESIS_DEVELOPMENT,
    ProjectState.STRATEGY_SPECIFICATION,
    ProjectState.CODE_GENERATION,
    ProjectState.CODE_VALIDATION,
    ProjectState.BACKTESTING,
    ProjectState.RISK_REVIEW,
    ProjectState.AWAITING_HUMAN_APPROVAL,
    ProjectState.PAPER_TRADING,
    ProjectState.PERFORMANCE_REVIEW,
)

#: States from which an operator may halt work. REJECTED is terminal and
#: PAUSED cannot pause itself.
_PAUSABLE: Final[frozenset[ProjectState]] = frozenset(PIPELINE_ORDER)

#: Backward edges, for when a stage fails and earlier work must be redone.
_REWORK_EDGES: Final[dict[ProjectState, tuple[ProjectState, ...]]] = {
    ProjectState.KNOWLEDGE_REVIEW: (ProjectState.RESEARCHING,),
    ProjectState.KNOWLEDGE_TESTING: (
        ProjectState.RESEARCHING,
        ProjectState.KNOWLEDGE_REVIEW,
    ),
    ProjectState.HYPOTHESIS_DEVELOPMENT: (ProjectState.KNOWLEDGE_TESTING,),
    ProjectState.STRATEGY_SPECIFICATION: (ProjectState.HYPOTHESIS_DEVELOPMENT,),
    ProjectState.CODE_GENERATION: (ProjectState.STRATEGY_SPECIFICATION,),
    ProjectState.CODE_VALIDATION: (ProjectState.CODE_GENERATION,),
    ProjectState.BACKTESTING: (
        ProjectState.STRATEGY_SPECIFICATION,
        ProjectState.CODE_GENERATION,
    ),
    ProjectState.RISK_REVIEW: (ProjectState.BACKTESTING,),
    ProjectState.AWAITING_HUMAN_APPROVAL: (ProjectState.RISK_REVIEW,),
    ProjectState.PERFORMANCE_REVIEW: (
        ProjectState.HYPOTHESIS_DEVELOPMENT,
        ProjectState.PAPER_TRADING,
    ),
}


def _build_transition_table() -> Mapping[ProjectState, frozenset[ProjectState]]:
    table: dict[ProjectState, set[ProjectState]] = {
        state: set() for state in ProjectState
    }

    # Forward edges along the pipeline.
    for current, following in pairwise(PIPELINE_ORDER):
        table[current].add(following)

    # Rework edges.
    for state, targets in _REWORK_EDGES.items():
        table[state].update(targets)

    # Any active stage may be paused or rejected.
    for state in _PAUSABLE:
        table[state].add(ProjectState.PAUSED)
        table[state].add(ProjectState.REJECTED)

    # A paused project resumes to the stage it was paused from, or is rejected.
    table[ProjectState.PAUSED].update(_PAUSABLE)
    table[ProjectState.PAUSED].add(ProjectState.REJECTED)

    # REJECTED is terminal.
    table[ProjectState.REJECTED] = set()

    return MappingProxyType(
        {state: frozenset(targets) for state, targets in table.items()}
    )


TRANSITIONS: Final[Mapping[ProjectState, frozenset[ProjectState]]] = (
    _build_transition_table()
)

#: Entering these states requires a recorded human approval.
APPROVAL_GATED_STATES: Final[frozenset[ProjectState]] = frozenset(
    {ProjectState.PAPER_TRADING}
)

#: States in which the system may place paper orders.
TRADING_STATES: Final[frozenset[ProjectState]] = frozenset({ProjectState.PAPER_TRADING})

TERMINAL_STATES: Final[frozenset[ProjectState]] = frozenset({ProjectState.REJECTED})


def is_allowed(source: ProjectState, target: ProjectState) -> bool:
    """Return whether ``source -> target`` appears in the transition table."""
    return target in TRANSITIONS[source]
