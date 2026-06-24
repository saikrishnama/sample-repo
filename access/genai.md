# The Rest of the GenAI Conventions: Tools, Exceptions, MCP, Content Schemas, and Providers

The main guide
([end-to-end-ai-observability-with-opentelemetry.md](end-to-end-ai-observability-with-opentelemetry.md))
covers the parts of the OpenTelemetry GenAI spec you touch every day — agent spans,
the `chat` span, token/cost metrics, and the events that carry content. But the spec
(`open-telemetry/semantic-conventions-genai`, under `docs/gen-ai/`) has more pages
than that, and the ones it adds are exactly the parts that bite you once you go past
a single model call: **tool calls, embeddings, exceptions, MCP, the content schemas,
and provider-specific attributes.**

This is the companion reference. Think of it as "everything else in the spec folder,
in the order you'll actually need it." Each section maps to one spec page.

---

## 1. The `execute_tool` span (gen-ai-spans.md)

The main guide name-drops `execute_tool` but never fleshes it out. Here it is in
full — and it matters, because a tool call is where an agent actually *touches the
world*, so it's the span you'll stare at most during incidents.

| Attribute | Level | Notes |
|---|---|---|
| `gen_ai.operation.name` | Required | Must be `execute_tool` |
| `gen_ai.tool.name` | Required | The tool's name, e.g. `get_weather` |
| `gen_ai.tool.call.id` | Recommended | Correlates the call with the model's request to call it |
| `gen_ai.tool.type` | Recommended | `function` \| `extension` \| `datastore` |
| `gen_ai.tool.description` | Recommended | What the tool does |
| `gen_ai.tool.call.arguments` | Opt-In | The arguments passed — **may contain sensitive data** |
| `gen_ai.tool.call.result` | Opt-In | What the tool returned — **may contain sensitive data** |

- **Span name:** `execute_tool {gen_ai.tool.name}` — e.g. `execute_tool get_weather`
- **Span kind:** `INTERNAL`

```python
from opentelemetry.trace import SpanKind

def execute_tool(tool_name: str, call_id: str, args: dict) -> dict:
    with tracer.start_as_current_span(
        f"execute_tool {tool_name}",
        kind=SpanKind.INTERNAL,
        attributes={
            "gen_ai.operation.name": "execute_tool",
            "gen_ai.tool.name": tool_name,
            "gen_ai.tool.call.id": call_id,        # ties back to the model's tool_call
            "gen_ai.tool.type": "function",
            "gen_ai.tool.description": "Look up current weather for a city",
            # arguments/result are OPT-IN — only with content capture enabled:
            # "gen_ai.tool.call.arguments": json.dumps(args),
        },
    ) as span:
        try:
            result = TOOLS[tool_name](**args)
        except Exception as exc:
            span.set_attribute("error.type", exc.__class__.__name__)
            span.record_exception(exc)
            raise
        # "gen_ai.tool.call.result": json.dumps(result)   # opt-in
        return result
```

The `gen_ai.tool.call.id` is the quiet hero here: it's what lets you join the
model's *decision* to call a tool (in the `chat` span) with the tool's *execution*
(in this span). Without it, a run with three parallel tool calls becomes a guessing
game about which result went with which request.

---

## 2. The `embeddings` span (gen-ai-spans.md)

Embeddings calls power your RAG retrieval, and they cost tokens too, so trace them.
They use the generic model-span shape with a couple of embeddings-specific fields.

| Attribute | Level | Notes |
|---|---|---|
| `gen_ai.operation.name` | Required | Must be `embeddings` |
| `gen_ai.provider.name` | Required | `openai`, `aws.bedrock`, … |
| `gen_ai.request.model` | Cond. Req. | The embedding model |
| `gen_ai.embeddings.dimension.count` | Recommended | Vector dimensionality |
| `gen_ai.request.encoding_formats` | Recommended | Array, e.g. `["float"]` |
| `gen_ai.usage.input_tokens` | Recommended | Yes, embeddings burn tokens too |

- **Span name:** `embeddings {gen_ai.request.model}` — e.g. `embeddings text-embedding-3-large`
- **Span kind:** `CLIENT`

---

## 3. Recording failures: exceptions vs. `error.type` (gen-ai-exceptions.md)

There are *two* distinct mechanisms for failure, and people conflate them:

