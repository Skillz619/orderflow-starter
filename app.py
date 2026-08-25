"""
orderflow — a tiny service with two kinds of workload:

  1. A traditional REST endpoint (`/orders`) that calls a simulated
     downstream payment service.
  2. A small "agentic" endpoint (`/agent/analyze`) that runs a
     plan -> tool_call -> model_inference pipeline, similar in shape
     to how Canyon Code instruments agent workflows and tool calls.

Your job (see the assignment doc) is to instrument this service with
OpenTelemetry — traces AND metrics — following the TODOs below.
Do not change the business logic; only add instrumentation.

Run it with:
    uvicorn app:app --reload --port 8000
"""

import random
import time
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from opentelemetry import trace, metrics, context
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

app = FastAPI(title="orderflow")

executor = ThreadPoolExecutor(max_workers=4)

# ---------------------------------------------------------------------------
# TODO(1): OpenTelemetry SDK setup.
#
# A Resource identifies *this process* to anything downstream — without it
# the Collector's console output is just anonymous spans with no service
# name attached, which is useless once you have more than one service.
# ---------------------------------------------------------------------------
resource = Resource.create({"service.name": "orderflow"})

# --- Tracing -----------------------------------------------------------
# BatchSpanProcessor buffers spans client-side and exports them in batches
# on its own schedule/size threshold, rather than one gRPC call per span.
tracer_provider = TracerProvider(resource=resource)
tracer_provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="localhost:4317", insecure=True))
)
trace.set_tracer_provider(tracer_provider)
tracer = trace.get_tracer("orderflow")

# --- Metrics -------------------------------------------------------------
# Metrics don't stream per-datapoint like spans; a PeriodicExportingMetricReader
# collects the current state of every instrument on a fixed interval and
# ships it in one OTLP export.
metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(endpoint="localhost:4317", insecure=True),
    export_interval_millis=5000,
)
meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
metrics.set_meter_provider(meter_provider)
meter = metrics.get_meter("orderflow")

# One counter, one histogram. Attributes kept low-cardinality on purpose:
# `endpoint` and `status` have a handful of fixed values each. Something
# like order_id or query text does NOT belong here — see ANSWERS.md Q1.
requests_counter = meter.create_counter(
    "orderflow.requests",
    unit="1",
    description="Count of requests processed, by endpoint and outcome.",
)
downstream_duration_histogram = meter.create_histogram(
    "orderflow.downstream_call.duration",
    unit="ms",
    description="Duration of downstream/step calls (payment, tool_call, model_inference).",
)

# Auto-instrument FastAPI: gives every request a root HTTP span (route,
# method, status code) for free. Our manual spans below become children
# of that root span automatically, since they're created while the
# request's context is still active.
FastAPIInstrumentor.instrument_app(app)


class OrderRequest(BaseModel):
    item: str
    qty: int


class AgentRequest(BaseModel):
    query: str


def call_payment_service(order_id: str, amount: float) -> dict:
    """Simulated downstream call. ~10% chance of failure."""
    time.sleep(random.uniform(0.05, 0.25))
    if random.random() < 0.10:
        raise RuntimeError("payment_service: timeout")
    return {"order_id": order_id, "amount": amount, "status": "charged"}


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/orders")
def create_order(req: OrderRequest):
    # TODO(2): span per request + child span around the downstream call.
    with tracer.start_as_current_span("create_order") as order_span:
        order_id = f"ord_{random.randint(1000, 9999)}"
        amount = round(req.qty * random.uniform(5, 50), 2)

        # Order-specific detail as SPAN attributes, not metric labels —
        # item name is unbounded/high-cardinality, exactly what you do NOT
        # want on a metric (see ANSWERS.md Q1). A span attribute is fine:
        # it lives on one trace, not on a time series that grows forever.
        order_span.set_attribute("order.id", order_id)
        order_span.set_attribute("order.item", req.item)
        order_span.set_attribute("order.qty", req.qty)
        order_span.set_attribute("order.amount", amount)

        status = "success"
        start = time.monotonic()
        try:
            with tracer.start_as_current_span("call_payment_service") as payment_span:
                payment_span.set_attribute("order.id", order_id)
                payment_span.set_attribute("payment.amount", amount)
                try:
                    result = call_payment_service(order_id, amount)
                except RuntimeError as e:
                    payment_span.record_exception(e)
                    payment_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
                    raise
        except RuntimeError as e:
            status = "error"
            order_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise HTTPException(status_code=502, detail=str(e))
        finally:
            duration_ms = (time.monotonic() - start) * 1000
            downstream_duration_histogram.record(
                duration_ms, {"endpoint": "orders", "step": "payment_service"}
            )
            requests_counter.add(1, {"endpoint": "orders", "status": status})

        return {"order_id": order_id, "item": req.item, "qty": req.qty, "payment": result}


