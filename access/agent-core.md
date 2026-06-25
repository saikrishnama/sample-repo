# Inside the Black Box: A Deep Dive into Amazon Bedrock AgentCore Observability

The first time one of our agents misbehaved in production, the symptom was a support
ticket that said "the assistant just stopped." No stack trace, no error, no obvious
failure. The agent had quietly looped — calling the same tool eleven times, burning
tokens, and timing out before it ever answered. We found it the slow way: by reading
raw logs line by line for two hours. What we *wanted* was a button that said "show me
this one conversation, every step the agent took, what it cost, and where it went
sideways."

That button is what **Amazon Bedrock AgentCore Observability** gives you. It traces,
debugs, and monitors agents in production — visualizing every step of the agent
workflow so you can inspect the execution path, audit intermediate outputs, and find
the bottleneck or the failure without the archaeology. And it does it the right way:
AgentCore emits telemetry in **standardized OpenTelemetry (OTEL) format** and lands
it in **Amazon CloudWatch**, so it plugs into the monitoring stack you already have
instead of becoming another island.

This is the full deep dive. I'll walk the mental model (sessions → traces → spans),
the built-in telemetry you get for free, the one-time setup that makes traces show
up, and then the part that actually matters day to day: **how to instrument your
agent code** — Strands, LangGraph/LangChain, CrewAI, and a hand-rolled custom agent —
with working examples. By the end you'll be able to open the CloudWatch GenAI
Observability dashboard and read any run like a story.

---

## The architecture at a glance

AgentCore Observability has three moving parts: your **instrumented agent** emitting
OTEL, the **AgentCore service** adding its own built-in telemetry, and **CloudWatch**
storing all of it and rendering the GenAI Observability dashboard on top.

```
  Your agent code (Strands / LangGraph / CrewAI / custom)
        │
        │  ADOT SDK  →  opentelemetry-instrument python my_agent.py
        │  (+ OpenInference / OpenLLMetry / OpenLit / Traceloop spans)
        ▼
  ┌─────────────────────────────────────────────┐
  │  AgentCore Runtime                            │
  │   • built-in metrics (sessions, latency,      │   OTLP (http/protobuf)
  │     duration, token usage, error rates)       │ ─────────────────────┐
  │   • service spans (memory, gateway, tools)    │                      │
  └─────────────────────────────────────────────┘                      ▼
                                                          ┌──────────────────────────┐
                                                          │  Amazon CloudWatch        │
                                                          │   • /aws/spans/default    │
                                                          │     (traces)              │
                                                          │   • metrics (EMF,         │
                                                          │     ns: bedrock-agentcore)│
                                                          │   • Logs (runtime + otel) │
                                                          │   • X-Ray Transaction     │
                                                          │     Search                │
                                                          └──────────────┬───────────┘
                                                                         ▼
                                                       CloudWatch GenAI Observability
                                                       (trace waterfalls · span metrics ·
                                                        error breakdowns · session views)
```

Two things to internalize up front:

- **It's OpenTelemetry underneath.** AgentCore doesn't invent a proprietary format.
  Your agent emits OTEL spans and metrics through the **AWS Distro for OpenTelemetry
  (ADOT)**, following the GenAI semantic conventions. That's why the same
  instrumentation works whether the agent runs *inside* the AgentCore runtime or
  somewhere else entirely, and why you can later fan the same data to a third-party
  platform.
- **CloudWatch is the backend.** All metrics, spans, and logs land in CloudWatch.
  The **GenAI Observability** page in the CloudWatch console is where humans actually
  look — trace visualizations, custom span-metric graphs, and error breakdowns for
  your agent runtime.

---

## Component 1 — The mental model: sessions, traces, spans

Everything in AgentCore observability is a three-tiered hierarchy. Get this model
right and the dashboard reads itself.

| Level | What it is | Real-world analogue |
|---|---|---|
| **Session** | A complete interaction context between a user and an agent — the whole conversation, with state and history. Has a unique session ID. | The entire support chat |
| **Trace** | A single request-response cycle within a session: one user turn, including every internal step and any calls to other agents. | One question and its answer |
| **Span** | A discrete unit of work inside a trace, with a start/end time and parent-child nesting. | "parse input," "retrieve context," "call model," "run tool" |