- **`error.type`** — a span *attribute*. Always set it on the failed operation span.
  It's low-cardinality (a class name or error code) and it's what your metrics and
  alerts key on. This is the one that matters for dashboards.
- **`gen_ai.client.operation.exception`** — a dedicated *event* attached to the span
  for the richer story: the message and stack trace. Use it when you want the
  forensic detail, not just the classification.

The exception event carries the standard exception attributes:

| Attribute | Level | Notes |
|---|---|---|
| `exception.type` | Cond. Req. | Fully-qualified class name (this *or* `exception.message`) |
| `exception.message` | Cond. Req. | The message — **may contain sensitive info** |
| `exception.stacktrace` | Recommended | Runtime stack trace |

Guidance worth knowing: the event should be logged at **WARN** severity, and for
wrapper exceptions that don't help classify the failure, record the *inner*
exception type instead — a generic `RuntimeError` wrapping a `RateLimitError` should
report the rate-limit, since that's the actionable signal.

```python
# The pattern: set the low-cardinality attribute for metrics/alerts...
span.set_attribute("error.type", "RateLimitError")
# ...and record the rich event for forensics. OTel SDKs do this via:
span.record_exception(exc)   # emits the exception event with type/message/stacktrace
```

The rule of thumb: **`error.type` is for the alarm; the exception event is for the
post-mortem.** Set both.

---

## 4. The content schemas (the opt-in JSON shapes)

The spec ships JSON Schemas that pin the *shape* of captured content, so that when
you do turn on content capture, every tool agrees on the structure. These back the
opt-in attributes from the main guide and add two that are pure gold for agents:

| Schema file | Backs | What it captures |
|---|---|---|
| `gen-ai-input-messages.json` | `gen_ai.input.messages` | The chat history sent to the model |
| `gen-ai-output-messages.json` | `gen_ai.output.messages` | The messages the model produced |
| `gen-ai-system-instructions.json` | `gen_ai.system_instructions` | The system prompt, captured separately |
| `gen-ai-tool-definitions.json` | `gen_ai.tool.definitions` | The tool specs the model could choose from |
| `gen-ai-retrieval-documents.json` | retrieval output | The documents/chunks a RAG step returned |
| `gen-ai-memory-records.json` | agent memory | The records read from / written to agent memory |

The last two are the ones most guides miss, and they're the difference between
"the agent gave a weird answer" and a precise diagnosis:

- **Retrieval documents** — capturing what RAG actually returned (with relevance
  scores) tells you whether a bad answer came from bad retrieval or bad reasoning.
- **Memory records** — capturing what the agent loaded from memory catches the
  *stale-memory* bug, where the agent reasons from a fact that was true last week.

(There's a worked, Bedrock-flavored version of exactly this retrieval+memory tracing
in [../bedrock/kb-memory-tracing-example.md](../bedrock/kb-memory-tracing-example.md).)

All six are **opt-in** for the same reason as the message attributes: they carry
content that's often regulated. Turn them on per service and scrub centrally in the
Collector.

---

## 5. Tracing MCP — the agent-to-tool boundary (mcp.md)

If your agents call tools over the **Model Context Protocol**, the spec has a whole
page for it — and this is increasingly where the interesting failures live, because
the tool runs in a *different process or host* than the agent. MCP tracing is what
lets a single trace span the agent-to-tool boundary instead of stopping at it.

MCP defines two span types around the JSON-RPC calls between client and server:

- **Client span** (`CLIENT`) — the agent side initiating a request/notification
- **Server span** (`SERVER`) — the MCP server processing it

- **Span name:** `{mcp.method.name} {target}` — e.g. `tools/call get_weather`

| Attribute | Level | Notes |
|---|---|---|
| `mcp.method.name` | Required | The RPC method: `tools/call`, `initialize`, `prompts/get`, `resources/read`, … |
| `gen_ai.tool.name` | Cond. Req. | When the method targets a specific tool |
| `gen_ai.prompt.name` | Cond. Req. | For prompt operations |
| `jsonrpc.request.id` | Cond. Req. | For requests (absent for notifications) |
| `mcp.session.id` | Recommended | The MCP session |
| `mcp.protocol.version` | Recommended | Protocol version in use |
| `network.transport` | Recommended | `pipe` (stdio), `tcp`/`quic` (HTTP) |
| `server.address` / `server.port` | Recommended | Where the server lives |
| `error.type` | Cond. Req. | On failure |

