"""Poll a Jaeger query API during/after a load test and record when each trace
became fully visible. The output is Jaeger JSON plus an `observedAt` field per
trace (epoch microseconds), which the scorer uses for the timeliness metric."""
from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Dict, List


def _fetch(base_url: str, service: str, start_us: int, end_us: int, limit: int, timeout: float) -> List[dict]:
    q = urllib.parse.urlencode({"service": service, "start": start_us, "end": end_us, "limit": limit})
    url = f"{base_url.rstrip('/')}/api/traces?{q}"
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8")).get("data", []) or []


def collect(base_url: str, services: List[str], duration_s: float, interval_s: float = 5.0,
            lookback_s: float = 0.0, limit: int = 5000, timeout: float = 30.0, log=print) -> dict:
    start_us = int((time.time() - lookback_s) * 1e6)
    stop_at = time.time() + duration_s
    seen: Dict[str, dict] = {}       # traceID -> latest Jaeger trace object
    counts: Dict[str, int] = {}      # traceID -> span count last seen
    while True:
        now_us = int(time.time() * 1e6)
        for svc in services:
            try:
                batch = _fetch(base_url, svc, start_us, now_us, limit, timeout)
            except Exception as exc:  # keep polling through transient errors
                log(f"warn: fetch failed for {svc}: {exc}")
                continue
            for t in batch:
                tid = t.get("traceID")
                n = len(t.get("spans", []))
                if n > counts.get(tid, 0):
                    counts[tid] = n
                    t["observedAt"] = now_us  # last time the trace grew
                    seen[tid] = t
        log(f"polled: {len(seen)} traces")
        if time.time() >= stop_at:
            break
        time.sleep(interval_s)
    return {"data": list(seen.values())}
