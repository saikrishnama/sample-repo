# Watching the Machine Think: A Deep Dive into Monitoring AI Agents with Amazon Bedrock AgentCore Observability

> *How do you debug a system that reasons, chooses its own tools, and acts on a user's behalf — when the bug might be a sentence the model "decided" to write? This is the field guide to answering that question with Amazon Bedrock AgentCore Observability.*

---

## Why agents broke our old monitoring

For twenty years we got good at watching software that does what we told it to. A request comes in, it flows through a known set of functions, it returns a response. When something goes wrong, the stack trace points at a line of code. Dashboards track CPU, latency, and error codes, and that's mostly enough.

AI agents quietly demolish that model.

An agent **makes decisions on your user's behalf**. It **invokes tools dynamically** — deciding at runtime whether to call a calculator, hit a weather API, query a database, or spin up a browser. It **follows a reasoning path** that you didn't hard-code and can't fully predict. The result is what AWS calls an *accountability gap*: when an agent gives a wrong answer, the failure might not be in any line of your code at all. It might be in a tool the model chose not to call, a prompt that nudged the reasoning sideways, or a context window that quietly truncated.

Traditional metrics — CPU, 5xx counts, p99 latency — still matter, but they no longer tell you *why* an agent behaved the way it did. To answer that, you need to see things classic APM never captured:

- **Token usage** — your real cost and latency driver.
- **Tool-selection patterns** — *which* tools the agent picked, and whether it picked the right ones.
- **The reasoning trajectory** — the ordered chain of steps the agent took from prompt to answer.
- **End-to-end latency across the whole workflow**, not just per service.

The guiding principle from the AWS team is blunt: for agents, **observability has to be fundamental from day one, not bolted on after an incident.** Amazon Bedrock AgentCore Observability is AWS's answer to that — and because it speaks **OpenTelemetry (OTEL)** and stores everything in **Amazon CloudWatch**, it slots into the monitoring stack you already run instead of replacing it.

This post is a deep dive: the mental model, the architecture, the one-time setup, two concrete instrumentation paths (with code), a tour of every metric AgentCore emits, and how to actually read it all in the console.

---

## Part 1 — The mental model: Sessions → Traces → Spans

Everything in AgentCore Observability hangs off a three-level hierarchy. Get this right and the rest of the product makes sense; get it wrong and the dashboards look like noise.

```
┌─────────────────────────────────────────────────────────────────────┐
│  SESSION  —  the whole conversation with one user                     │
│  (unique runtimeSessionId, state + history, isolated from others)     │
│                                                                       │
│   ┌───────────────────────────────┐  ┌──────────────────────────┐    │
│   │  TRACE  — one request/response │  │  TRACE  — next turn       │    │
│   │  cycle (one agent invocation)  │  │                          │    │
│   │                                │  │   ┌────────────────────┐ │    │
│   │  ┌──────────┐  ┌────────────┐  │  │   │ SPAN: parse input  │ │    │
│   │  │ SPAN     │  │ SPAN       │  │  │   ├────────────────────┤ │    │
│   │  │ LLM call │  │ tool: calc │  │  │   │ SPAN: retrieve ctx │ │    │
│   │  └──────────┘  └────────────┘  │  │   ├────────────────────┤ │    │
│   │       │             │          │  │   │ SPAN: LLM generate │ │    │
│   │  ┌────▼─────────────▼───────┐  │  │   └────────────────────┘ │    │
│   │  │ SPAN: tool: weather API  │  │  │                          │    │
│   │  └──────────────────────────┘  │  │                          │    │
│   └───────────────────────────────┘  └──────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────┘
```

**Sessions** are the highest level — the *complete interaction context* between a user and an agent. A session encapsulates the entire conversation: it maintains state and context across exchanges, tracks conversation history, allocates resources, and — critically — keeps each user's interaction **isolated** from every other user's. Every session has a unique ID (the `runtimeSessionId`), and AgentCore gives you session-level metrics for runtime agents out of the box.

**Traces** sit in the middle. A trace is the detailed record of a *single request–response cycle* — one agent invocation, from the moment the request arrives to the moment the final response is generated. It captures input parameters, processing steps, every tool invocation (with its inputs, outputs, and execution time), resource utilization, errors, and how the response was built. Each trace belongs to exactly one session. **Traces require code instrumentation (ADOT) to be captured** — they are the richest signal and the one you have to opt into for your agent logic.

**Spans** are the atoms. A span is a discrete, measurable unit of work with a defined start and end time: "parse the user query," "retrieve context from memory," "call the weather tool," "format the output." Spans carry an operation name, timestamps, **parent–child relationships**, attributes/tags, events, and a success/failure status. Within a trace they form a **tree**, and that tree is what lets you see exactly which operation ate 3 seconds or threw the exception.

Why this structure is the whole point:

| You start at... | ...and drill down to | ...to answer |
|---|---|---|
| A **session** with weird behavior | its **traces** | "Which turn went wrong?" |
| A slow/failed **trace** | its **spans** | "Which step was the bottleneck or error?" |
| A specific **span** | its attributes/events | "What were the exact inputs/outputs?" |

That's progressive troubleshooting: session anomaly → trace pattern → span root cause.

---

## Part 2 — The architecture: OTEL in, CloudWatch out

Here's how the signals actually flow from your agent to a dashboard.

