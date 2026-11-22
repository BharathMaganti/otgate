"""Flow specification: what a fully observable business flow must emit."""
from __future__ import annotations

import fnmatch
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .model import Span

DEFAULT_WEIGHTS = {"complete_traces": 0.4, "span_detail": 0.4, "timeliness": 0.2}


@dataclass
class SpanRule:
    id: str
    service: str
    name: str
    required_attributes: List[str] = field(default_factory=list)
    parent: Optional[str] = None  # id of another SpanRule in the same flow
    optional: bool = False

    def matches(self, span: Span) -> bool:
        return fnmatch.fnmatchcase(span.service, self.service) and fnmatch.fnmatchcase(span.name, self.name)


@dataclass
class Flow:
    name: str
    root: str  # id of the SpanRule that identifies the flow
    spans: List[SpanRule]
    expected_transactions: Optional[int] = None

    def rule(self, rule_id: str) -> SpanRule:
        for r in self.spans:
            if r.id == rule_id:
                return r
        raise KeyError(f"flow '{self.name}': unknown span id '{rule_id}'")


@dataclass
class Spec:
    flows: List[Flow]
    weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    threshold: float = 0.8
    timeliness_seconds: float = 30.0


def _load_raw(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in (".yaml", ".yml"):
        import yaml  # PyYAML
        return yaml.safe_load(text)
    return json.loads(text)


def load_spec(path: str) -> Spec:
    raw = _load_raw(Path(path))
    flows = []
    for f in raw.get("flows", []):
        rules = []
        for i, s in enumerate(f.get("spans", [])):
            rules.append(SpanRule(
                id=s.get("id") or f"span{i + 1}",
                service=s["service"],
                name=s["name"],
                required_attributes=list(s.get("required_attributes", [])),
                parent=s.get("parent"),
                optional=bool(s.get("optional", False)),
            ))
        flow = Flow(
            name=f["name"],
            root=f.get("root") or rules[0].id,
            spans=rules,
            expected_transactions=f.get("expected_transactions"),
        )
        flow.rule(flow.root)
        for r in rules:
            if r.parent:
                flow.rule(r.parent)
        flows.append(flow)
    weights = dict(DEFAULT_WEIGHTS)
    weights.update(raw.get("weights", {}))
    total = sum(weights.values())
    if total <= 0:
        raise ValueError("weights must sum to a positive number")
    weights = {k: v / total for k, v in weights.items()}
    return Spec(
        flows=flows,
        weights=weights,
        threshold=float(raw.get("threshold", 0.8)),
        timeliness_seconds=float(raw.get("timeliness_seconds", 30)),
    )
