"""Load test for the Intelligence Service decision path (SPEC §12, REQ-015).

Workload: an operator dashboard / Core loop. Each simulated user repeatedly
  - POST /intel/assess  with a real mid-crisis snapshot (weight 8)  <- the decision path under test
  - GET  /health        (weight 1)
  - GET  /metrics       (weight 1)
Run:  locust -f loadtest/locustfile.py --headless -u 10 -r 10 -t 30s --host http://localhost:8100
Set LOAD_POLICY=lp to load the optimiser instead of the default heuristic.
"""
import json
import os

from locust import HttpUser, between, task

BODY = json.load(open(os.path.join(os.path.dirname(__file__), "sample_assess_request.json")))
BODY["policy"] = os.environ.get("LOAD_POLICY", "heuristic")
BODY["narrate"] = False          # LLM calls are measured separately (network-bound, cached)


class Operator(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(8)
    def assess(self):
        with self.client.post("/intel/assess", json=BODY, name="POST /intel/assess", catch_response=True) as r:
            if r.status_code != 200 or "recommendations" not in r.text:
                r.failure(f"status {r.status_code}")

    @task(1)
    def health(self):
        self.client.get("/health", name="GET /health")

    @task(1)
    def metrics(self):
        self.client.get("/metrics", name="GET /metrics")
