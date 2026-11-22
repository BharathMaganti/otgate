"""Visibility Score computation and blind-spot detection."""
from __future__ import annotations

import statistics
from collections import Counter
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from .model import Span, Trace
from .spec import Flow, Spec, SpanRule

EMPTY = (None, "", [], {})


@dataclass
class TraceResult:
    trace_id: str
    complete: bool
    missing_spans: List[str] = field(default_factory=list)
    broken_links: List[str] = field(default_factory=list)
    wrong_parent: List[str] = field(default_factory=list)
    missing_attributes: List[Tuple[str, str]] = field(default_factory=list)
    spans_checked: int = 0
    spans_detailed: int = 0
    lag_s: Optional[float] = None
    uncovered_ms: Optional[float] = None
    root_duration_ms: Optional[float] = None


@dataclass
class FlowResult:
    flow: str
    traces_found: int
    traces_expected: int
    complete_traces: Optional[float]
    span_detail: Optional[float]
    timeliness: Optional[float]
    score: float
    passed: bool
    blind_spots: List[Dict] = field(default_factory=list)
    uncovered_ms_median: Optional[float] = None
    uncovered_ms_p95: Optional[float] = None
    uncovered_pct_median: Optional[float] = None
    lag_s_p95: Optional[float] = None
    notes: List[str] = field(default_factory=list)
    traces: List[TraceResult] = field(default_factory=list)


def _ancestors(span: Span, by_id: Dict[str, Span]):
    seen = set()
    cur = by_id.get(span.parent_id) if span.parent_id else None
    while cur is not None and cur.span_id not in seen:
        seen.add(cur.span_id)
        yield cur
        cur = by_id.get(cur.parent_id) if cur.parent_id else None


def _uncovered_us(root: Span, spans: List[Span]) -> int:
    """Time inside the root span not covered by any other span in the trace."""
    intervals = sorted(
        (max(s.start_us, root.start_us), min(s.end_us, root.end_us))
        for s in spans
        if s.span_id != root.span_id and s.end_us > root.start_us and s.start_us < root.end_us
    )
    covered, cur_s, cur_e = 0, None, None
    for s, e in intervals:
        if cur_e is None or s > cur_e:
            if cur_e is not None:
                covered += cur_e - cur_s
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        covered += cur_e - cur_s
    return max(0, root.duration_us - covered)


def _pct(values: List[float], p: float) -> Optional[float]:
    if not values:
        return None
    v = sorted(values)
    k = max(0, min(len(v) - 1, int(round(p / 100 * (len(v) - 1)))))
    return v[k]


def evaluate_trace(flow: Flow, trace: Trace, spec: Spec) -> TraceResult:
    by_id = {s.span_id: s for s in trace.spans}
    res = TraceResult(trace_id=trace.trace_id, complete=True)

    # 1. Broken trace chains: a parent id that never arrived, or several roots.
    for s in trace.spans:
        if s.parent_id and s.parent_id not in by_id:
            res.broken_links.append(s.label)
    roots = trace.roots()
    if len(roots) > 1:
        res.broken_links.extend(f"extra root {r.label}" for r in roots[1:])

    # 2. Expected spans, parentage and attributes.
    matches: Dict[str, List[Span]] = {r.id: [s for s in trace.spans if r.matches(s)] for r in flow.spans}
    for rule in flow.spans:
        found = matches[rule.id]
        if not found:
            if not rule.optional:
                res.missing_spans.append(f"{rule.service}:{rule.name}")
            continue
        for s in found:
            if rule.parent:
                parent_rule = flow.rule(rule.parent)
                if not any(parent_rule.matches(a) for a in _ancestors(s, by_id)):
                    res.wrong_parent.append(f"{rule.service}:{rule.name} (expected under {parent_rule.service}:{parent_rule.name})")
            res.spans_checked += 1
            missing = [a for a in rule.required_attributes if s.attributes.get(a) in EMPTY]
            if missing:
                res.missing_attributes.extend((f"{rule.service}:{rule.name}", a) for a in missing)
            else:
                res.spans_detailed += 1

    res.complete = not (res.missing_spans or res.broken_links or res.wrong_parent)

    # 3. Timeliness: how long after the transaction ended was the full trace visible?
    if trace.observed_at_us:
        res.lag_s = max(0.0, (trace.observed_at_us - trace.end_us) / 1e6)

    # 4. Unaccounted time inside the root span (diagnostic, not scored).
    root_rule = flow.rule(flow.root)
    root_spans = matches.get(root_rule.id) or []
    if root_spans:
        root = root_spans[0]
        res.root_duration_ms = root.duration_us / 1000
        res.uncovered_ms = _uncovered_us(root, trace.spans) / 1000
    return res