A session contains many traces; a trace contains many spans. So when a user reports
"the assistant stopped," you start at the **session**, drill into the **trace** for
the turn that broke, and read the **span** tree to see exactly which operation
consumed the time or threw the error — "process user query" expanding into "parse
input → retrieve context → generate response → format output," each with its own
duration and status.

This is why session IDs matter so much (Component 4): without a consistent session
ID, your traces scatter and you lose the ability to follow one user's journey across
turns.

---

## Component 2 — What you get for free (built-in telemetry)

Before you write a single line of instrumentation, AgentCore emits a set of
**built-in metrics** for agents, gateways, memory, built-in tools, and identity
resources. These land in CloudWatch automatically for agents running in the
AgentCore runtime. The key agent-level ones:

- **Session count** — how many distinct interaction contexts
- **Latency** and **duration** — how long requests and operations take
- **Token usage** — consumption per agent
- **Error rates** — how often things fail

For **memory resources**, AgentCore also emits **spans and logs** by default (once
tracing is enabled). For everything else — your agent's reasoning steps, tool calls,
and custom metrics — you have to instrument the code yourself (Component 5). The
division is simple:

> **Free:** service-level metrics, plus memory spans/logs.
> **Instrument it yourself:** your agent's traces, custom spans, custom metrics, and
> the rich trace waterfall in the GenAI dashboard.

Exactly what each resource type provides by default, and where it surfaces:

| Resource | Service-provided data | In GenAI Observability dashboard | In CloudWatch (Logs/metrics) |
|---|---|---|---|
| **Agent** (runtime) | Metrics | ✅ Yes | ✅ Yes |
| **Memory** | Metrics, Spans\*, Logs\* | ❌ No | ✅ Yes |
| **Gateway** | Metrics | ❌ No | ✅ Yes |
| **Tools** (built-in) | Metrics | ❌ No | ✅ Yes |
| **Payments** | Metrics, Spans, Logs | ❌ No | ✅ Yes |
| **Policy** | Metrics, Spans\*\* | ✅ Yes (under Gateway tab) | ✅ Yes |

<small>\* Memory spans and logs require enablement (toggle tracing + configure a log
destination). \*\* Policy observability appears under the AgentCore Gateway tab in the
GenAI dashboard.</small>

The thing to notice: only **Agent** (and Policy) data renders in the rich GenAI
Observability dashboard. Memory, gateway, and tools metrics are in CloudWatch but you
view them through the regular Metrics/Logs consoles. Metrics publish to the
**`bedrock-agentcore`** namespace in CloudWatch's Enhanced Metric Format (EMF).

---

## Component 3 — The one-time setup that makes traces appear

This is the step people skip and then wonder why the dashboard is empty. Agent
*metrics* show up on their own, but **spans and traces require CloudWatch Transaction
Search**, which you enable once per account.

### Enable CloudWatch Transaction Search

In the console: **CloudWatch → Application Signals (APM) → Transaction search →
Enable Transaction Search**, and check the box to **ingest spans as structured
logs**. Or do it via the API in three steps.

First, add a resource policy letting X-Ray write spans to CloudWatch Logs:

```bash
aws logs put-resource-policy \
  --policy-name AgentCoreTransactionSearch \
  --policy-document '{
    "Version": "2012-10-17",
    "Statement": [{
      "Sid": "TransactionSearchXRayAccess",
      "Effect": "Allow",
      "Principal": { "Service": "xray.amazonaws.com" },
      "Action": "logs:PutLogEvents",
      "Resource": [
        "arn:aws:logs:us-east-1:123456789012:log-group:aws/spans:*",
        "arn:aws:logs:us-east-1:123456789012:log-group:/aws/application-signals/data:*"
      ],
      "Condition": {
        "ArnLike": { "aws:SourceArn": "arn:aws:xray:us-east-1:123456789012:*" },
        "StringEquals": { "aws:SourceAccount": "123456789012" }
      }
    }]
  }'
```

