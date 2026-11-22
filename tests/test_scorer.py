import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from otgate.loaders import load_traces  # noqa: E402
from otgate.scorer import score_all  # noqa: E402
from otgate.spec import load_spec  # noqa: E402
from otgate.synth import generate  # noqa: E402

SPEC = os.path.join(ROOT, "examples", "flows.yaml")


def _write(doc):
    f = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
    json.dump(doc, f)
    f.close()
    return f.name


class ScorerTests(unittest.TestCase):
    def score(self, doc, expected=None):
        spec = load_spec(SPEC)
        if expected:
            spec.flows[0].expected_transactions = expected
        return score_all(spec, load_traces([_write(doc)]))[0]

    def test_clean_traces_score_one(self):
        doc = generate(50, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        r = self.score(doc)
        self.assertEqual(r.score, 1.0)
        self.assertTrue(r.passed)
        self.assertEqual(r.blind_spots, [])

    def test_missing_span_detected(self):
        doc = generate(10, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        doc["data"][0]["spans"].pop(3)  # drop risk.inference
        r = self.score(doc)
        self.assertAlmostEqual(r.complete_traces, 0.9)
        self.assertEqual(r.blind_spots[0]["type"], "missing span")
        self.assertIn("risk.inference", r.blind_spots[0]["where"])

    def test_broken_chain_detected(self):
        doc = generate(10, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        doc["data"][0]["spans"][1]["references"][0]["spanID"] = "deadbeefdeadbeef"
        r = self.score(doc)
        kinds = {b["type"] for b in r.blind_spots}
        self.assertIn("broken trace chain", kinds)
        self.assertIn("wrong parent", kinds)

    def test_missing_attribute_lowers_detail(self):
        doc = generate(10, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        doc["data"][0]["spans"][0]["tags"] = [t for t in doc["data"][0]["spans"][0]["tags"] if t["key"] != "quote.id"]
        r = self.score(doc)
        self.assertAlmostEqual(r.span_detail, 39 / 40)
        self.assertEqual(r.complete_traces, 1.0)

    def test_untraced_transactions_count_against_completeness(self):
        doc = generate(10, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        r = self.score(doc, expected=20)
        self.assertAlmostEqual(r.complete_traces, 0.5)
        self.assertEqual(r.blind_spots[0]["type"], "untraced transaction")

    def test_timeliness_and_renormalization(self):
        doc = generate(10, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=1.0)
        r = self.score(doc)
        self.assertEqual(r.timeliness, 0.0)
        self.assertAlmostEqual(r.score, 0.8)
        for t in doc["data"]:
            t.pop("observedAt")
        r = self.score(doc)
        self.assertIsNone(r.timeliness)
        self.assertEqual(r.score, 1.0)

    def test_uncovered_time(self):
        doc = generate(5, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0)
        r = self.score(doc)
        self.assertGreater(r.uncovered_ms_median, 0)

    def test_otlp_loader(self):
        doc = {"resourceSpans": [{
            "resource": {"attributes": [{"key": "service.name", "value": {"stringValue": "quote-api"}}]},
            "scopeSpans": [{"spans": [{
                "traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331",
                "name": "POST /quote", "startTimeUnixNano": "1000000000", "endTimeUnixNano": "2000000000",
                "attributes": [{"key": "http.status_code", "value": {"intValue": "200"}}]}]}]}]}
        traces = load_traces([_write(doc)])
        self.assertEqual(len(traces), 1)
        s = traces[0].spans[0]
        self.assertEqual(s.service, "quote-api")
        self.assertEqual(s.duration_us, 1_000_000)
        self.assertEqual(s.attributes["http.status_code"], 200)

    def test_cli_exit_code(self):
        good = _write(generate(20, drop_trace=0, drop_span=0, lose_context=0, missing_attr=0, late=0))
        bad = _write(generate(20, drop_span=0.9, seed=3))
        run = lambda f: subprocess.run([sys.executable, "-m", "otgate", "score", "--spec", SPEC, "--traces", f],
                                       cwd=ROOT, capture_output=True, text=True).returncode
        self.assertEqual(run(good), 0)
        self.assertEqual(run(bad), 1)


if __name__ == "__main__":
    unittest.main()
