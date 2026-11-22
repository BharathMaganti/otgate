"""Markdown, JSON and console reports."""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import List

from .scorer import FlowResult
from .spec import Spec


def _fmt(v, pct=True):
    if v is None:
        return "n/a"
    return f"{v * 100:.1f}%" if pct else f"{v:.1f}"


def to_json(results: List[FlowResult], spec: Spec, include_traces: bool = False) -> str:
    flows = []
    for r in results:
        d = asdict(r)
        if not include_traces:
            d.pop("traces")
        flows.append(d)
    return json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "threshold": spec.threshold,
        "weights": spec.weights,
        "timeliness_seconds": spec.timeliness_seconds,
        "passed": all(r.passed for r in results),
        "flows": flows,
    }, indent=2)


def to_markdown(results: List[FlowResult], spec: Spec, top: int = 15) -> str:
    w = spec.weights
    lines = [
        "# Observability Test Report",
        "",
        f"Gate: Visibility Score >= {spec.threshold}  |  Weights: complete traces {w['complete_traces']:.0%}, "
        f"span detail {w['span_detail']:.0%}, timeliness {w['timeliness']:.0%} (<= {spec.timeliness_seconds:g}s)",
        "",
        "| Flow | Score | Result | Complete traces | Span detail | Timeliness | Traces found/expected | Uncovered time (median) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        unc = "n/a" if r.uncovered_ms_median is None else f"{r.uncovered_ms_median:.1f} ms ({r.uncovered_pct_median:.0f}%)"
        lines.append(
            f"| {r.flow} | **{r.score:.2f}** | {'PASS' if r.passed else 'FAIL'} | {_fmt(r.complete_traces)} | "
            f"{_fmt(r.span_detail)} | {_fmt(r.timeliness)} | {r.traces_found}/{r.traces_expected} | {unc} |"
        )
    for r in results:
        lines += ["", f"## {r.flow} — blind spots ({'PASS' if r.passed else 'FAIL'})", ""]
        for n in r.notes:
            lines.append(f"> {n}")
        if r.notes:
            lines.append("")
        if not r.blind_spots:
            lines.append("No blind spots found.")
            continue
        lines += ["| # | Gap | Where | Transactions affected |", "|---|---|---|---|"]
        for i, b in enumerate(r.blind_spots[:top], 1):
            lines.append(f"| {i} | {b['type']} | `{b['where']}` | {b['transactions']} ({b['share']:.0%}) |")
        if len(r.blind_spots) > top:
            lines.append(f"\n…and {len(r.blind_spots) - top} more in the JSON report.")
    return "\n".join(lines) + "\n"


def to_console(results: List[FlowResult], spec: Spec) -> str:
    out = []
    for r in results:
        out.append(
            f"[{'PASS' if r.passed else 'FAIL'}] {r.flow}: score {r.score:.2f} (threshold {spec.threshold}) "
            f"complete={_fmt(r.complete_traces)} detail={_fmt(r.span_detail)} timely={_fmt(r.timeliness)} "
            f"traces={r.traces_found}/{r.traces_expected}"
        )
        for b in r.blind_spots[:3]:
            out.append(f"    - {b['type']}: {b['where']} ({b['transactions']} transactions)")
    return "\n".join(out)
