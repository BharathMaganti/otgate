# otgate: Observability Test Engineering

`otgate` tests the **telemetry** of a distributed system, not the system itself. After a load test, it checks every transaction's trace against a flow spec, computes a **Visibility Score**, writes a prioritized **blind-spot report**, and **fails the CI/CD stage** when a flow scores below the threshold (default 0.8), the way a code-coverage gate does.

It is tool-agnostic. It reads Jaeger JSON and OpenTelemetry OTLP/JSON (including the Collector's file-exporter JSONL), so it works with any load generator (JMeter, Locust, k6, Gatling) and any OpenTelemetry-instrumented stack.

## The Visibility Score

| Component | Weight | A transaction passes when… |
|---|---|---|
| Complete traces | 40% | every expected span is present, no span points to a parent that never arrived, there is a single root, and each span sits under the parent the spec says it should |
| Span detail | 40% | each matched span carries all its required attributes (non-empty) |
| Timeliness | 20% | the full trace was queryable in the backend within `timeliness_seconds` of the transaction ending |

`Score = 0.4·complete + 0.4·detail + 0.2·timely`. Weights and the threshold are configurable. If arrival timestamps aren't available, timeliness is left out and the other weights are re-normalized; the report says so.

Transactions the load test sent that produced **no trace at all** count as incomplete when you pass `--expected FLOW=N`.

The report also shows **uncovered time**: the share of the entry span's duration not covered by any child span. A high value means work happens somewhere no one traced.

## Install

```bash
pip install .          # or: pip install PyYAML && python -m otgate ...
```

## Quick start (synthetic data)

```bash
otgate demo --transactions 500 --out demo.json
otgate score --spec examples/flows.yaml --traces demo.json \
  --expected auto-quote=500 --report-md report.md --report-json report.json
```

## Real use

1. **Describe the flow** in `flows.yaml` (see `examples/flows.yaml`): the spans each business flow must produce, which parent each sits under, and the attributes an engineer needs to diagnose it.
2. **Run your load test** and capture the traces, either way:
   - *Jaeger:* poll while the test runs, which also measures timeliness:
     ```bash
     otgate collect --jaeger-url http://jaeger:16686 --service quote-api \
       --duration 900 --interval 5 --out traces.json
     ```
   - *OpenTelemetry Collector:* add a `file` exporter to the traces pipeline and pass that file to `--traces`.
3. **Score and gate:**
   ```bash
   otgate score --spec flows.yaml --traces traces.json --expected auto-quote=5000 \
     --report-md blind-spots.md
   ```
   Exit code `0` = gate passed, `1` = release blocked. Use `--no-fail` for report-only runs.

## CI/CD

See `.github/workflows/observability-gate.yml`. The same two commands work in Jenkins, GitLab CI or Azure DevOps as a stage after the performance test.

## Tests

```bash
python -m unittest -v
```

## Note on the demo data

`otgate demo` generates **synthetic** traces with injected defects, for demonstration and testing only. Its numbers are not results from any real system.
