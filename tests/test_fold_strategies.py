from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, strategies as st

from agentic_memory_system.schema import Event, EventType
from agentic_memory_system.fold import SumAndClampFold, WeightedAverageFold, LastNWindowFold


def _event(node_id: str = "n1", **kwargs) -> Event:
    defaults = dict(
        type=EventType.confirmation_added,
        weight=1.0,
        polarity=1,
        source="test",
        reason="test",
    )
    defaults.update(kwargs)
    return Event(node_id=node_id, **defaults)


# --- WeightedAverageFold ---

def test_weighted_average_empty_returns_one():
    assert WeightedAverageFold().fold([]) == 1.0


def test_weighted_average_all_positive_is_one():
    events = [_event(polarity=1, weight=w) for w in (0.2, 1.0, 3.0)]
    assert WeightedAverageFold().fold(events) == 1.0


def test_weighted_average_all_negative_is_zero():
    events = [_event(polarity=-1, weight=w) for w in (0.2, 1.0, 3.0)]
    assert WeightedAverageFold().fold(events) == 0.0


def test_weighted_average_weights_by_confidence():
    events = [
        _event(polarity=1, weight=3.0),
        _event(polarity=-1, weight=1.0),
    ]
    assert WeightedAverageFold().fold(events) == pytest.approx(0.75)


def test_weighted_average_zero_total_weight_returns_one():
    events = [_event(polarity=1, weight=0.0), _event(polarity=-1, weight=0.0)]
    assert WeightedAverageFold().fold(events) == 1.0


@st.composite
def _weighted_event_strategy(draw):
    return _event(
        polarity=draw(st.sampled_from([-1, 1])),
        weight=draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
    )


@given(events=st.lists(_weighted_event_strategy(), max_size=8), data=st.data())
def test_weighted_average_order_independent(events, data):
    shuffled = data.draw(st.permutations(events))
    assert WeightedAverageFold().fold(events) == pytest.approx(WeightedAverageFold().fold(shuffled))


# --- LastNWindowFold ---

def _ts(offset_seconds: int) -> datetime:
    return datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(seconds=offset_seconds)


def test_last_n_window_keeps_only_most_recent():
    old = _event(polarity=-1, weight=1.0, created_at=_ts(0))
    recent = _event(polarity=1, weight=1.0, created_at=_ts(1))
    fold = LastNWindowFold(n=1)
    assert fold.fold([old, recent]) == 1.0


def test_last_n_window_ignores_input_list_order():
    old = _event(polarity=-1, weight=1.0, created_at=_ts(0))
    recent = _event(polarity=1, weight=1.0, created_at=_ts(1))
    fold = LastNWindowFold(n=1)
    assert fold.fold([old, recent]) == fold.fold([recent, old])


def test_last_n_window_n_zero_is_empty_window():
    events = [_event(polarity=1, weight=1.0, created_at=_ts(i)) for i in range(3)]
    assert LastNWindowFold(n=0).fold(events) == SumAndClampFold().fold([])


def test_last_n_window_n_larger_than_events_uses_all():
    events = [_event(polarity=1, weight=1.0, created_at=_ts(i)) for i in range(3)]
    assert LastNWindowFold(n=10).fold(events) == SumAndClampFold().fold(events)


def test_last_n_window_delegates_to_inner_strategy():
    events = [_event(polarity=-1, weight=1.0, created_at=_ts(i)) for i in range(2)]
    fold = LastNWindowFold(n=2, inner=WeightedAverageFold())
    assert fold.fold(events) == WeightedAverageFold().fold(events)


@st.composite
def _timed_event_strategy(draw):
    return _event(
        id=str(draw(st.uuids())),
        polarity=draw(st.sampled_from([-1, 1])),
        weight=draw(st.floats(min_value=0.0, max_value=2.0, allow_nan=False)),
        created_at=_ts(draw(st.integers(min_value=0, max_value=100))),
    )


@given(events=st.lists(_timed_event_strategy(), max_size=8), n=st.integers(min_value=1, max_value=8), data=st.data())
def test_last_n_window_order_independent(events, n, data):
    shuffled = data.draw(st.permutations(events))
    fold = LastNWindowFold(n=n)
    assert fold.fold(events) == fold.fold(shuffled)