```
        YOUR AGENT (Strands / LangChain / CrewAI / custom)
                          │
                          │  emits OpenTelemetry signals
                          │  (metrics, traces/spans, logs)
                          ▼
        ┌───────────────────────────────────────────┐
        │   ADOT  (AWS Distro for OpenTelemetry)      │
        │   - auto on AgentCore Runtime               │
        │   - `opentelemetry-instrument` off-runtime  │
        └───────────────────────────────────────────┘
                          │
            ┌─────────────┼──────────────────┐
            ▼             ▼                  ▼
       ┌─────────┐  ┌────────────┐    ┌──────────────┐
       │ METRICS │  │ TRACES/    │    │   LOGS       │
       │  (EMF)  │  │  SPANS     │    │              │
       └────┬────┘  └─────┬──────┘    └──────┬───────┘
            │             │                  │
            ▼             ▼                  ▼
   ┌──────────────────────────────────────────────────────┐
   │                  AMAZON CLOUDWATCH                     │
   │                                                        │
   │  Metrics            Transaction Search       Log       │
   │  namespaces:        (aws/spans log group)    groups:   │
   │  • AWS/Bedrock-      ← powered by X-Ray       /aws/     │
   │    AgentCore           trace ingestion        bedrock-  │
   │  • AWS/Usage                                  agentcore │
   │  • bedrock-agentcore                          /aws/     │
   │    (your custom EMF metrics)                  vendedlogs│
   └──────────────────────────┬────────────────────────────┘
                              │
                              ▼
              ┌─────────────────────────────────┐
              │  CloudWatch GenAI Observability  │
              │  (Bedrock AgentCore tab)         │
              │  Agents · Sessions · Traces views│
              └─────────────────────────────────┘
```

Three things to internalize from this picture:

1. **Everything lands in CloudWatch.** Metrics, spans, and logs are all stored in CloudWatch and are viewable in the console or downloadable via the AWS CLI/SDKs. There's no separate datastore to manage.
2. **Traces ride on CloudWatch Transaction Search**, which is built on AWS X-Ray's trace ingestion. Spans are written to a special `aws/spans` log group. Enabling Transaction Search is the one-time switch that makes traces searchable (Part 4).
3. **There are two distinct "metric planes."** The AgentCore *service* publishes default metrics to `AWS/Bedrock-AgentCore` (and identity usage to `AWS/Usage`). Your *instrumented code* publishes custom metrics to the `bedrock-agentcore` namespace via CloudWatch's **Embedded Metric Format (EMF)** — collected automatically by ADOT, no extra code.

Because the whole pipeline is OTEL-compatible, you can also point it at a third-party observability platform instead of ADOT (more on `DISABLE_ADOT_OBSERVABILITY` later).

---

## Part 3 — What you get for free (service-provided data)

Before you write a single line of instrumentation, AgentCore already emits telemetry. **Metrics are on by default for every resource type.** Spans and logs are sometimes on by default and sometimes require you to flip a switch.

| Resource type | Service-provided data | Shows in GenAI Observability | In CloudWatch (Logs/Metrics) |
|---|---|---|---|
| **Agent** (Runtime) | Metrics, Spans\*, Logs\* | Yes | Yes |
| **Memory** | Metrics, Spans\*, Logs\* | Yes | Yes |
| **Payments** | Metrics, Spans, Logs | Yes | Yes |
| **Gateway** | Metrics, Spans, Logs\* | Yes | Yes |
| **Tools** (Code Interpreter, Browser) | Metrics, Spans\*, Logs\* | Yes | Yes |
| **Policy** | Metrics, Spans\*, Logs | Yes | Yes |

\* *An asterisk means that signal requires explicit enablement (e.g., toggling tracing on the resource, or configuring a log destination). Metrics are always provided by default.*

A few non-obvious notes that will save you confusion:

- **Memory resources emit spans and logs by default** (the only resource type with default span output) — agents and gateways need instrumentation/enablement for spans.
- **Policy observability is displayed under the Gateway tab** in GenAI Observability, not its own tab.
- **Viewing any spans/traces requires CloudWatch Transaction Search to be enabled** — that's the prerequisite we tackle next.

---

## Part 4 — The one-time prerequisite: enable CloudWatch Transaction Search

This is the single most important "gotcha" in the whole product. **Until you enable Transaction Search, AgentCore spans are not searchable** — your traces simply won't show up. It's a **once-per-account** setup, and after you enable it, **allow about 10 minutes** for spans to start flowing.

You can index **1% of traces at no cost**, and adjust the sampling percentage later. That free tier is plenty to get started.

### Option A — Console

There are two console paths depending on which CloudWatch UI you land in; both do the same thing:

- **Path 1:** CloudWatch console → **Settings → Account → X-Ray traces** tab → **Transaction Search** → **View settings → Edit → Enable Transaction Search** → under *"For X-Ray users"* enter the % of traces to index → **Save**. Wait until **"Ingest OpenTelemetry spans"** shows **Enabled** before sending traces.
- **Path 2 (newer console):** CloudWatch → **Application Signals (APM) → Transaction search → Enable Transaction Search** → check **"ingest spans as structured logs"** → **Save**.

You can also kick this off directly from the **Bedrock AgentCore panel** in the GenAI Observability console via a **Configure** button.

### Option B — API / CLI

If you'd rather script it (Infrastructure-as-Code, CI, etc.), it's three calls.

**Step 1 — Grant X-Ray permission to write spans into your CloudWatch Logs:**

