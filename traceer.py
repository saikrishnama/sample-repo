from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter

# Configure OTLP exporter to send to Instance A
collector_endpoint = "INSTANCE_A_PRIVATE_IP:4317"

trace.set_tracer_provider(TracerProvider())
tracer = trace.get_tracer(__name__)

otlp_exporter = OTLPSpanExporter(
    endpoint=collector_endpoint,
    insecure=True  # no TLS inside VPC
)

span_processor = BatchSpanProcessor(otlp_exporter)
trace.get_tracer_provider().add_span_processor(span_processor)

# Generate a test trace
with tracer.start_as_current_span("test-span") as span:
    span.set_attribute("example.attribute", "hello-xray")
    print("Trace sent!")
