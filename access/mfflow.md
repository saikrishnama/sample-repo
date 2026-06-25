# Monitoring AI Agents with MLflow on Databricks (and Any OpenTelemetry Backend)

You've shipped an AI agent. Maybe it's a LangChain agent, maybe a LangGraph
workflow, maybe a hand-rolled loop calling OpenAI or Anthropic directly. It works in
the demo. Then it hits production and the questions start: Why did this run take
nine model calls when it should take two? Which user is burning our token budget?
Why did the agent loop on itself? What did the model actually see, and what did it
say back?

You can't answer any of that without **tracing** — a step-by-step record of every
model call, tool invocation, and retrieval inside each agent run, with token counts,
latency, inputs, and outputs attached. This guide shows how to get that with
**MLflow**, why MLflow is an unusually good fit when you're on **Databricks**, and —
critically — how MLflow's tracing is built on **OpenTelemetry** so the same
instrumentation works whether you store traces in Databricks, in your own
OpenTelemetry collector, or in both at once.

The whole thing rests on one fact: **MLflow Tracing is an OpenTelemetry
instrumentation library.** The traces it produces are OpenTelemetry traces. That's
what lets you instrument your agent exactly once and then point the data wherever you
want, today or three backends from now, without touching the code.

---

## What you get, in one picture

```
  Your agent (LangChain / LangGraph / OpenAI / Anthropic / custom)
        │
        │   mlflow.<framework>.autolog()   ← one line, or @mlflow.trace by hand
        ▼
  MLflow Tracing (OpenTelemetry under the hood)
        │
        ├──────────────► Databricks Managed MLflow ──► Trace UI · Eval · Lineage
        │                (set_tracking_uri("databricks"))
        │
        └──OTLP─────────► Your OpenTelemetry Collector ──► Tempo / Jaeger (traces)
                          (OTEL_EXPORTER_OTLP_*)            Prometheus (metrics)
                                                            Loki / OpenSearch (logs)
                                                                  │
                                                                  ▼
                                                            Grafana / Datadog /
                                                            Honeycomb / SigNoz / ...
```

A single agent run becomes a **trace** — a tree of **spans**. The root span is the
agent invocation; child spans are the individual model calls (`CHAT_MODEL`), tool
calls (`TOOL`), retrieval steps (`RETRIEVER`), and chain steps (`CHAIN`). Each span
carries timing, inputs, outputs, and — for model calls — token usage and model name.
That tree is what you read when something goes wrong, and what aggregates into your
cost and latency dashboards when everything goes right.

---

## Step 1 — Instrument the agent

There are two ways to produce traces. Use auto-instrumentation everywhere you can,
and manual instrumentation for the parts a library can't see.

### Auto-instrumentation (one line per framework)

MLflow ships built-in tracing integrations for roughly twenty GenAI frameworks and
SDKs. A single `autolog()` call patches the client so every call emits a fully
detailed span tree — no other code changes.

```python
# pip install "mlflow>=3.1"
import mlflow

mlflow.set_experiment("/Shared/support-agent")   # groups traces under one experiment

# Enable tracing for whichever framework(s) your agent uses. Each is one line.
mlflow.langchain.autolog()      # LangChain + LangGraph: chains, agents, tools, retrievers
mlflow.openai.autolog()         # raw OpenAI SDK calls
mlflow.anthropic.autolog()      # raw Anthropic SDK calls
mlflow.llama_index.autolog()    # LlamaIndex query engines + retrievers
mlflow.dspy.autolog()           # DSPy modules
# also available: bedrock, gemini, crewai, autogen, litellm, langgraph, and more

# From here, every agent run produces a full trace tree automatically:
#   AGENT/CHAIN (root)
#     ├─ CHAT_MODEL span  → model name, input/output tokens, latency
#     ├─ TOOL span        → tool name, args, result
#     └─ RETRIEVER span   → query, retrieved documents
```