Then point trace segments at CloudWatch Logs, and (optionally) set a sampling rate:

```bash
# Send X-Ray trace segments to CloudWatch Logs
aws xray update-trace-segment-destination --destination CloudWatchLogs

# Optional: sample 10% of traces instead of the default
aws xray update-indexing-rule --name "Default" \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 10}}'
```

Once this is on, agent spans land in the **`/aws/spans/default`** log group and become
visible in the GenAI Observability dashboard. For **memory** and **gateway** resources
you additionally toggle **Tracing → Enable** on each resource (Transaction Search must
be on first).

> **Why this is separate.** Metrics are cheap and always-on; full trace ingestion has
> cost and cardinality implications, so AWS makes you opt in deliberately and gives
> you a sampling knob. Turn sampling down in high-traffic accounts — but keep it high
> enough that you still catch the rare failure, because for agents the rare failure
> is the whole point.

---

## Component 4 — Sessions and trace propagation

A session is only useful if every trace for a conversation carries the *same* session
ID. AgentCore gives you two mechanisms depending on where the agent runs.

### Inside the AgentCore runtime: the session header

When you invoke an agent in the runtime, pass the session ID as a header and ADOT
propagates it downstream automatically:

```python
import boto3

client = boto3.client("bedrock-agentcore", region_name="us-west-2")

response = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:111122223333:runtime/support_agent-nIg2xk3VSR",
    runtimeSessionId="a1b2c3d4-5678-90ab-cdef-EXAMPLEaaaaa",   # ← becomes the session.id
    payload='{"query": "Plan a weekend in Seattle"}',
)
```

`runtimeSessionId` maps to the **`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`**
header; ADOT reads it and sets `session.id` correctly on every span in the trace.

You can attach other observability headers on invocation too:

| Header | Purpose | Example |
|---|---|---|
| `X-Amzn-Trace-Id` | X-Ray trace ID (continue an existing trace) | `Root=1-5759e988-...;Parent=53995c3f42cd8ad8;Sampled=1` |
| `traceparent` | W3C trace context for cross-service correlation | `00-4bf92f3577b34da6...-00f067aa0ba902b7-01` |
| `baggage` | Propagate custom key-value context | `userId=alice,serverRegion=us-east-1` |
| `tracestate` | Vendor-specific tracing state | `congo=t61rcWkgMzE` |

If you don't supply a trace ID, OTEL auto-generates one. To stitch your agent into a
larger distributed trace (e.g. a web frontend → API → agent), pass your existing
`traceparent` and the spans link up.

### Outside the runtime: set session.id in baggage

If your agent runs outside the AgentCore runtime, set the session ID into OTEL
baggage yourself before processing a request:

```python
from opentelemetry import baggage, context

def begin_session(session_id: str):
    ctx = baggage.set_baggage("session.id", session_id)
    return context.attach(ctx)   # makes it active; every child span inherits session.id
```

---

## Component 5 — Instrumenting your agent (the part that matters)

Built-in metrics tell you *that* something is slow or failing; instrumentation tells
you *why*. The recipe is the same regardless of framework:

1. Add the ADOT SDK and boto3 to your dependencies.
2. Run your agent under `opentelemetry-instrument`.
3. Let your framework's GenAI instrumentation produce the spans.

```txt
# requirements.txt
aws-opentelemetry-distro>=0.10.0
boto3
```

```bash
pip install "aws-opentelemetry-distro>=0.10.0" boto3
```

The launch command is the magic — `opentelemetry-instrument` injects the SDK onto the
Python path so spans flow without you wiring up exporters by hand:

```bash
opentelemetry-instrument python my_agent.py
```

In a container, make it the entrypoint:

```dockerfile
# Dockerfile
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["opentelemetry-instrument", "python", "main.py"]
```