def score_flow(flow: Flow, traces: List[Trace], spec: Spec) -> FlowResult:
    root_rule = flow.rule(flow.root)
    flow_traces = [t for t in traces if any(root_rule.matches(s) for s in t.spans)]
    results = [evaluate_trace(flow, t, spec) for t in flow_traces]
    notes: List[str] = []

    expected = max(len(results), flow.expected_transactions or 0)
    if flow.expected_transactions and len(results) < flow.expected_transactions:
        notes.append(
            f"{flow.expected_transactions - len(results)} of {flow.expected_transactions} load-test transactions "
            "produced no trace at all (counted as incomplete)."
        )

    complete = (sum(r.complete for r in results) / expected) if expected else None
    checked = sum(r.spans_checked for r in results)
    detail = (sum(r.spans_detailed for r in results) / checked) if checked else (0.0 if expected else None)
    lags = [r.lag_s for r in results if r.lag_s is not None]
    timely = (sum(l <= spec.timeliness_seconds for l in lags) / len(lags)) if lags else None
    if timely is None:
        notes.append("No arrival timestamps available; timeliness excluded and weights re-normalized. "
                     "Use `otgate collect` to measure it.")

    parts = {"complete_traces": complete, "span_detail": detail, "timeliness": timely}
    active = {k: spec.weights.get(k, 0) for k, v in parts.items() if v is not None}
    wsum = sum(active.values())
    score = sum(parts[k] * w for k, w in active.items()) / wsum if wsum else 0.0

    # Blind-spot report, prioritized by how many transactions each gap affected.
    n = max(expected, 1)
    counters = {
        "missing span": Counter(x for r in results for x in set(r.missing_spans)),
        "broken trace chain": Counter(x for r in results for x in set(r.broken_links)),
        "wrong parent": Counter(x for r in results for x in set(r.wrong_parent)),
        "missing attribute": Counter(f"{sp} -> {a}" for r in results for sp, a in set(r.missing_attributes)),
    }
    severity = {"missing span": 3, "broken trace chain": 3, "wrong parent": 2, "missing attribute": 1}
    blind = []
    for kind, c in counters.items():
        for where, cnt in c.items():
            blind.append({
                "type": kind, "where": where, "transactions": cnt,
                "share": round(cnt / n, 4), "priority": round(cnt / n * severity[kind], 4),
            })
    if flow.expected_transactions and len(results) < flow.expected_transactions:
        miss = flow.expected_transactions - len(results)
        blind.append({"type": "untraced transaction", "where": f"{root_rule.service}:{root_rule.name}",
                      "transactions": miss, "share": round(miss / n, 4), "priority": round(miss / n * 3, 4)})
    blind.sort(key=lambda b: (-b["priority"], b["where"]))

    unc = [r.uncovered_ms for r in results if r.uncovered_ms is not None]
    unc_pct = [r.uncovered_ms / r.root_duration_ms * 100 for r in results
               if r.uncovered_ms is not None and r.root_duration_ms]
    return FlowResult(
        flow=flow.name,
        traces_found=len(results),
        traces_expected=expected,
        complete_traces=complete,
        span_detail=detail,
        timeliness=timely,
        score=round(score, 4),
        passed=score >= spec.threshold and expected > 0,
        blind_spots=blind,
        uncovered_ms_median=statistics.median(unc) if unc else None,
        uncovered_ms_p95=_pct(unc, 95),
        uncovered_pct_median=statistics.median(unc_pct) if unc_pct else None,
        lag_s_p95=_pct(lags, 95),
        notes=notes,
        traces=results,
    )


def score_all(spec: Spec, traces: List[Trace]) -> List[FlowResult]:
    return [score_flow(f, traces, spec) for f in spec.flows]