```bash
aws logs put-resource-policy --policy-name MyResourcePolicy --policy-document '{
  "Version": "2012-10-17",
  "Statement": [
    {
      "Sid": "TransactionSearchXRayAccess",
      "Effect": "Allow",
      "Principal": { "Service": "xray.amazonaws.com" },
      "Action": "logs:PutLogEvents",
      "Resource": [
        "arn:partition:logs:region:account-id:log-group:aws/spans:*",
        "arn:partition:logs:region:account-id:log-group:/aws/application-signals/data:*"
      ],
      "Condition": {
        "ArnLike":      { "aws:SourceArn": "arn:partition:xray:region:account-id:*" },
        "StringEquals": { "aws:SourceAccount": "account-id" }
      }
    }
  ]
}'
```

**Step 2 — Send trace segments to CloudWatch Logs:**

```bash
aws xray update-trace-segment-destination --destination CloudWatchLogs
```

**Step 3 (optional) — Set the trace sampling percentage:**

```bash
aws xray update-indexing-rule --name "Default" \
  --rule '{"Probabilistic": {"DesiredSamplingPercentage": 5}}'
```

> **Heads-up on `aws:SourceArn`:** AWS's own pages show this condition two ways — some examples scope it to the **X-Ray** service (`arn:...:xray:...`, shown above) and one expanded example uses `arn:...:logs:...`. Since the principal here is the X-Ray service writing on your behalf, the X-Ray-scoped form is the intended one. Worth double-checking against the current docs before you commit it to IaC.

