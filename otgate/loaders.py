"""Load traces from Jaeger JSON or OpenTelemetry OTLP/JSON exports."""
from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List

from .model import Span, Trace


def _otlp_value(v: Dict[str, Any]) -> Any:
    if not isinstance(v, dict):
        return v
    for key in ("stringValue", "boolValue", "doubleValue"):
        if key in v:
            return v[key]
    if "intValue" in v:
        return int(v["intValue"])
    if "arrayValue" in v:
        return [_otlp_value(x) for x in v["arrayValue"].get("values", [])]
    if "kvlistValue" in v:
        return {kv["key"]: _otlp_value(kv.get("value", {})) for kv in v["kvlistValue"].get("values", [])}
    if "bytesValue" in v:
        return v["bytesValue"]
    return None


def _otlp_attrs(items: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    return {a["key"]: _otlp_value(a.get("value", {})) for a in items or []}


def _norm_id(value: str) -> str:
    """OTLP/JSON uses hex ids; protobuf-to-JSON tools sometimes emit base64."""
    if not value:
        return ""
    v = value.strip()
    if all(c in "0123456789abcdefABCDEF" for c in v):
        return v.lower()
    try:
        return base64.b64decode(v).hex()
    except Exception:
        return v


def load_otlp_documents(docs: List[Dict[str, Any]]) -> List[Trace]:
    traces: Dict[str, Trace] = {}
    for doc in docs:
        for rs in doc.get("resourceSpans", []):
            res = _otlp_attrs(rs.get("resource", {}).get("attributes", []))
            service = str(res.get("service.name", "unknown_service"))
            for ss in rs.get("scopeSpans", rs.get("instrumentationLibrarySpans", [])):
                for sp in ss.get("spans", []):
                    tid = _norm_id(sp.get("traceId", ""))
                    span = Span(
                        trace_id=tid,
                        span_id=_norm_id(sp.get("spanId", "")),
                        parent_id=_norm_id(sp.get("parentSpanId", "")) or None,
                        service=service,
                        name=sp.get("name", ""),
                        start_us=int(sp.get("startTimeUnixNano", 0)) // 1000,
                        end_us=int(sp.get("endTimeUnixNano", 0)) // 1000,
                        attributes=_otlp_attrs(sp.get("attributes", [])),
                    )
                    traces.setdefault(tid, Trace(trace_id=tid)).spans.append(span)
    return list(traces.values())


def load_jaeger_document(doc: Dict[str, Any]) -> List[Trace]:
    out: List[Trace] = []
    for t in doc.get("data", []):
        procs = {pid: p.get("serviceName", "unknown_service") for pid, p in t.get("processes", {}).items()}
        trace = Trace(trace_id=t.get("traceID", ""), observed_at_us=t.get("observedAt"))
        for sp in t.get("spans", []):
            parent = None
            for ref in sp.get("references", []) or []:
                if ref.get("refType") == "CHILD_OF" and ref.get("traceID", trace.trace_id) == trace.trace_id:
                    parent = ref.get("spanID")
                    break
            start = int(sp.get("startTime", 0))
            trace.spans.append(Span(
                trace_id=trace.trace_id,
                span_id=sp.get("spanID", ""),
                parent_id=parent,
                service=procs.get(sp.get("processID"), "unknown_service"),
                name=sp.get("operationName", ""),
                start_us=start,
                end_us=start + int(sp.get("duration", 0)),
                attributes={tag["key"]: tag.get("value") for tag in sp.get("tags", []) or []},
            ))
        out.append(trace)
    return out


def _read_docs(path: Path) -> List[Dict[str, Any]]:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    try:
        doc = json.loads(text)
        return doc if isinstance(doc, list) else [doc]
    except json.JSONDecodeError:
        # JSON Lines, as written by the OpenTelemetry Collector file exporter.
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def load_traces(paths: List[str], fmt: str = "auto") -> List[Trace]:
    merged: Dict[str, Trace] = {}
    for p in paths:
        docs = _read_docs(Path(p))
        for doc in docs:
            kind = fmt
            if kind == "auto":
                kind = "jaeger" if "data" in doc else "otlp"
            traces = load_jaeger_document(doc) if kind == "jaeger" else load_otlp_documents([doc])
            for t in traces:
                if t.trace_id in merged:
                    existing = merged[t.trace_id]
                    seen = existing.span_ids()
                    existing.spans.extend(s for s in t.spans if s.span_id not in seen)
                    if t.observed_at_us and not existing.observed_at_us:
                        existing.observed_at_us = t.observed_at_us
                else:
                    merged[t.trace_id] = t
    return list(merged.values())