For a LangChain or LangGraph agent, this alone captures your two most valuable
failure signals for free: **how many model calls a run took** (your reasoning-loop
detector) and **how many tool calls fired** (your dependency-error detector) — both
visible directly in the span tree.

### Manual instrumentation (for custom code)

When you're not on a supported framework — a custom agent loop, a bespoke tool —
instrument by hand. MLflow gives you two primitives.

The decorator, for whole functions:

```python
import mlflow

@mlflow.trace(span_type="TOOL", name="execute_tool lookup_order")
def lookup_order(order_id: str) -> dict:
    # Inputs and the return value are captured automatically.
    return db.fetch_order(order_id)

@mlflow.trace(span_type="AGENT", name="invoke_agent support-agent")
def run_agent(task: str) -> str:
    ...
```

And `start_span`, for fine-grained control inside a function — here a model call with
rich attributes attached:

```python
import mlflow
from mlflow.entities import SpanType

def call_model(prompt: str, session_id: str) -> str:
    with mlflow.start_span(name="chat claude-3-5-sonnet", span_type=SpanType.LLM) as span:
        span.set_attributes({
            "gen_ai.provider.name": "anthropic",
            "gen_ai.operation.name": "chat",
            "gen_ai.request.model": "claude-3-5-sonnet",
            "gen_ai.conversation.id": session_id,
        })
        span.set_inputs({"prompt": prompt})

        resp = anthropic_client.messages.create(
            model="claude-3-5-sonnet-20241022",
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )

        u = resp.usage
        span.set_attributes({
            "gen_ai.usage.input_tokens": u.input_tokens,
            "gen_ai.usage.output_tokens": u.output_tokens,
            "gen_ai.usage.cache_read.input_tokens": getattr(u, "cache_read_input_tokens", 0),
            "gen_ai.response.finish_reasons": [resp.stop_reason],
        })
        span.set_outputs({"text": resp.content[0].text})
        return resp.content[0].text
```

The `span_type` values — `LLM`, `CHAT_MODEL`, `AGENT`, `CHAIN`, `TOOL`, `RETRIEVER`,
`EMBEDDING`, `RERANKER`, `PARSER` — are MLflow's GenAI span taxonomy. They matter
because they're what gets translated into standard GenAI attribute names when you
export to OpenTelemetry (Step 3).

### Stamping user and session context

Auto-instrumentation captures the framework's internals but knows nothing about
*who* triggered the run. Add that yourself, on the active trace:

```python
import mlflow

with mlflow.start_span(name="invoke_agent support-agent") as span:
    mlflow.update_current_trace(tags={
        "enduser.id": user_id,            # use a hashed/pseudonymous key, never raw PII
        "enduser.tenant": tenant_id,
        "user.tier": tier,
        "gen_ai.conversation.id": session_id,
    })
    span.set_inputs({"task": task})
    result = agent.invoke(task)           # autolog captures everything underneath
    span.set_outputs({"result": result})
```

These tags are what let you later ask "top users by token spend" or "per-tenant
cost" — essential if you bill by usage. Keep them pseudonymous: a hashed user key,
not an email or name.

> **Namespace discipline.** MLflow's own attributes live under `mlflow.*`, and the
> GenAI semantic conventions own `gen_ai.*`. Anything *you* invent — an internal task
> ID, a custom counter like `model_calls` — should go under your own prefix
> (`app.*`), not either of those. It keeps your telemetry from colliding with names
> the spec or MLflow may add later.

---

## Step 2 — Send traces to Databricks Managed MLflow

If your agents run on Databricks, or you want the managed trace UI with evaluation
and data lineage, point MLflow at the workspace. This is the simplest path and a
great default for development and debugging.

```python
import mlflow

# Inside a Databricks notebook or job this is automatic. From outside, authenticate:
#   export DATABRICKS_HOST=https://<workspace>.cloud.databricks.com
#   export DATABRICKS_TOKEN=<token>
mlflow.set_tracking_uri("databricks")
mlflow.set_experiment("/Shared/support-agent")

mlflow.langchain.autolog()   # or your manual spans from Step 1
# ... run your agent ...
```

