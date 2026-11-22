"""Command line interface: otgate score | collect | demo."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .collect import collect
from .loaders import load_traces
from .report import to_console, to_json, to_markdown
from .scorer import score_all
from .spec import load_spec


def _apply_expected(spec, pairs):
    for p in pairs or []:
        name, _, n = p.partition("=")
        for f in spec.flows:
            if f.name == name:
                f.expected_transactions = int(n)
                break
        else:
            raise SystemExit(f"--expected: no flow named '{name}'")


def cmd_score(a) -> int:
    spec = load_spec(a.spec)
    if a.threshold is not None:
        spec.threshold = a.threshold
    _apply_expected(spec, a.expected)
    traces = load_traces(a.traces, a.format)
    results = score_all(spec, traces)
    print(to_console(results, spec))
    if a.report_md:
        Path(a.report_md).write_text(to_markdown(results, spec), encoding="utf-8")
    if a.report_json:
        Path(a.report_json).write_text(to_json(results, spec, a.include_traces), encoding="utf-8")
    passed = all(r.passed for r in results)
    print(f"\nRelease gate: {'PASSED' if passed else 'BLOCKED'}")
    return 0 if passed or a.no_fail else 1


def cmd_collect(a) -> int:
    doc = collect(a.jaeger_url, a.service, a.duration, a.interval, a.lookback, a.limit,
                  log=lambda m: print(m, file=sys.stderr))
    Path(a.out).write_text(json.dumps(doc), encoding="utf-8")
    print(f"wrote {len(doc['data'])} traces to {a.out}")
    return 0


def cmd_demo(a) -> int:
    from .synth import generate
    doc = generate(transactions=a.transactions, seed=a.seed)
    Path(a.out).write_text(json.dumps(doc), encoding="utf-8")
    print(f"wrote {len(doc['data'])} synthetic traces ({a.transactions} transactions) to {a.out}")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="otgate", description="Observability Test Engineering: score telemetry and gate releases.")
    p.add_argument("--version", action="version", version=f"otgate {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("score", help="score trace exports against a flow spec")
    s.add_argument("--spec", required=True, help="flow spec (.yaml or .json)")
    s.add_argument("--traces", required=True, nargs="+", help="Jaeger JSON or OTLP JSON/JSONL files")
    s.add_argument("--format", choices=["auto", "jaeger", "otlp"], default="auto")
    s.add_argument("--expected", nargs="*", metavar="FLOW=N", help="transactions the load test sent per flow")
    s.add_argument("--threshold", type=float, help="override the spec's pass threshold")
    s.add_argument("--report-md", help="write a Markdown blind-spot report")
    s.add_argument("--report-json", help="write a JSON report")
    s.add_argument("--include-traces", action="store_true", help="include per-trace results in JSON")
    s.add_argument("--no-fail", action="store_true", help="always exit 0 (report only)")
    s.set_defaults(func=cmd_score)

    c = sub.add_parser("collect", help="poll Jaeger and record when each trace became visible")
    c.add_argument("--jaeger-url", required=True, help="e.g. http://localhost:16686")
    c.add_argument("--service", required=True, nargs="+", help="entry-point service name(s)")
    c.add_argument("--duration", type=float, required=True, help="seconds to keep polling")
    c.add_argument("--interval", type=float, default=5.0)
    c.add_argument("--lookback", type=float, default=0.0, help="also include traces from N seconds before start")
    c.add_argument("--limit", type=int, default=5000)
    c.add_argument("--out", required=True)
    c.set_defaults(func=cmd_collect)

    d = sub.add_parser("demo", help="write synthetic traces with injected defects")
    d.add_argument("--transactions", type=int, default=200)
    d.add_argument("--seed", type=int, default=7)
    d.add_argument("--out", default="demo-traces.json")
    d.set_defaults(func=cmd_demo)

    a = p.parse_args(argv)
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
