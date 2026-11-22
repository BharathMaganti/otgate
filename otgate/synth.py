"""Generate synthetic Jaeger traces with injected telemetry defects.

Used for demos and tests only; real scoring should run on traces captured from
a load test."""
from __future__ import annotations

import random
import uuid
from typing import Dict


def _id(n: int = 16) -> str:
    return uuid.uuid4().hex[:n]


def generate(transactions: int = 200, seed: int = 7, drop_trace: float = 0.03, drop_span: float = 0.08,
             lose_context: float = 0.05, missing_attr: float = 0.10, late: float = 0.05,
             base_time_us: int = 1_760_000_000_000_000) -> Dict:
    rnd = random.Random(seed)
    data = []
    for i in range(transactions):
        if rnd.random() < drop_trace:
            continue  # transaction ran, but no trace was ever exported
        tid = _id(32)
        procs = {"p1": {"serviceName": "quote-api"}, "p2": {"serviceName": "pricing-svc"},
                 "p3": {"serviceName": "rating-db"}, "p4": {"serviceName": "risk-model"}}
        t0 = base_time_us + i * 250_000
        root_id, price_id, db_id, model_id = _id(), _id(), _id(), _id()
        quote_id = f"Q{i:06d}"

        def span(sid, pid, op, start, dur, parent, tags):
            return {"traceID": tid, "spanID": sid, "operationName": op, "processID": pid,
                    "startTime": start, "duration": dur,
                    "references": [{"refType": "CHILD_OF", "traceID": tid, "spanID": parent}] if parent else [],
                    "tags": [{"key": k, "type": "string", "value": v} for k, v in tags.items()]}

        db_dur = rnd.randint(20_000, 90_000)
        model_dur = rnd.randint(30_000, 120_000)
        price_dur = db_dur + model_dur + rnd.randint(5_000, 20_000)
        root_dur = price_dur + rnd.randint(10_000, 40_000)
        spans = [
            span(root_id, "p1", "POST /quote", t0, root_dur, None,
                 {"http.method": "POST", "http.status_code": "200", "quote.id": quote_id}),
            span(price_id, "p2", "price.calculate", t0 + 5_000, price_dur, root_id,
                 {"quote.id": quote_id, "product.line": "auto"}),
            span(db_id, "p3", "SELECT rating_table", t0 + 8_000, db_dur, price_id,
                 {"db.system": "postgresql", "db.statement": "SELECT ..."}),
            span(model_id, "p4", "risk.inference", t0 + 8_000 + db_dur, model_dur, price_id,
                 {"model.version": "v3.2", "quote.id": quote_id}),
        ]
        # Defect injection
        if rnd.random() < drop_span:
            spans.pop(rnd.choice([2, 3]))  # an un-instrumented call
        if rnd.random() < lose_context and len(spans) > 1:
            spans[1]["references"][0]["spanID"] = _id()  # context lost across async hop
        if rnd.random() < missing_attr:
            target = spans[rnd.randrange(len(spans))]
            if target["tags"]:
                target["tags"].pop(rnd.randrange(len(target["tags"])))
        end = t0 + root_dur
        lag = rnd.uniform(45, 120) if rnd.random() < late else rnd.uniform(1, 8)
        data.append({"traceID": tid, "spans": spans, "processes": procs, "observedAt": int(end + lag * 1e6)})
    return {"data": data}