Operations covered span the whole protocol surface: session management
(`initialize`, notifications), tools (`tools/call`, `tools/list`), prompts
(`prompts/get`, `prompts/list`), resources (`resources/read`, `resources/list`,
`resources/subscribe`), and completion/sampling.

The payoff: **trace-context propagation across MCP.** When the agent's client span
and the MCP server's span share one trace, you can follow a request from the agent's
reasoning, across the protocol boundary, into the tool's execution, and back — one
continuous story instead of two disconnected halves.

---

## 6. Provider-specific conventions (anthropic.md, openai.md, aws-bedrock.md, azure-ai-inference.md)

The base conventions are provider-neutral. Each provider page then adds the
attributes unique to that platform and pins the `gen_ai.provider.name` value. You
layer these *on top of* the base `chat`/`embeddings` spans.

### `gen_ai.provider.name` values

| Provider | Value |
|---|---|
| OpenAI | `openai` |
| Anthropic | `anthropic` |
| AWS Bedrock | `aws.bedrock` |
| Azure AI Inference | `azure.ai.inference` |
| Google Vertex AI | `gcp.vertex_ai` |

### AWS Bedrock additions

These are the ones worth wiring up if you're on Bedrock (and they tie directly into
the Bedrock posts in the sibling `bedrock/` folder):

| Attribute | Level | Notes |
|---|---|---|
| `aws.bedrock.guardrail.id` | Required | The Guardrail applied — capturing it lets you audit guardrail coverage per call |
| `aws.bedrock.knowledge_base.id` | Recommended | The Knowledge Base queried for RAG |
| `gen_ai.request.top_k` | Recommended | Bedrock exposes top-k sampling |
| `gen_ai.response.id` | Recommended | Provider-side response ID for support tickets |

So a Bedrock `chat` span carries the base attributes (`gen_ai.operation.name`,
`gen_ai.usage.*`, `gen_ai.response.finish_reasons`) *plus* `gen_ai.provider.name =
aws.bedrock` *plus* these Bedrock-specific keys. The other provider pages
(OpenAI, Anthropic, Azure) follow the same pattern — base attributes plus a handful
of platform-specific ones — so you instrument once against the base shape and just
add the provider's extras.

---

## Quick map: spec page → what it gives you

| Spec page | What you get | Covered in |
|---|---|---|
| `gen-ai-agent-spans.md` | `create_agent`, `invoke_agent`, `plan`, `invoke_workflow` | Main guide |
| `gen-ai-spans.md` | `chat`, `embeddings`, **`execute_tool`** | Main guide (chat) + §1–2 here |
| `gen-ai-metrics.md` | token usage, durations, TTFT/TPOT, buckets | Main guide |
| `gen-ai-events.md` | inference-details + evaluation-result events | Main guide |
| `gen-ai-exceptions.md` | the exception event + `error.type` | §3 here |
| content JSON schemas | input/output messages, system instructions, tool defs, **retrieval docs, memory records** | §4 here |
| `mcp.md` | MCP client/server spans across the tool boundary | §5 here |
| `anthropic.md` / `openai.md` / `aws-bedrock.md` / `azure-ai-inference.md` | per-provider attributes + `gen_ai.provider.name` | §6 here |

If you've instrumented everything in the main guide plus these six sections, you've
covered the entire `docs/gen-ai/` surface of the spec — agents, models, tools,
embeddings, metrics, events, exceptions, MCP, content, and providers.

---

### References

- *OpenTelemetry GenAI Semantic Conventions* — `github.com/open-telemetry/semantic-conventions-genai`, folder `docs/gen-ai/`
  - `gen-ai-spans.md` · `gen-ai-agent-spans.md` · `gen-ai-metrics.md` · `gen-ai-events.md` · `gen-ai-exceptions.md` · `mcp.md`
  - `anthropic.md` · `openai.md` · `aws-bedrock.md` · `azure-ai-inference.md`
  - JSON schemas: `gen-ai-input-messages.json` · `gen-ai-output-messages.json` · `gen-ai-system-instructions.json` · `gen-ai-tool-definitions.json` · `gen-ai-retrieval-documents.json` · `gen-ai-memory-records.json`