Traces land in the **Traces** tab of the MLflow experiment in your Databricks
workspace. Each trace renders as the span tree — expand any span to see its inputs,
outputs, token usage, and latency. On top of raw tracing you get the
Databricks-native extras:

- **Evaluation.** Run LLM-as-judge or rule-based scorers against production traces;
  the scores attach to the trace so quality becomes queryable, not anecdotal.
- **Prompt registry & lineage.** Version prompts, and trace each run back to the
  Unity Catalog models and data it touched.

This path is ideal for understanding individual runs and for the offline eval loop.
For cross-team aggregation, cost dashboards, and alerting, you'll also want the
traces in an observability backend — which is the next step.

---

## Step 3 — Send traces to your own OpenTelemetry backend

Because MLflow Tracing emits OpenTelemetry, you can ship the exact same traces to any
OTLP-compatible collector or backend — Grafana Tempo, Jaeger, Datadog, Honeycomb,
SigNoz, New Relic, Splunk — without changing a line of the instrumentation from
Step 1. You configure it entirely through environment variables, set **before any
span is created**.

```bash
# Point MLflow's exporter at your OpenTelemetry Collector
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="http://otel-collector:4317/v1/traces"
export OTEL_EXPORTER_OTLP_TRACES_PROTOCOL="grpc"          # or "http/protobuf"
export OTEL_SERVICE_NAME="support-agent"                  # becomes service.name on every span

# Optional auth headers, if your collector or backend requires them
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="api_key=12345"

# The linchpin: translate MLflow's spans into the standard GenAI conventions.
# This converts span types, model info, token usage, and messages into gen_ai.* attributes.
export MLFLOW_ENABLE_OTEL_GENAI_SEMCONV="true"
```

```python
import mlflow

# With OTEL_EXPORTER_OTLP_TRACES_ENDPOINT set, MLflow exports OTLP instead of to a
# tracking server. No tracking URI is required for OTel-only mode.
mlflow.langchain.autolog()
# ... run your agent; spans arrive at the collector as gen_ai.* OTLP traces ...
```

`MLFLOW_ENABLE_OTEL_GENAI_SEMCONV=true` is the setting that makes this worthwhile.
Without it, MLflow exports its internal attribute names and your traces won't line up
with telemetry from any other source. With it, an MLflow `CHAT_MODEL` span arrives as
a `gen_ai.operation.name = chat` span carrying `gen_ai.usage.input_tokens`,
`gen_ai.request.model`, `gen_ai.response.finish_reasons`, and the rest of the
standard GenAI attributes — so a single dashboard query works across every agent and
team, regardless of which framework produced the span.

### Why route through a collector at all

Pointing your apps at an OpenTelemetry Collector (rather than straight at a backend)
buys you three things that matter for AI workloads specifically:

- **Central PII redaction.** Prompts and responses routinely contain regulated data.
  A redaction processor in the collector scrubs it once, centrally, before it lands
  in storage — no individual app can leak by forgetting to scrub.
- **Tail sampling.** Keep *every* error and slow agent run, and sample only the
  boring successes. For agents, the rare failure is the whole point, so you decide
  what to keep after the trace finishes, not before it starts.
- **Backend portability.** Switch from Tempo to Datadog by editing one exporter
  block. The apps never know.

A minimal collector config that redacts, samples, and fans out:

