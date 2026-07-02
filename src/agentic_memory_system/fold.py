from datetime import datetime, timezone
from typing import Protocol

from .schema import Event


class FoldStrategy(Protocol):
    def fold(self, events: list[Event]) -> float: ...


class SumAndClampFold:
    def fold(self, events: list[Event]) -> float:
        total = 1.0 + sum(e.polarity * e.weight for e in events)
        return max(0.0, min(1.0, total))


class WeightedAverageFold:
    def fold(self, events: list[Event]) -> float:
        total_weight = sum(e.weight for e in events)
        if total_weight == 0.0:
            return 1.0
        score = sum(e.weight * (1.0 if e.polarity == 1 else 0.0) for e in events) / total_weight
        return max(0.0, min(1.0, score))


_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


class LastNWindowFold:
    def __init__(self, n: int, inner: FoldStrategy | None = None) -> None:
        self._n = n
        self._inner = inner or SumAndClampFold()

    def fold(self, events: list[Event]) -> float:
        if self._n <= 0:
            return self._inner.fold([])
        ordered = sorted(events, key=lambda e: (e.created_at or _EPOCH, e.id or ""))
        return self._inner.fold(ordered[-self._n :])
