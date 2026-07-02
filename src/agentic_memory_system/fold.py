from typing import Protocol

from .schema import Event


class FoldStrategy(Protocol):
    def fold(self, events: list[Event]) -> float: ...


class SumAndClampFold:
    def fold(self, events: list[Event]) -> float:
        total = 1.0 + sum(e.polarity * e.weight for e in events)
        return max(0.0, min(1.0, total))