```yaml
# otel-collector-config.yaml
receivers:
  otlp:
    protocols:
      grpc: { endpoint: 0.0.0.0:4317 }
      http: { endpoint: 0.0.0.0:4318 }

processors:
  memory_limiter: { check_interval: 1s, limit_mib: 1500 }
  redaction:                          # scrub PII from captured prompts/responses
    allow_all_keys: true
    blocked_values:
      - "[0-9]{3}-[0-9]{2}-[0-9]{4}"                  # SSN
      - "[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+"            # email
  tail_sampling:                      # keep errors + slow runs, sample the rest
    decision_wait: 10s
    policies:
      - name: keep-errors
        type: status_code
        status_code: { status_codes: [ERROR] }
      - name: keep-slow
        type: latency
        latency: { threshold_ms: 5000 }
      - name: sample-the-rest
        type: probabilistic
        probabilistic: { sampling_percentage: 20 }
  batch: { timeout: 5s, send_batch_size: 1024 }

exporters:
  otlp/tempo: { endpoint: tempo:4317, tls: { insecure: true } }

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, redaction, tail_sampling, batch]
      exporters: [otlp/tempo]
```

### Metrics, too

MLflow can also emit span-duration metrics over OTLP:

```bash
export OTEL_METRICS_EXPORTER="otlp"
export OTEL_EXPORTER_OTLP_METRICS_ENDPOINT="http://otel-collector:4317"
export OTEL_EXPORTER_OTLP_METRICS_PROTOCOL="grpc"
export OTEL_METRIC_EXPORT_INTERVAL="60000"   # milliseconds
```

