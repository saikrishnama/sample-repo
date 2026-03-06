import logging
from typing import Optional, Tuple

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.propagators.aws.xray_id_generator import AwsXRayPropagator

from xray_helper import XRayTrace  # your helper class file


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def setup_tracing(
    job_name: str,
    trace_id: str,
    otlp_endpoint: str
) -> Tuple[trace.Tracer, Optional[object]]:
    """
    Configure OpenTelemetry tracing and extract AWS X-Ray context
    using the XRayTrace helper.

    Args:
        job_name (str): Name of the Glue job or service.
        trace_id (str): AWS X-Ray trace ID.
        otlp_endpoint (str): OTLP collector endpoint hostname or IP.

    Returns:
        tuple: (tracer, context) where context may be None if no parent segment is found.
    """
    resource = Resource.create({
        "service.name": job_name,
        "cloud.provider": "AWS::AWSGlue"
    })

    otlp_exporter = OTLPSpanExporter(
        endpoint=f"http://{otlp_endpoint}:4318/v1/traces"
    )

    processor = BatchSpanProcessor(otlp_exporter)
    trace.set_tracer_provider(
        TracerProvider(resource=resource, active_span_processor=processor)
    )

    tracer = trace.get_tracer(__name__)

    logger.info("Initializing X-Ray trace helper for trace ID: %s", trace_id)
    xray_trace = XRayTrace(trace_id)

    parent_id = xray_trace.retrieve_id(job_name)
    if parent_id:
        logger.info("Extracting parent context using AWS X-Ray Propagator")
        carrier = {
            "X-Amzn-Trace-Id": f"Root={trace_id};Parent={parent_id};Sampled=1"
        }
        propagator = AwsXRayPropagator()
        context = propagator.extract(carrier=carrier)
    else:
        logger.warning("No parent segment found for job '%s'. Starting a new trace.", job_name)
        context = None

    return tracer, context
