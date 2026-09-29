# Load test — Intelligence Service decision path

Workload: 80% `POST /intel/assess` with a real mid-crisis snapshot (`sample_assess_request.json`, tick 121), 10% `GET /health`, 10% `GET /metrics`. Each user waits 50–200 ms between requests.

```bash
python loadtest/run_loadtest.py        # starts the service, runs 1 / 10 / 50 users (heuristic) + 10 users (LP), 30 s each
# or against a running service:
locust -f loadtest/locustfile.py --headless -u 10 -r 10 -t 30s --host http://localhost:8100
```

Results: `results/summary.md` (+ the raw Locust CSVs). Recorded in `docs/verification.md` VER-014.
