# orderflow — Observability Engineer Intern take-home

Full instructions are in the assignment doc you were sent. Quick start:

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# terminal 1
docker compose up

# terminal 2
uvicorn app:app --reload --port 8000

# terminal 3 — generate some traffic
curl -X POST localhost:8000/orders -H 'content-type: application/json' \
  -d '{"item": "widget", "qty": 3}'

curl -X POST localhost:8000/agent/analyze -H 'content-type: application/json' \
  -d '{"query": "AAPL"}'
```

Traces print to the Collector's console (`docker compose logs -f otel-collector`).
Metrics are scrapeable at `localhost:8889/metrics` once you've wired up the
metrics pipeline.

Files you'll touch:
- `app.py` — instrumentation TODOs 1-3
- `otel-collector-config.yaml` — pipeline TODOs 4-6
- `k8s/daemonset-broken.yaml` — find and fix the bugs (no cluster required)
- `bmc/bmc_thermal_task.py` — implement `parse_redfish_thermal()`; run with
  `python bmc/bmc_thermal_task.py`
- `ANSWERS.md` — written answers, questions 1-8 (create this file from
  `ANSWERS_TEMPLATE.md`)

See the assignment doc for what to submit and where.