Once Transaction Search is on, spans land in the **`aws/spans`** log group (you'll also see it referenced as `/aws/spans/default`), and *then* you can enable per-resource tracing for memory, gateway, tools, and identity by editing each resource's **Tracing** setting and toggling **Enable**.

---

## Part 5 — One pipeline, two on-ramps: which setup is yours?

A point worth making before any code: **the monitoring you get is identical either way.** Same sessions, same traces, same spans, same dashboards, same metrics. The OTEL → ADOT → CloudWatch pipeline from Part 2 doesn't care where your agent runs. The *only* thing that changes is the **on-ramp** — how telemetry gets into that pipeline in the first place.

There are two on-ramps, and you don't choose between them on preference — your deployment chooses for you:

| | **On-ramp 1: AgentCore Runtime** | **On-ramp 2: Anywhere else** |
|---|---|---|
| **You run on** | AgentCore Runtime | EC2, EKS, Lambda, another cloud, your laptop |
| **Instrumentation** | Automatic — the runtime wires up OTEL for you | Manual — you add ADOT + `opentelemetry-instrument` |
| **Code changes** | Wrap in `BedrockAgentCoreApp` (~4 lines) | None to the agent; wrap the *launch* command |
| **Env vars to set** | None | ~8 (`AGENT_OBSERVABILITY_ENABLED`, OTEL exporters…) |
| **Log group** | Auto-created | You pre-create it |
| **Session ID set via** | HTTP header (`X-Amzn-...-Session-Id`) | OTEL baggage (`session.id`) |
| **Setup effort** | Near zero | A one-time checklist |
| **Resulting visibility** | **Identical** | **Identical** |

> **Decision rule:** If your agent runs *in* the AgentCore Runtime, use on-ramp 1 (Part 6) and skip the manual setup entirely. If it runs *anywhere else*, use on-ramp 2 (Part 7). That's the whole decision — read only the section that matches your deployment.

The next two parts walk each on-ramp end-to-end. They're deliberately parallel, so once you've identified yours, you can ignore the other.

---

## Part 6 — On-ramp 1: Runtime-hosted agents (zero-effort instrumentation)

If you deploy your agent into the **AgentCore Runtime**, observability is essentially free. When you deploy via the AgentCore CLI, **the runtime automatically instruments your agent with OpenTelemetry** — no extra OTEL libraries, no config files, no env vars. You wrap your existing agent in a few lines of the `BedrockAgentCoreApp` SDK and the runtime captures session metrics, performance data, error tracking, and full execution traces for you.

### The agent

Here's a minimal Strands agent with two tools — a calculator and a (very optimistic) weather function:

```python
# app/StrandsClaudeGettingStarted/main.py
from strands import Agent, tool
from strands_tools import calculator
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands.models import BedrockModel

app = BedrockAgentCoreApp()

@tool
def weather():
    """Get weather"""
    return "sunny"

model = BedrockModel(
    model_id="us.anthropic.claude-3-7-sonnet-20250219-v1:0",
)
agent = Agent(
    model=model,
    tools=[calculator, weather],
    system_prompt="You're a helpful assistant. You can do simple math calculation, and tell the weather."
)

@app.entrypoint
def strands_agent_bedrock(payload):
    """Invoke the agent with a payload"""
    user_input = payload.get("prompt")
    response = agent(user_input)
    return response.message['content'][0]['text']

if __name__ == "__main__":
    app.run()
```

The only "observability code" here is `BedrockAgentCoreApp()` and the `@app.entrypoint` decorator. That's it.

### Deploy and invoke (CLI)

```bash
npm install -g @aws/agentcore
agentcore create --name StrandsClaudeGettingStarted
cd StrandsClaudeGettingStarted
agentcore deploy
agentcore invoke
```

### Deploy and invoke (starter toolkit, Python)

If you prefer notebooks/SDK over the CLI:

```python
from bedrock_agentcore_starter_toolkit import Runtime
from boto3.session import Session

boto_session = Session()
region = boto_session.region_name
agentcore_runtime = Runtime()
agent_name = "strands_claude_getting_started"

response = agentcore_runtime.configure(
    entrypoint="strands_claude.py",
    auto_create_execution_role=True,
    auto_create_ecr=True,
    requirements_file="requirements.txt",
    region=region,
    agent_name=agent_name,
)
launch_result = agentcore_runtime.launch()
invoke_response = agentcore_runtime.invoke({"prompt": "How is the weather now?"})
```

### Invoke programmatically with boto3

```python
import boto3, json

client = boto3.client('bedrock-agentcore')

response = client.invoke_agent_runtime(
    agentRuntimeArn="YOUR_AGENT_RUNTIME_ARN",
    runtimeSessionId="my-observability-session-001",   # <-- groups traces into a session
    payload=json.dumps({"prompt": "What is 2 + 2?"}),
    qualifier="DEFAULT"
)
print(json.loads(response['response'].read()))
```

Notice `runtimeSessionId`. Pass the **same** session ID across related requests and AgentCore stitches their traces into one session — which is exactly what makes the Sessions view useful.

### Enhanced control via custom headers

When you invoke the runtime over HTTP, you can pass optional headers to control trace and session correlation precisely:

| Header | What it does | Example |
|---|---|---|
| `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` | Sets the AgentCore session ID | `a1b2c3d4-5678-90ab-cdef-EXAMPLEaaaaa` |
| `X-Amzn-Trace-Id` | X-Ray-format trace ID (OTEL auto-generates one if absent) | `Root=1-5759e988-bd862e3fe1be46a994272793;Parent=53995c3f42cd8ad8;Sampled=1` |
| `traceparent` | W3C trace-context header | `00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01` |
| `baggage` | Arbitrary key-value context propagation | `userId=alice,serverRegion=us-east-1` |
| `tracestate` | Vendor-specific trace state | — |
| `mcp-session-id` | MCP session identifier | — |

`Sampled=1` in the X-Ray header means "sample this trace at 100%" — handy when you're actively debugging a specific request and want to guarantee it's captured.

The same `X-Amzn-Trace-Id` / `traceparent` propagation is supported by the built-in **tools** APIs (`StartCodeInterpreterSession`, `InvokeCodeInterpreter`, `StopCodeInterpreterSession`, `StartBrowserSession`, `StopBrowserSession`) and `X-Amzn-Trace-Id` by the **identity** APIs (`GetWorkloadAccessToken`, `GetResourceOauth2Token`, `GetResourceAPIKey`, etc.), so a single trace ID can follow a request across primitives.

---

## Part 7 — On-ramp 2: Open-source / third-party agents (off the runtime)

This is the other on-ramp from Part 5. You might run on EC2, EKS, Lambda, a container somewhere, or another cloud entirely. As the comparison table promised, **you get exactly the same monitoring** — the only difference is the *initial setup*. You add the ADOT SDK, set a handful of environment variables to point telemetry at CloudWatch, and launch your process under the `opentelemetry-instrument` wrapper.

This works natively with **Strands, LangChain/LangGraph, and CrewAI**, which emit OTEL and GenAI semantic conventions out of the box (sometimes via an auto-instrumentor like `opentelemetry-instrumentation-langchain`). For other frameworks, AgentCore supports the popular instrumentation libraries: **OpenInference, OpenLLMetry, OpenLit, and Traceloop.**

### Step 1 — Dependencies

```text
# requirements.txt
aws-opentelemetry-distro>=0.10.0
boto3
```

```bash
pip install aws-opentelemetry-distro>=0.10.0 boto3
# for the Strands example, also:
pip install 'strands-agents[otel]'
```

### Step 2 — Create the CloudWatch log group/stream first

Unlike the runtime (which auto-creates its log group), off-runtime you must **pre-create** the CloudWatch log group and stream that your telemetry will target, then reference them in the env vars below.

### Step 3 — Environment variables

AWS credentials/region:

```bash
export AWS_ACCOUNT_ID=<account id>
export AWS_DEFAULT_REGION=<default region>
export AWS_REGION=<region>
export AWS_ACCESS_KEY_ID=<access key id>
export AWS_SECRET_ACCESS_KEY=<secret key>
```

OTEL / ADOT pipeline (the inline comments are straight from AWS's docs):

```bash
export AGENT_OBSERVABILITY_ENABLED=true            # Activates the ADOT pipeline
export OTEL_PYTHON_DISTRO=aws_distro               # Use AWS Distro for OpenTelemetry
export OTEL_PYTHON_CONFIGURATOR=aws_configurator   # AWS configurator for ADOT SDK (Python only)
export OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf    # Export protocol
export OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=<YOUR-LOG-GROUP>,x-aws-log-stream=<YOUR-LOG-STREAM>,x-aws-metric-namespace=<YOUR-NAMESPACE>
export OTEL_RESOURCE_ATTRIBUTES=service.name=<YOUR-AGENT-NAME>   # Identifies your agent
export OTEL_TRACES_EXPORTER=otlp
```

A fuller `OTEL_RESOURCE_ATTRIBUTES` form (from the configure guide) ties your telemetry to a specific AgentCore log group and endpoint:

```bash
export OTEL_RESOURCE_ATTRIBUTES=service.name=<agent-name>,aws.log.group.names=/aws/bedrock-agentcore/runtimes/<agent-id>,cloud.resource_id=<AgentEndpointArn:AgentEndpointName>
```

### Step 4 — A non-runtime Strands agent

```python
# agent.py
from strands import Agent
from strands_tools import http_request

WEATHER_SYSTEM_PROMPT = """You are a weather assistant with HTTP capabilities. You can:
1. Make HTTP requests to the National Weather Service API
2. Process and display weather forecast data
3. Provide weather information for locations in the United States
..."""

weather_agent = Agent(
    system_prompt=WEATHER_SYSTEM_PROMPT,
    tools=[http_request],
)

response = weather_agent("What's the weather like in Seattle?")
print(response)
```

### Step 5 — Run it under auto-instrumentation

```bash
opentelemetry-instrument python agent.py
```

The `opentelemetry-instrument` wrapper reads your OTEL env vars, auto-instruments Strands/Bedrock calls, tool calls, and database calls, and ships traces to CloudWatch — all without changing the agent code. In a container, it's just your `CMD`:

```dockerfile
CMD ["opentelemetry-instrument", "python", "main.py"]
```

### Session correlation off-runtime

On the runtime you set the session via an HTTP header. Off-runtime, you set it through **OTEL baggage**:

```python
from opentelemetry import baggage, context

ctx = baggage.set_baggage("session.id", session_id)   # tag this work with a session
context.attach(ctx)                                   # make it the active context
```

```bash
opentelemetry-instrument python strands_travel_agent_with_session.py --session-id "user-session-123"
```

### Want to use a different observability platform?

If you'd rather route telemetry to Datadog, Grafana, etc. instead of ADOT/CloudWatch, the runtime lets you opt out of the default ADOT wiring:

```bash
export DISABLE_ADOT_OBSERVABILITY=true
```

This unsets the runtime's default ADOT environment variables so none of the default ADOT configuration is applied, leaving you free to point OTEL wherever you like.

---

## Part 8 — The metrics catalog (the part you'll bookmark)

AgentCore publishes a lot of default metrics, organized by resource type. Below is a practical catalog. Two service namespaces matter most: **`AWS/Bedrock-AgentCore`** (gateway, policy, identity authorization/resource-access) and **`AWS/Usage`** (identity service quotas). Your own instrumented metrics go to **`bedrock-agentcore`** via EMF.

> Gateway and built-in tool metrics are **batched at 1-minute intervals**. Tool *resource-usage* data may lag up to **60 minutes** and is explicitly **not authoritative for billing**.

### 7.1 Gateway metrics — `AWS/Bedrock-AgentCore`

The Gateway is your MCP front door to tools, so these are the metrics that tell you whether tool-calling is healthy.

**Invocation metrics**

| Metric | Description | Stats | Units |
|---|---|---|---|
| `Invocations` | Total requests to each Data Plane API (one per call, regardless of status) | Sum | Count |
| `Throttles` | Requests throttled (HTTP 429) | Sum | Count |
| `SystemErrors` | Requests that failed with 5xx | Sum | Count |
| `UserErrors` | Requests that failed with 4xx (except 429) | Sum | Count |
| `Latency` | Request receipt → **first** response token | Avg/Min/Max/p50/p90/p99 | Milliseconds |
| `Duration` | Request → **final** response token (end-to-end) | Avg/Min/Max/p50/p90/p99 | Milliseconds |
| `TargetExecutionTime` | Time spent executing the target (Lambda/OpenAPI/etc.) | Avg/Min/Max/p50/p90/p99 | Milliseconds |

**Usage metrics**

| Metric | Description | Stats | Units |
|---|---|---|---|
| `TargetType` | Total requests served by each target type (MCP, Lambda, OpenAPI) | Sum | Count |

**Dimensions:** `Operation` (e.g., `InvokeGateway`), `Protocol` (e.g., `MCP`), `Method` (e.g., `tools/list`), `Resource` (gateway ARN), `Name` (tool name).

The split between `Latency` (first token) and `Duration` (last token) is the useful nuance: a healthy first-token latency with a ballooning duration points at slow targets — and `TargetExecutionTime` tells you how much of that is the target's fault versus the gateway's overhead.

### 7.2 Built-in tools metrics (Code Interpreter & Browser)

Grouped by operation:

- **Invoke tool:** `Invocations`, `Throttles` (429 `ThrottlingException`), `SystemErrors`, `UserErrors`, `Latency`.
- **Create tool session:** the five above **plus** `Duration` (length of the tool session; `Operation` becomes `CodeInterpreterSession` / `BrowserSession`).
- **Browser user takeover:** `TakerOverCount`, `TakerOverReleaseCount`, `TakerOverDuration` *(metric names spelled as published in the AWS docs).*

**Resource-usage metrics** (vended, at account + tool level, 1-minute resolution):

| Metric | Dimensions | Description |
|---|---|---|
| `CPUUsed-vCPUHours` | `Service`; `Service, Resource` | Virtual CPU consumed, in vCPU-Hours |
| `MemoryUsed-GBHours` | `Service`; `Service, Resource` | Memory consumed, in GB-Hours |

Where `Service` is `AgentCore.CodeInterpreter` or `AgentCore.Browser`, and `Resource` is the tool ID. Vended **session-level usage logs** (1-second granularity, log type `USAGE_LOGS`) carry finer fields like `codeInterpreter.vcpu.hours.used`, `browser.memory.gb_hours.used`, plus `session.id` and `elapsed_time_seconds`.

### 7.3 Identity metrics — `AWS/Usage` + `AWS/Bedrock-AgentCore`

**Usage (`AWS/Usage`):** `CallCount`, `ThrottleCount` (dimensions `Service, Type, Class, Resource`).

**Authorization (`AWS/Bedrock-AgentCore`):**

| Metric | Description |
|---|---|
| `WorkloadAccessTokenFetchSuccess` | Successful workload-access-token fetches |
| `WorkloadAccessTokenFetchFailures` | Failures, broken out by `ExceptionType` |
| `WorkloadAccessTokenFetchThrottles` | Throttled fetches |

**Resource access (OAuth2 / API keys):**

| Metric | Description |
|---|---|
| `ResourceAccessTokenFetchSuccess` / `…Failures` / `…Throttles` | OAuth2 token fetches from credential providers |
| `ApiKeyFetchSuccess` / `…Failures` / `…Throttles` | API key fetches |

**Common dimensions:** `WorkloadIdentity`, `WorkloadIdentityDirectory` (usually `default`), `TokenVault` (usually `default`), `ProviderName` (e.g., `MyGoogleProvider`), `FlowType` (`USER_FEDERATION`, `M2M`), `ExceptionType` (e.g., `ValidationException`, `ThrottlingException`).

These are the metrics that tell you whether your agent is failing because it *can't get credentials* — a failure class invisible to ordinary HTTP error counts.

### 7.4 Policy metrics — `AWS/Bedrock-AgentCore`

Policy controls which actions/tools an agent is *allowed* to take. Its metrics are published by default and shown under the **Gateway** tab.

| Metric | Description | Unit |
|---|---|---|
| `Invocations` | Requests to the service | Count |
| `SystemErrors` / `UserErrors` | 5xx / 4xx errors | Count |
| `Latency` | Request → response | Milliseconds |
| `AllowDecisions` | Decisions resulting in ALLOW | Count |
| `DenyDecisions` | Decisions resulting in DENY | Count |
| `TotalMismatchedPolicies` | Policies that failed for a request (missing attribute / type mismatch) | Count |
| `PolicyMismatch` | Failures for one specific policy | Count |
| `MismatchErrors` | Requests with ≥1 mismatched policy | Count |
| `DeterminingPolicies` | Determining policies per request | Count |
| `NoDeterminingPolicies` | Requests denied because no policy applied | Count |

**Dimensions:** `OperationName` (`AuthorizeAction`, `PartiallyAuthorizeActions`), `PolicyEngine`, `Policy`, `TargetResource`, `ToolName`, `Mode` (`LOG_ONLY`, `ENFORCE`).

The `Mode` dimension is the one to watch during rollout: run in `LOG_ONLY` first, watch `DenyDecisions` to see what *would* have been blocked, then flip to `ENFORCE` once the deny rate looks right.

### 7.5 Payments metrics

For agents that transact, AgentCore auto-generates spans and metrics for every data-plane API call.

| Metric | Unit | Description |
|---|---|---|
| `OperationSuccess` / `OperationFailure` | Count | Succeeded / failed API calls |
| `OperationLatency` | Milliseconds | Per-call end-to-end latency |
| `SpendAmount` | None | Amount processed (`ProcessPayment` only) |
| `Throttles` | Count | Throttled requests |
| `UserErrors` | Count | Client-side validation errors |
| `ActiveSessions` | Count | Active payment sessions |
| `PaymentRequestCount` | Count | Total payment requests |
| `PaymentSuccessCount` / `PaymentFailureCount` | Count | Successful / failed transactions |
| `PaymentLatency` | Milliseconds | Payment processing latency |

**Dimensions:** `Operation`, `PaymentManagerId`, `PaymentConnectorId`, `AgentName`, and `Currency` (for `SpendAmount`). Spans are named `Bedrock.AgentCore.Payments.<Operation>` — e.g., `Bedrock.AgentCore.Payments.ProcessPayment`.

---

## Part 9 — Setting an alarm (a worked example)

Metrics are only useful if they page you before your users notice. Here's a CloudWatch alarm on the gateway error metric:

```bash
aws cloudwatch put-metric-alarm \
  --alarm-name "HighErrorRate" \
  --alarm-description "Alarm when gateway 5xx errors exceed threshold" \
  --metric-name "SystemErrors" \
  --namespace "AWS/Bedrock-AgentCore" \
  --statistic "Sum" \
  --dimensions "Name=Resource,Value=my-gateway-arn" \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator "GreaterThanThreshold" \
  --alarm-actions "arn:aws:sns:us-west-2:123456789012:my-topic"
```

Good first alarms to stand up: gateway `SystemErrors` and `Throttles`, identity `…FetchFailures`, policy `DenyDecisions` spikes, and a `Latency`/`Duration` p99 threshold on your agent.

---

## Part 10 — Reading the data in the console

Once data is flowing, here's where to look.

### GenAI Observability dashboard

Open the **CloudWatch GenAI Observability** page (`console.aws.amazon.com/cloudwatch/home#gen-ai-observability`) and pick the **Bedrock AgentCore** tab. You get three views:

- **Agents View** — lists agents both **on and off** the runtime; drill into per-agent metrics, sessions, and traces. Shows session counts, latency, throttles, and error rates with a customizable time filter.
- **Sessions View** — every session, ready to drill into its traces.
- **Traces View** — the trace **trajectory** and **timeline** — the visual reasoning path of a single request.

```
Agents View                Sessions View              Traces View
┌──────────────────┐       ┌──────────────────┐       ┌────────────────────────────┐
│ agent-A   ▲ 1.2k │       │ sess-001  3 trc  │       │  ▸ invoke_agent     820ms   │
│ agent-B   ▼ 340  │  ──▶  │ sess-002  1 trc  │  ──▶  │    ▸ LLM call       610ms   │
│ agent-C   ● 12   │       │ sess-003  7 trc  │       │    ▸ tool: weather   90ms ✗ │
│ (latency,err,thr)│       │ (per-session)    │       │    ▸ tool: calc      40ms   │
└──────────────────┘       └──────────────────┘       └────────────────────────────┘
   "which agent?"             "which session?"            "which span broke?"
```

### Logs

CloudWatch → **Logs → Log groups**:

| What | Log group | Use for |
|---|---|---|
| Standard stdout/stderr | `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>/[runtime-logs] <UUID>` | Runtime errors, app logs, debug prints |
| OTEL structured logs | `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint_name>/runtime-logs` (a.k.a. `otel-rt-logs`) | Execution detail, correlation IDs, perf |
| Vended app logs (memory/gateway/tools) | `/aws/vendedlogs/bedrock-agentcore/{resource-type}/APPLICATION_LOGS/{resource-id}` | Per-resource application logs |

The OTEL structured logs include correlation IDs — `trace_id`, `span_id`, and `aws.request_id` — which are the **join keys** that let you stitch a log line back to the exact span and trace it came from.

### Traces & spans

CloudWatch → **Transaction Search**. Spans live in `/aws/spans/default`. Filter by service name, pick a trace, and you get the execution graph — agent invocation sequence, framework integration (e.g., LangChain), LLM calls and responses, tool invocations and results, and error paths/exceptions.

### Metrics

CloudWatch → **Metrics** → choose the namespace: `AWS/Bedrock-AgentCore` (service metrics), `AWS/Usage` (identity quotas), or `bedrock-agentcore` (your instrumented EMF metrics).

---

## Part 11 — A worked debugging walkthrough: from "it's slow sometimes" to root cause

All the theory pays off in a moment like this. Here's a realistic incident and the exact path through AgentCore Observability to solve it — the kind of thing the dashboards exist for.

**The report:** *"The travel agent is fine most of the time, but every so often a user waits 15+ seconds and sometimes gets a vague answer."* No stack trace, no 5xx — classic agent fuzziness.

**Step 1 — Confirm the pattern in the Agents View.** Open GenAI Observability → Bedrock AgentCore → your agent. The p50 `Duration` looks healthy (~2s) but the **p99 is 16s**, and the spread started yesterday. So it's not broken — it's *bimodal*. A subset of requests is doing something expensive. Metrics told you *that* it happens and *how often*; they can't tell you *why*. Drop to traces.

**Step 2 — Find a bad trace in the Sessions/Traces View.** Sort traces by duration, open a 16-second one. The trace timeline makes the shape obvious at a glance:

```
▸ invoke_agent                                                  15,820 ms
  ▸ LLM call (planning)                                            640 ms
  ▸ tool: search_flights  ──────────────────────────────────   12,900 ms   ⚠
  ▸ LLM call (summarize)                                           910 ms
  ▸ tool: weather                                                   90 ms
```

There it is: one span, `search_flights`, eating 12.9 of the 15.8 seconds. The reasoning wasn't wrong and the LLM wasn't slow — a **tool call** was the bottleneck. This is the moment the session→trace→span hierarchy earns its keep: you went from "an agent feels slow" to "one specific operation in one specific request" in two clicks.

**Step 3 — Decide which subsystem owns it.** `search_flights` runs through the Gateway, so cross-reference the **Gateway metrics** (Part 8). Filter `TargetExecutionTime` and `Duration` by the gateway's `Resource` ARN and the tool `Name`:

- If `TargetExecutionTime` ≈ `Duration` → the **target itself** (the flights API / Lambda) is slow. Not your agent — go fix or add a timeout to the target.
- If `Duration` ≫ `TargetExecutionTime` → the time is **gateway/network overhead**, not the target.
- A spike in `Throttles` on the same tool → you're being **rate-limited**, and the agent is silently retrying.

**Step 4 — Read the span attributes for the smoking gun.** Open the `search_flights` span in Transaction Search. Span attributes carry `error_type`, `http.response.status_code`, `latency_ms`, and `tool.name`. Say you see `http.response.status_code = 429` on two child attempts before a slow success — confirmed: **throttling-induced retries**. The "vague answer" users complained about lines up too: when the retries finally time out, the agent summarizes with partial data.

**Step 5 — Correlate logs by `trace_id`.** Want the literal request/response payloads? Jump to the vended gateway logs (`/aws/vendedlogs/bedrock-agentcore/gateway/APPLICATION_LOGS/...`) and filter on the **`trace_id`** from the span. Because spans and logs share `trace_id` and `span_id`, you land on the exact log lines for this one bad request — `request_payload`, `response_payload`, and all.

**The fix and the guardrail.** Root cause: the flights target throttles under load and the agent retries serially. You add a backoff/timeout on the target and a `Throttles` alarm (Part 9) so next time you're paged in minutes, not in a vague user complaint.

The whole investigation — metrics to spot it, traces to localize it, span attributes to explain it, logs to prove it — is the loop you'll run for almost every agent incident. Internalize that loop and the rest of this guide is just reference material.

---

## Part 12 — Data protection: traces capture *everything*, so be careful

This is the part teams skip and regret. Traces, spans, and logs capture the **raw inputs and outputs** of your agent — which means user prompts, PII, and sensitive business data can flow straight into your observability store unless you stop them.

AWS's guidance, distilled:

1. **Start simple, then expand.** Defaults already capture model calls, token usage, and tool execution — you don't need to over-instrument on day one.
2. **Configure for your development stage.** More verbosity in dev, tighter in prod.
3. **Use consistent naming conventions** for services, spans, and attributes — it's what makes filtering and correlation possible at scale.
4. **Filter sensitive data from observability attributes and payloads.** Scrub PII and confidential business data *before* it lands in a span. This is the one that protects you.
5. **Set up CloudWatch alarms** to catch issues before they reach users.

### Control *where* data goes

For memory, gateway, runtime, built-in tools, and WorkloadIdentity resources, you choose the log/trace destination — **CloudWatch Logs, Amazon S3, or Amazon Data Firehose** — from the AgentCore console: select the resource → **Log delivery** pane → **Add** → pick a destination → set **Log type = APPLICATION_LOGS** → optionally tune **Field selection / Output format / Field delimiter** under *Additional settings*. That field-selection control is itself a data-protection lever: don't deliver fields you don't need.

### Programmatic delivery configuration

For IaC, the same wiring is available via the `logs` SDK — create a vended log group, then `put_delivery_source` / `put_delivery_destination` / `create_delivery` to connect a source to a destination (`deliveryDestinationType='CWL'` for logs, `'XRAY'` for traces). Trace delivery sources/destinations apply to **memory and gateway** resources.

### Least-privilege IAM

A minimal policy for an agent emitting telemetry:

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Effect": "Allow",
    "Action": [
      "bedrock-agentcore:*",
      "logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents",
      "logs:DescribeLogGroups", "logs:DescribeLogStreams",
      "cloudwatch:PutMetricData",
      "xray:PutTraceSegments", "xray:PutTelemetryRecords"
    ],
    "Resource": "*"
  }]
}
```

Tighten `Resource` to specific ARNs for production.

---

## Part 13 — A practical rollout checklist

If you're starting from zero, do it in this order:

1. ☐ **Enable CloudWatch Transaction Search** once for the account (start at 1% sampling). Wait ~10 min.
2. ☐ Decide your path: **Runtime-hosted** (auto-instrumented) or **off-runtime** (ADOT + `opentelemetry-instrument`).
3. ☐ Deploy/run an agent and confirm spans appear in **Transaction Search** (`/aws/spans/default`).
4. ☐ Pass a stable **session ID** (header on-runtime, baggage off-runtime) so traces group into sessions.
5. ☐ Open **GenAI Observability → Bedrock AgentCore** and confirm Agents/Sessions/Traces populate.
6. ☐ Enable **per-resource tracing** for memory/gateway/tools/identity where you need spans.
7. ☐ Configure **log destinations** and **field selection** (data protection) for each resource.
8. ☐ **Scrub PII** from attributes/payloads before they're emitted.
9. ☐ Stand up **CloudWatch alarms** on errors, throttles, latency, and policy denies.
10. ☐ Tune sampling up from 1% once you know what you actually want to keep.

---

## Closing thought

The reason agent observability feels different is that you're no longer just monitoring a *system* — you're monitoring a *decision-maker*. The questions change from "is it up and fast?" to "did it choose the right tool, reason soundly, stay within policy, and spend the right amount of money and tokens to get there?"

AgentCore Observability answers those questions by giving every interaction a shape you can inspect — **sessions** you can isolate, **traces** you can replay, and **spans** you can blame — all in OpenTelemetry's open format and all stored in CloudWatch where the rest of your operational world already lives. Turn on Transaction Search, pass a session ID, scrub your PII, and you've closed most of the accountability gap before your first production incident.

That's the whole game: make the machine's thinking *visible*, so that when it surprises you, you can see exactly why.

---

### Reference sources

- AgentCore Observability overview — `docs.aws.amazon.com/bedrock-agentcore/latest/devguide/observability.html`
- Get started — `.../observability-get-started.html`
- Configure / add observability — `.../observability-configure.html`
- Telemetry concepts (sessions/traces/spans) — `.../observability-telemetry.html`
- Service-provided data — `.../observability-service-provided.html`
- Metrics: gateway / tool / identity / policy / payments — `.../observability-{gateway|tool|identity|policy|payments}-metrics.html`
- Viewing observability data — `.../observability-view.html`
- AWS *AgentCore Deep Dive* workshop, Observability module — `catalog.workshops.aws/agentcore-deep-dive/en-US/70-agentcore-observability`
- AWS ML Blog — *Build trustworthy AI agents with Amazon Bedrock AgentCore Observability*

> *Note on sources: the workshop catalog pages require an authenticated session and could not be retrieved anonymously; that material was reconstructed from the official AWS dev-guide pages, the starter-toolkit docs, and the AWS ML blog that the workshop is built on. A handful of metric names (e.g., `TakerOver…`) are reproduced exactly as published — including upstream typos — so they match what you'll see in the console. Verify the `aws:SourceArn` resource-policy detail against the current docs before using it in production.*
