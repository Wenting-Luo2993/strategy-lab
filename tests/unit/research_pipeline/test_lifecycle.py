"""Tests for the run lifecycle state machine."""

from __future__ import annotations

import pytest

from vibe.research_pipeline.lifecycle import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATES,
    IllegalTransitionError,
    RunState,
    assert_legal_transition,
    is_legal_transition,
    is_terminal,
)


def test_every_state_has_a_transition_entry():
    assert set(LEGAL_TRANSITIONS) == set(RunState)


def test_completed_is_only_reachable_from_validating_or_review():
    """The core guarantee: a run cannot skip validation and be trusted."""
    sources = {
        state
        for state, targets in LEGAL_TRANSITIONS.items()
        if RunState.COMPLETED in targets
    }
    assert sources == {RunState.VALIDATING, RunState.REVIEW_REQUIRED}


def test_running_cannot_jump_straight_to_completed():
    assert not is_legal_transition(RunState.RUNNING, RunState.COMPLETED)


def test_registered_cannot_jump_straight_to_completed():
    assert not is_legal_transition(RunState.REGISTERED, RunState.COMPLETED)


def test_validation_failed_cannot_become_completed():
    """Known-wrong numbers must never be laundered into a result."""
    assert not is_legal_transition(
        RunState.VALIDATION_FAILED, RunState.COMPLETED
    )


def test_review_required_can_resolve_either_way():
    assert is_legal_transition(RunState.REVIEW_REQUIRED, RunState.COMPLETED)
    assert is_legal_transition(
        RunState.REVIEW_REQUIRED, RunState.VALIDATION_FAILED
    )


def test_archived_is_absorbing():
    assert LEGAL_TRANSITIONS[RunState.ARCHIVED] == frozenset()


def test_all_terminal_states_can_be_archived():
    for state in TERMINAL_STATES - {RunState.ARCHIVED}:
        assert is_legal_transition(state, RunState.ARCHIVED)


def test_terminal_states_have_no_non_archive_exits():
    for state in TERMINAL_STATES:
        assert LEGAL_TRANSITIONS[state] <= {RunState.ARCHIVED}


def test_is_terminal_flags():
    assert is_terminal(RunState.COMPLETED)
    assert is_terminal(RunState.INCONCLUSIVE)
    assert not is_terminal(RunState.RUNNING)
    assert not is_terminal(RunState.REVIEW_REQUIRED)


def test_assert_legal_transition_passes_on_happy_path():
    assert_legal_transition(RunState.REGISTERED, RunState.RUNNING)
    assert_legal_transition(RunState.RUNNING, RunState.VALIDATING)
    assert_legal_transition(RunState.VALIDATING, RunState.COMPLETED)


def test_assert_legal_transition_raises_with_guidance():
    with pytest.raises(IllegalTransitionError) as excinfo:
        assert_legal_transition(RunState.RUNNING, RunState.COMPLETED)
    message = str(excinfo.value)
    assert "running" in message and "completed" in message
    assert "validating" in message  # tells the caller what is allowed


def test_no_state_transitions_to_itself():
    for state, targets in LEGAL_TRANSITIONS.items():
        assert state not in targets


def test_execution_failed_is_distinct_from_validation_failed():
    """No metrics at all is a different problem from bad metrics."""
    assert RunState.EXECUTION_FAILED is not RunState.VALIDATION_FAILED
    assert is_legal_transition(RunState.RUNNING, RunState.EXECUTION_FAILED)
    assert not is_legal_transition(RunState.RUNNING, RunState.VALIDATION_FAILED)
