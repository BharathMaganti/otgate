"""Normalized trace model shared by all loaders."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class Span:
    trace_id: str
    span_id: str
    parent_id: Optional[str]
    service: str
    name: str
    start_us: int
    end_us: int
    attributes: Dict[str, Any] = field(default_factory=dict)

    @property
    def duration_us(self) -> int:
        return max(0, self.end_us - self.start_us)

    @property
    def label(self) -> str:
        return f"{self.service}:{self.name}"


@dataclass
class Trace:
    trace_id: str
    spans: List[Span] = field(default_factory=list)
    # Epoch microseconds when the complete trace was first observed in the
    # backend (set by the `collect` command). None = unknown.
    observed_at_us: Optional[int] = None

    @property
    def end_us(self) -> int:
        return max((s.end_us for s in self.spans), default=0)

    @property
    def start_us(self) -> int:
        return min((s.start_us for s in self.spans), default=0)

    def span_ids(self) -> set:
        return {s.span_id for s in self.spans}

    def roots(self) -> List[Span]:
        return [s for s in self.spans if not s.parent_id]