def run_tool_call(query: str, parent_ctx: context.Context) -> dict:
    """Simulated external tool invocation (e.g. a lookup/search tool).

    Runs on a ThreadPoolExecutor worker thread. `parent_ctx` was captured
    on the request thread and must be explicitly attached here — otel
    context does not cross the thread boundary on its own (see TODO(3)
    notes above `agent_analyze`).
    """
    token = context.attach(parent_ctx)
    try:
        start = time.monotonic()
        with tracer.start_as_current_span("tool_call") as span:
            span.set_attribute("tool.name", "lookup")
            span.set_attribute("tool.query", query)
            time.sleep(random.uniform(0.1, 0.4))
            result = {"tool": "lookup", "query": query, "result": f"data-for-{query}"}
            duration_ms = (time.monotonic() - start) * 1000
            downstream_duration_histogram.record(
                duration_ms, {"endpoint": "agent_analyze", "step": "tool_call"}
            )
            return result
    finally:
        context.detach(token)


def run_model_inference(tool_ctx: dict) -> dict:
    """Simulated LLM call. Reports fake token usage, like a real
    provider response would."""
    start = time.monotonic()
    with tracer.start_as_current_span("model_inference") as span:
        time.sleep(random.uniform(0.3, 1.0))
        input_tokens = random.randint(200, 800)
        output_tokens = random.randint(50, 300)

        # Token counts as span attributes: this is what lets you later
        # attribute $ cost to a single agent run in a trace viewer.
        span.set_attribute("llm.model", "sim-model-large")
        span.set_attribute("llm.usage.input_tokens", input_tokens)
        span.set_attribute("llm.usage.output_tokens", output_tokens)

        duration_ms = (time.monotonic() - start) * 1000
        downstream_duration_histogram.record(
            duration_ms, {"endpoint": "agent_analyze", "step": "model_inference"}
        )
        return {
            "answer": f"synthesized answer using {tool_ctx['result']}",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "model": "sim-model-large",
        }


@app.post("/agent/analyze")
def agent_analyze(req: AgentRequest):
    # TODO(3): workflow = plan -> tool_call -> model_inference, each its
    # own span, with tool_call hopping onto a background thread.
    with tracer.start_as_current_span("agent_analyze") as workflow_span:
        status = "success"
        try:
            with tracer.start_as_current_span("plan") as plan_span:
                plan = {"steps": ["tool_call", "model_inference"], "query": req.query}
                plan_span.set_attribute("agent.query", req.query)
                plan_span.set_attribute("agent.plan.steps", plan["steps"])

            # Capture the current context (includes the "plan" span's parent,
            # i.e. workflow_span, as current) BEFORE handing off to the
            # executor thread. Without this, run_tool_call's span would
            # start a disconnected trace instead of nesting under this one.
            parent_ctx = context.get_current()
            future = executor.submit(run_tool_call, req.query, parent_ctx)
            tool_result = future.result()

            model_result = run_model_inference(tool_result)

            workflow_span.set_attribute(
                "llm.usage.total_tokens",
                model_result["input_tokens"] + model_result["output_tokens"],
            )

            return {
                "query": req.query,
                "plan": plan,
                "tool_result": tool_result,
                "answer": model_result["answer"],
                "usage": {
                    "input_tokens": model_result["input_tokens"],
                    "output_tokens": model_result["output_tokens"],
                },
            }
        except Exception as e:
            status = "error"
            workflow_span.record_exception(e)
            workflow_span.set_status(trace.Status(trace.StatusCode.ERROR, str(e)))
            raise
        finally:
            requests_counter.add(1, {"endpoint": "agent_analyze", "status": status})