AgentCore supports the major GenAI instrumentation libraries —
**[OpenInference](https://github.com/Arize-ai/openinference)**,
**[OpenLLMetry](https://github.com/traceloop/openllmetry)**,
**[OpenLit](https://github.com/openlit/openlit)**, and
**[Traceloop](https://www.traceloop.com/docs/introduction)** — any of which patch
your framework to emit GenAI-convention spans (model, tokens, latency) automatically.

### Example A — Strands

Strands has built-in OTEL support; you just tell its tracer to emit, then launch
under `opentelemetry-instrument`.

```python
# my_agent.py  →  run with:  opentelemetry-instrument python my_agent.py
from strands import Agent
from strands.models import BedrockModel

agent = Agent(
    model=BedrockModel(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0"),
    system_prompt="You are a travel planning assistant.",
    # Strands emits OTEL spans for the agent loop, model calls, and tool calls.
    trace_attributes={
        "agent.name": "travel-agent",
        "agent.version": "1.0.0",
    },
)

result = agent("Plan a weekend in Seattle")
# A trace now exists: an invoke_agent span with child chat + execute_tool spans,
# all tagged with the session.id from the invocation header.
```

### Example B — LangGraph / LangChain

LangChain-family agents come with OTEL + GenAI convention support and can also be
auto-instrumented. Add the instrumentation and run under ADOT:

```python
# graph_agent.py  →  opentelemetry-instrument python graph_agent.py
from openinference.instrumentation.langchain import LangChainInstrumentor
LangChainInstrumentor().instrument()        # patches LangChain/LangGraph to emit spans

from langgraph.prebuilt import create_react_agent
from langchain_aws import ChatBedrock

llm = ChatBedrock(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0")
agent = create_react_agent(llm, tools=[search_tool, lookup_order])

# Each node, model call, and tool invocation becomes a span in one trace.
result = agent.invoke({"messages": [("user", "Where is my order #4821?")]})
```

### Example C — CrewAI

```python
# crew_agent.py  →  opentelemetry-instrument python crew_agent.py
from openlit import init as openlit_init
openlit_init()                              # emit GenAI-convention spans for CrewAI

from crewai import Agent, Task, Crew

researcher = Agent(role="Researcher", goal="Find facts", backstory="...")
task = Task(description="Research Seattle weekend activities", agent=researcher)
crew = Crew(agents=[researcher], tasks=[task])

result = crew.kickoff()
# Crew → Agent → Task → tool/model calls all nest as parent-child spans.
```

### Example D — A custom agent with hand-rolled spans

When you're not on a supported framework, create spans by hand with the OTEL API.
Follow the GenAI conventions so the data lands cleanly in the dashboard, and keep
anything custom under your own namespace.

```python
# custom_agent.py  →  opentelemetry-instrument python custom_agent.py
from opentelemetry import trace, baggage, context
from opentelemetry.trace import SpanKind
import boto3, json, time

tracer = trace.get_tracer("support-agent")
bedrock = boto3.client("bedrock-runtime", region_name="us-west-2")

def run_agent(task: str, session_id: str, user_id: str) -> str:
    # Tie this whole run to a session so traces group correctly.
    context.attach(baggage.set_baggage("session.id", session_id))

    with tracer.start_as_current_span(
        "invoke_agent support-agent",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.provider.name": "aws.bedrock",
            "gen_ai.agent.name": "support-agent",
            "session.id": session_id,
            "enduser.id": user_id,            # pseudonymous id, never raw PII
        },
    ) as agent_span:
        model_calls, tool_calls = 0, 0

        # --- a model call as a child `chat` span ---
        with tracer.start_as_current_span(
            "chat claude-3-5-sonnet",
            kind=SpanKind.CLIENT,
            attributes={
                "gen_ai.operation.name": "chat",
                "gen_ai.provider.name": "aws.bedrock",
                "gen_ai.request.model": "anthropic.claude-3-5-sonnet-20241022-v2:0",
            },
        ) as chat_span:
            start = time.monotonic()
            resp = bedrock.invoke_model(
                modelId="anthropic.claude-3-5-sonnet-20241022-v2:0",
                body=json.dumps({
                    "anthropic_version": "bedrock-2023-05-31",
                    "max_tokens": 1024,
                    "messages": [{"role": "user", "content": task}],
                }),
            )
            body = json.loads(resp["body"].read())
            usage = body["usage"]
            chat_span.set_attribute("gen_ai.usage.input_tokens", usage["input_tokens"])
            chat_span.set_attribute("gen_ai.usage.output_tokens", usage["output_tokens"])
            chat_span.set_attribute("gen_ai.response.finish_reasons", [body["stop_reason"]])
            model_calls += 1

        # --- a tool call as a child `execute_tool` span ---
        with tracer.start_as_current_span(
            "execute_tool lookup_order",
            kind=SpanKind.INTERNAL,
            attributes={
                "gen_ai.operation.name": "execute_tool",
                "gen_ai.tool.name": "lookup_order",
            },
        ):
            tool_calls += 1
            # ... run the tool ...

        # Cheapest failure detectors — keep custom keys out of gen_ai.*
        agent_span.set_attribute("app.agent.model_calls", model_calls)
        agent_span.set_attribute("app.agent.tool_calls", tool_calls)
        if model_calls > 6:
            agent_span.add_event("possible_reasoning_loop", {"model_calls": model_calls})

        return "done"
```

> **Namespace discipline.** The `gen_ai.*` namespace is owned by the OpenTelemetry
> spec — use it for standard attributes (operation, provider, model, usage). Anything
> you invent, like `model_calls`, goes under your own prefix (`app.*`) so your
> telemetry stays forward-compatible.

---

## Component 6 — Agents hosted *outside* the AgentCore runtime

A genuinely useful property: you can ship telemetry to CloudWatch GenAI Observability
from agents that don't run in AgentCore at all — a Lambda, an ECS task, a box under
your desk. Enable Transaction Search and add ADOT exactly as above, create a log
group for the agent, then set these environment variables.

```bash
# --- AWS credentials/region ---
export AWS_REGION=us-east-1
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...

# --- OTEL / ADOT configuration ---
export AGENT_OBSERVABILITY_ENABLED=true
export OTEL_PYTHON_DISTRO=aws_distro
export OTEL_PYTHON_CONFIGURATOR=aws_configurator          # ADOT Python only
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
export OTEL_TRACES_EXPORTER=otlp

# service.name + the CloudWatch log group your spans/metrics belong to
export OTEL_RESOURCE_ATTRIBUTES="service.name=support-agent,aws.log.group.names=/aws/bedrock-agentcore/runtimes/support-agent-id,cloud.resource_id=<AgentEndpointArn>"

# route logs/metrics to the right log group, stream, and metric namespace
export OTEL_EXPORTER_OTLP_LOGS_HEADERS="x-aws-log-group=/aws/bedrock-agentcore/runtimes/support-agent-id,x-aws-log-stream=runtime-logs,x-aws-metric-namespace=bedrock-agentcore"
```

Then launch the same way: `opentelemetry-instrument python my_agent.py`. The agent's
traces now appear in the GenAI Observability dashboard alongside your in-runtime
agents — same instrumentation, no code difference.

### Sending to a different platform instead

If you'd rather route AgentCore-runtime telemetry to Datadog, Grafana, Arize, or any
other OTEL-native platform, unset the default ADOT wiring with one variable and point
the standard OTEL exporter at your collector:

```bash
export DISABLE_ADOT_OBSERVABILITY=true
# then set your own OTEL_EXPORTER_OTLP_ENDPOINT / headers for the target platform
```

This is the portability dividend of being OTEL-native: the instrumentation in your
agent doesn't change, only where the data goes.

---

## Component 7 — Reading it back: the dashboard and what to watch

Open **CloudWatch → GenAI Observability**. With instrumentation flowing you get trace
waterfalls (the span tree per request), custom span-metric graphs, error breakdowns,
and session-level views. The signals worth building alarms on:

| Level | Watch for | Why |
|---|---|---|
| **Session** | session count, sessions with errors | usage trend; which conversations broke |
| **Trace** | P95 end-to-end latency, error rate per turn | slow or failing request-response cycles |
| **Span** | `model_calls` per run, tool error rate, per-span duration | reasoning loops, flaky tools, the slow step |
| **Cost** | token usage by agent/model | spend attribution and runaway detection |

And the alerts that earn their keep (CloudWatch alarms on the metrics above):

- `model_calls per run > 6` → reasoning-loop tripwire
- token-usage burn rate `> 3×` rolling average → runaway spend
- trace error rate `> 10%` over 15 min → quality regression
- a single tool's error rate spiking → dependency problem
- one session/user driving an anomalous share of calls → possible abuse

### Where exactly the data lives

When the dashboard isn't enough and you need the raw records, here's the map. Three
distinct things land in three distinct places:

| Data | Location | How to view |
|---|---|---|
| **Traces / spans** | `/aws/spans/default` | CloudWatch → **Transaction Search** (filter by service name, open a trace for the execution graph) |
| **Standard logs** (stdout/stderr, your `print`/`logging`) | `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>/[runtime-logs]<UUID>` | CloudWatch → Logs → Log groups |
| **OTEL structured logs** (execution detail, correlation IDs → traces) | `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>/otel-rt-logs` | CloudWatch → Logs → Log groups |
| **Metrics** (service + your custom EMF metrics) | namespace `bedrock-agentcore` | CloudWatch → Metrics |
| **Memory / gateway logs** | `/aws/vendedlogs/bedrock-agentcore/{memory|gateway}/APPLICATION_LOGS/{resource-id}` | CloudWatch → Logs → Log groups |

The OTEL structured logs are the useful ones for debugging — they carry correlation
IDs that link a log line back to the exact trace and span it came from.

### Querying spans with CloudWatch Logs Insights

Because spans are ingested as structured logs, you can query them directly. A few
that pull their weight — find the slowest agent runs:

```sql
-- Slowest invoke_agent spans in the last hour
fields @timestamp, attributes.`session.id`, attributes.`gen_ai.agent.name`, durationNano/1000000 as ms
| filter name = 'invoke_agent support-agent'
| sort ms desc
| limit 20
```

```sql
-- Token usage per model, to spot the expensive calls
fields attributes.`gen_ai.request.model` as model,
       attributes.`gen_ai.usage.input_tokens` as in_tok,
       attributes.`gen_ai.usage.output_tokens` as out_tok
| filter attributes.`gen_ai.operation.name` = 'chat'
| stats sum(in_tok) as input, sum(out_tok) as output by model
```

```sql
-- Runs that tripped the reasoning-loop event
fields @timestamp, attributes.`session.id`, attributes.`app.agent.model_calls` as calls
| filter attributes.`app.agent.model_calls` > 6
| sort calls desc
```

Run these against the `/aws/spans/default` log group (or your agent's `otel-rt-logs`
group for log-level queries).

---

## Component 8 — IAM: the permissions that make it work

Empty dashboards are most often a permissions problem. The agent's execution role
needs to write logs, spans, and metrics; an out-of-runtime agent's credentials need
the same. The minimal policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "AgentCoreObservabilityWrite",
      "Effect": "Allow",
      "Action": [
        "logs:CreateLogGroup",
        "logs:CreateLogStream",
        "logs:PutLogEvents",
        "logs:DescribeLogGroups",
        "logs:DescribeLogStreams",
        "cloudwatch:PutMetricData",
        "xray:PutTraceSegments",
        "xray:PutSpans",
        "xray:GetSamplingRules"
      ],
      "Resource": "*"
    }
  ]
}
```

Tighten `Resource` to your specific log-group ARNs in production. Separately — and
this is the one people miss — **Transaction Search itself** needs the X-Ray
resource-based policy from Component 3 (`logs:PutLogEvents` granted to
`xray.amazonaws.com`); that's an account-level grant, not part of the agent role.

---

## Component 9 — Monitoring across accounts

Most real deployments span accounts — dev, staging, prod, or one account per team.
You don't want to log into five consoles. **CloudWatch cross-account observability**
lets a single *monitoring account* display sessions, traces, and metrics from many
*source accounts* in one GenAI Observability view.

The model is a **sink** (in the monitoring account, accepts telemetry) linked to
**links** (one per source account, forwards telemetry). Console path: **CloudWatch →
Settings → Monitoring account configuration → Configure**, sharing at minimum
**Metrics** and **Logs**, then link source accounts via AWS Organizations
(recommended) or individually.

As infrastructure-as-code, create the sink in the monitoring account:

```yaml
# monitoring-account-sink.yaml
Resources:
  ObservabilitySink:
    Type: AWS::Oam::Sink
    Properties:
      Name: AgentCoreObservabilitySink
      Policy:
        Version: '2012-10-17'
        Statement:
          - Effect: Allow
            Principal: '*'
            Action: ['oam:CreateLink', 'oam:UpdateLink']
            Resource: '*'
            Condition:
              StringEquals: { aws:PrincipalOrgID: '<your-org-id>' }   # e.g. o-a1b2c3d4e5
              ForAllValues:StringEquals:
                oam:ResourceTypes: ['AWS::Logs::LogGroup', 'AWS::CloudWatch::Metric']
Outputs:
  SinkArn: { Value: !GetAtt ObservabilitySink.Arn }   # share with source accounts
```

Then a link in each source account, pointed at that sink ARN:

```yaml
# source-account-link.yaml  (deploy org-wide with CloudFormation StackSets)
Resources:
  ObservabilityLink:
    Type: AWS::Oam::Link
    Properties:
      LabelTemplate: '$AccountName'
      ResourceTypes: ['AWS::Logs::LogGroup', 'AWS::CloudWatch::Metric']
      SinkIdentifier: '<sink-arn-from-monitoring-account>'
```

Watch the constraints: it's **single-Region** (monitoring and source accounts must
share a Region), **both sides must share Metrics and Logs**, and cross-account data
only appears while the OAM link is live. You filter by **Account ID** in the sessions
and traces tables to scope to one account.

---

## Putting it all together: a complete end-to-end example

Here's the whole thing wired up for a containerized support agent, start to finish.

**1. Dependencies** (`requirements.txt`):

```txt
aws-opentelemetry-distro>=0.10.0
boto3
strands-agents
openinference-instrumentation-langchain   # if you use LangChain/LangGraph
```

**2. The agent** (`main.py`) — propagate the session ID, then let the framework emit:

```python
from opentelemetry import baggage, context
from strands import Agent
from strands.models import BedrockModel

def handler(event):
    # session id arrives on the invocation header; stamp it so every span groups correctly
    session_id = event.get("session_id", "unknown")
    context.attach(baggage.set_baggage("session.id", session_id))

    agent = Agent(
        model=BedrockModel(model_id="anthropic.claude-3-5-sonnet-20241022-v2:0"),
        system_prompt="You are a support agent.",
        trace_attributes={"agent.name": "support-agent", "agent.version": "1.0.0"},
    )
    return agent(event["query"])
```

**3. Container entrypoint** (`Dockerfile`) — launch under ADOT auto-instrumentation:

```dockerfile
FROM public.ecr.aws/docker/library/python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .
CMD ["opentelemetry-instrument", "python", "main.py"]
```

**4. One-time account setup** — enable Transaction Search (Component 3):

```bash
aws logs put-resource-policy --policy-name AgentCoreTransactionSearch \
  --policy-document file://xray-trust-policy.json
aws xray update-trace-segment-destination --destination CloudWatchLogs
```

**5. Invoke it with a session ID** (Component 4):

```python
import boto3
client = boto3.client("bedrock-agentcore", region_name="us-west-2")
resp = client.invoke_agent_runtime(
    agentRuntimeArn="arn:aws:bedrock-agentcore:us-west-2:111122223333:runtime/support_agent-XXXX",
    runtimeSessionId="a1b2c3d4-5678-90ab-cdef-EXAMPLEaaaaa",
    payload='{"query": "Where is my order #4821?", "session_id": "a1b2c3d4-5678-90ab-cdef-EXAMPLEaaaaa"}',
)
```

**6. View it** — open **CloudWatch → GenAI Observability**, find the session, open the
trace, and read the span waterfall: `invoke_agent` → `chat` (with token usage) →
`execute_tool`. The loop you couldn't see before is now one click deep.

---

## Troubleshooting: the dashboard is empty

The five things that account for almost every "I see nothing" report:

1. **Transaction Search isn't enabled.** No `/aws/spans/default`, no traces. Run the
   Component 3 setup — this is the #1 cause.
2. **You didn't launch under `opentelemetry-instrument`.** Running `python main.py`
   directly produces no spans. The auto-instrument wrapper is what loads ADOT.
3. **Missing IAM permissions.** No `logs:PutLogEvents` / `xray:PutTraceSegments` on
   the role → telemetry silently fails to write. See Component 8.
4. **Sampling set too low.** If `update-indexing-rule` is at 1%, low-traffic agents
   may show nothing. Raise it while validating, then lower it.
5. **Session ID not propagated.** Traces appear but don't group into sessions because
   `session.id` was never set (header on invoke, or baggage in code).

For metrics specifically: confirm they're publishing to the **`bedrock-agentcore`**
namespace in the Metrics console. For cross-account gaps: confirm the OAM link is
active and both accounts share Metrics + Logs in the same Region.

---

## Rolling it out without boiling the ocean

**Phase 1 — See the built-ins (a day).** Deploy an agent to the AgentCore runtime
and open the GenAI Observability page. You get session count, latency, duration,
token usage, and error rates with zero instrumentation. Stop flying blind.

**Phase 2 — Turn on traces (a day).** Enable CloudWatch Transaction Search (the
X-Ray resource policy + `update-trace-segment-destination`). Toggle tracing on for
memory/gateway resources. Now spans land in `/aws/spans/default`.

**Phase 3 — Instrument your agent (a week).** Add `aws-opentelemetry-distro`, pick an
instrumentation library for your framework, and launch under
`opentelemetry-instrument`. Propagate `session.id`. Now you get the full trace
waterfall and can debug any single run end to end.

**Phase 4 — Attribute and alert (ongoing).** Add custom `app.*` attributes
(`model_calls`, tool counts), stamp `enduser.id`, wire CloudWatch alarms on the
signals above, and — if you need it — fan the same OTEL stream to a second platform.

---

## The takeaway

AgentCore Observability solves the "the assistant just stopped" problem by giving you
a real execution tree for every agent run, stored in CloudWatch and rendered in the
GenAI Observability dashboard. The model is simple once it clicks — **sessions**
contain **traces** contain **spans** — and the setup is two deliberate steps: enable
Transaction Search so spans flow, then instrument your agent with ADOT so the spans
are rich.

The reason it's worth doing properly is the same reason the whole industry is
converging on OpenTelemetry: because AgentCore emits OTEL with GenAI conventions, the
instrumentation you write once works for in-runtime agents, out-of-runtime agents,
and any third-party platform you might move to later. You're not betting on a vendor;
you're joining a standard — and the day the rare failure happens at 3 AM, you open one
trace and read the whole story instead of grepping logs for two hours.

---

### References & further reading

- *Observe your agent applications on Amazon Bedrock AgentCore Observability* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html
- *Add observability to your AgentCore resources* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-configure.html
- *Understand observability for agentic resources (sessions, traces, spans)* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-telemetry.html
- *AgentCore generated observability data (service-provided metrics/spans/logs)* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-service-provided.html
- *View observability data for AgentCore agents* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-view.html
- *Monitor AgentCore resources across accounts* — https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability-cross-account.html
- *AWS Distro for OpenTelemetry (ADOT)* — https://aws-otel.github.io/
- *OpenTelemetry GenAI semantic conventions* — https://opentelemetry.io/docs/specs/semconv/gen-ai/
- *Instrumentation libraries* — OpenInference, OpenLLMetry, OpenLit, Traceloop