This produces the `mlflow.trace.span.duration` histogram (in milliseconds), labeled
by span type, root/child status, and experiment — a ready-made latency signal for
your dashboards. Token-based metrics and dollar cost you derive yourself from the
`gen_ai.usage.*` attributes (there's no built-in cost metric — see Step 5).

---

## Step 4 — Send to both at once (dual export)

In practice you usually want both backends: Databricks for the rich trace UI and the
eval loop during development, and your OpenTelemetry stack for production
aggregation, cost dashboards, and alerting. MLflow does this with one extra flag —
the **same trace** goes to both destinations.

```python
import os

os.environ["MLFLOW_ENABLE_DUAL_EXPORT"] = "true"
os.environ["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = "http://otel-collector:4317/v1/traces"
os.environ["OTEL_SERVICE_NAME"] = "support-agent"
os.environ["MLFLOW_ENABLE_OTEL_GENAI_SEMCONV"] = "true"   # gen_ai.* on the OTLP side

import mlflow
mlflow.set_tracking_uri("databricks")
mlflow.set_experiment("/Shared/support-agent")
mlflow.langchain.autolog()
# Each trace now lands in the Databricks Traces UI AND your collector simultaneously.
```

> **The dual-export flag name varies by surface.** Open-source MLflow documents
> `MLFLOW_TRACE_ENABLE_OTLP_DUAL_EXPORT=true` (tracking server + OTLP), while the
> Databricks integration documents `MLFLOW_ENABLE_DUAL_EXPORT=true` (Databricks +
> OTLP). Check the MLflow version you're running, set the variable that version
> documents, and verify a trace actually appears in *both* places before relying on
> it. Pin your MLflow version so the contract doesn't shift under you.

---

## Step 5 — Turn traces into cost: derive dollars from tokens

MLflow captures token counts on every model span, but there is no built-in dollar
metric — pricing is provider-specific. Derive cost yourself by applying a price table
to the standard token attributes, and price **cache reads** separately, since
providers bill them at a steep discount.

```python
from opentelemetry import metrics

# USD per 1K tokens — keep current per provider/model.
PRICING = {
    "claude-3-5-sonnet": {"in": 0.003, "out": 0.015, "cache_read": 0.0003, "cache_write": 0.00375},
    "gpt-4o":            {"in": 0.005, "out": 0.015, "cache_read": 0.0025,  "cache_write": 0.005},
}

meter = metrics.get_meter("support-agent")
cost_counter = meter.create_counter("app.gen_ai.cost.usd", unit="usd")   # custom → app.* prefix

def record_cost(model, in_tok, out_tok, cache_read_tok, cache_write_tok, user_tier):
    p = PRICING[model]
    usd = ((in_tok / 1000) * p["in"]
           + (out_tok / 1000) * p["out"]
           + (cache_read_tok / 1000) * p["cache_read"]
           + (cache_write_tok / 1000) * p["cache_write"])
    cost_counter.add(usd, {
        "gen_ai.request.model": model,
        "gen_ai.provider.name": "anthropic",
        "user.tier": user_tier,        # slice spend by customer tier
    })
```

Capturing `cache_read` separately is not bookkeeping — providers bill cached input at
roughly a tenth of normal input, so if you only count `input_tokens` you'll
over-report cost on every cached call.

---

## Step 6 — The dashboard and alerts worth building

With traces flowing, build a dashboard with one row per thing you care about so
anyone — engineer or exec — can read it at a glance:

| Row | Panels |
|---|---|
| **Agents** | Task success rate · model-calls-per-run (loop alarm) · tool error rate · P95 end-to-end latency |
| **LLMs** | Tokens/day by model & token type · cost/day by model · cache-hit ratio · time-to-first-token · P95 model latency · error rate by provider |
| **Users** | Top users by spend · cost by tenant · calls by tier · anomalous-user alert |

And the alerts that earn their keep:

- `model_calls_per_run > 6` → reasoning-loop tripwire
- `llm_cost_burn_rate > 3× rolling avg` → runaway spend, caught in seconds not at month-end
- `task_success_rate < 90%` over 15 min → quality regression or model drift
- a single tool's `tool_error_rate` spiking → dependency problem
- one `enduser.id` exceeding N% of total calls in an hour → possible abuse

---

## A sensible rollout

You don't deploy all of this at once.

**Phase 1 — See something (days).** Add `mlflow.<framework>.autolog()` to your
highest-traffic agent and point it at Databricks with
`mlflow.set_tracking_uri("databricks")`. One line, full trace trees, zero pipeline
work. Stop flying blind today.

**Phase 2 — Standardize (weeks).** Turn on `MLFLOW_ENABLE_OTEL_GENAI_SEMCONV=true`
and point the OTLP exporter at your collector. Add manual `@mlflow.trace` spans where
autolog can't reach. Now every agent's telemetry composes on the same `gen_ai.*`
vocabulary.

**Phase 3 — Attribute and alert (a quarter).** Stamp `enduser.id` / tenant / tier via
`update_current_trace`, derive the cost counter, build the three-row dashboard, and
wire the alerts. Flip on dual export so production runs hit both backends.

**Phase 4 — Close the loop (ongoing).** Use MLflow's evaluation on Databricks to
score production traces, feed failures into eval datasets, and run continuous canary
evals. Observability stops being insurance and becomes how you make the agents
better.

---

## The takeaway

MLflow gives you the easiest agent instrumentation available — one `autolog()` line
for most frameworks, deep agent-aware span trees for the rest — and on Databricks it
comes with a managed trace UI, evaluation, and lineage out of the box. The reason it
doesn't trap you is that it's OpenTelemetry underneath: the very same instrumentation
can stream straight into your own collector and on to any backend you like.

Two settings make all of this true. `MLFLOW_ENABLE_OTEL_GENAI_SEMCONV=true` makes
MLflow speak the standard `gen_ai.*` conventions so its traces aggregate with
everything else, and the dual-export flag lets one trace serve both Databricks and
your collector at once. Set those, instrument every agent once, and the day you
outgrow a backend you change one config — your instrumentation comes right along.

---

### References

- *OpenTelemetry Integration | MLflow* — https://mlflow.org/docs/latest/genai/tracing/opentelemetry/
- *Export MLflow Traces/Metrics via OTLP | MLflow* — https://mlflow.org/docs/latest/genai/tracing/opentelemetry/export/
- *Full OpenTelemetry Support in MLflow Tracing (blog)* — https://mlflow.org/blog/opentelemetry-tracing-support/
- *OpenTelemetry Export | Databricks* — https://docs.databricks.com/aws/en/mlflow3/genai/tracing/integrations/open-telemetry
- *LLM Tracing and Agent Observability | MLflow* — https://mlflow.org/docs/latest/genai/tracing/
