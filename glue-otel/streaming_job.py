import sys
import time
import logging
from typing import List

from awsglue.utils import getResolvedOptions

from pyspark.sql import SparkSession, DataFrame
from pyspark.sql.functions import (
    current_timestamp,
    input_file_name,
    lit,
    col,
    sha2,
    concat_ws,
)

from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------
args = getResolvedOptions(
    sys.argv,
    [
        "JOB_NAME",
        "INPUT_PATH",
        "OUTPUT_PATH",
        "QUARANTINE_PATH",
        "CHECKPOINT_PATH",
        "OTEL_ENDPOINT",
        "ENVIRONMENT",
        "MAX_FILES_PER_TRIGGER",
        "PROCESSING_INTERVAL",
    ],
)

JOB_NAME = args["JOB_NAME"]
INPUT_PATH = args["INPUT_PATH"].rstrip("/")
OUTPUT_PATH = args["OUTPUT_PATH"].rstrip("/")
QUARANTINE_PATH = args["QUARANTINE_PATH"].rstrip("/")
CHECKPOINT_PATH = args["CHECKPOINT_PATH"].rstrip("/")
OTEL_ENDPOINT = args["OTEL_ENDPOINT"]
ENVIRONMENT = args["ENVIRONMENT"]
MAX_FILES_PER_TRIGGER = args["MAX_FILES_PER_TRIGGER"]
PROCESSING_INTERVAL = args["PROCESSING_INTERVAL"]

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(JOB_NAME)

# ---------------------------------------------------------------------------
# OpenTelemetry
# ---------------------------------------------------------------------------
resource = Resource.create(
    {
        "service.name": "aws-glue-s3-streaming",
        "service.version": "1.0.0",
        "deployment.environment": ENVIRONMENT,
        "aws.glue.job.name": JOB_NAME,
    }
)

provider = TracerProvider(resource=resource)

# Keep exporter settings conservative so telemetry does not become the
# critical path of the data pipeline.
otlp_exporter = OTLPSpanExporter(
    endpoint=OTEL_ENDPOINT,
    insecure=True,
    timeout=10,
)

provider.add_span_processor(
    BatchSpanProcessor(
        otlp_exporter,
        max_queue_size=2048,
        max_export_batch_size=256,
        schedule_delay_millis=5000,
        export_timeout_millis=10000,
    )
)

trace.set_tracer_provider(provider)
tracer = trace.get_tracer("aws-glue-s3-streaming")


# ---------------------------------------------------------------------------
# Spark
# ---------------------------------------------------------------------------
spark = (
    SparkSession.builder
    .appName(JOB_NAME)
    .getOrCreate()
)

spark.sparkContext.setLogLevel("WARN")


# ---------------------------------------------------------------------------
# Schema
# Adjust this to your actual input contract.
# ---------------------------------------------------------------------------
INPUT_SCHEMA = """
    id STRING,
    customer_id STRING,
    event_type STRING,
    event_timestamp TIMESTAMP,
    amount DOUBLE
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def set_error(span, exc: Exception) -> None:
    """Mark an OpenTelemetry span as failed without hiding the exception."""
    try:
        span.record_exception(exc)
        span.set_status(
            Status(
                StatusCode.ERROR,
                str(exc)[:500],
            )
        )
    except Exception:
        # Never allow telemetry failures to mask the actual pipeline error.
        logger.exception("Unable to record telemetry error")


def extract_source_files(df: DataFrame) -> List[str]:
    """
    Return a bounded list of source files for observability.

    We deliberately cap the number of values so a very large micro-batch does
    not create huge X-Ray attributes/logs.
    """
    try:
        rows = (
            df.select(input_file_name().alias("source_file"))
            .where(col("source_file") != "")
            .distinct()
            .limit(50)
            .collect()
        )
        return [r["source_file"] for r in rows if r["source_file"]]
    except Exception:
        logger.exception("Could not collect source file names")
        return []


def write_quarantine(df: DataFrame, batch_id: str, reason: str) -> None:
    """
    Best-effort quarantine path. The original batch exception is still raised
    by the caller when appropriate.
    """
    try:
        quarantine_df = (
            df.withColumn("quarantine_timestamp", current_timestamp())
              .withColumn("quarantine_batch_id", lit(batch_id))
              .withColumn("quarantine_reason", lit(reason[:500]))
              .withColumn("source_file", input_file_name())
        )

        (
            quarantine_df.write
            .mode("append")
            .format("parquet")
            .save(f"{QUARANTINE_PATH}/batch_id={batch_id}")
        )

        logger.info(
            "Quarantined batch=%s reason=%s path=%s",
            batch_id,
            reason,
            QUARANTINE_PATH,
        )
    except Exception:
        logger.exception("Quarantine write failed for batch=%s", batch_id)


# ---------------------------------------------------------------------------
# Micro-batch processor
# ---------------------------------------------------------------------------
def process_batch(dataframe: DataFrame, batch_id: int) -> None:
    batch_id_str = str(batch_id)
    batch_start = time.time()

    with tracer.start_as_current_span("glue.microbatch") as batch_span:
        batch_span.set_attribute("aws.glue.job.name", JOB_NAME)
        batch_span.set_attribute("aws.glue.job.type", "streaming")
        batch_span.set_attribute("glue.batch.id", batch_id_str)
        batch_span.set_attribute("deployment.environment", ENVIRONMENT)
        batch_span.set_attribute("s3.input.path", INPUT_PATH)
        batch_span.set_attribute("s3.output.path", OUTPUT_PATH)

        source_files = extract_source_files(dataframe)

        if source_files:
            # X-Ray annotations should stay low-cardinality. Put only a bounded
            # count in the trace; detailed file names remain in the data/output.
            batch_span.set_attribute(
                "glue.source_file_count",
                len(source_files),
            )
            logger.info(
                "Batch=%s source_files=%s",
                batch_id_str,
                source_files,
            )

        try:
            # ---------------------------------------------------------------
            # Transform
            # ---------------------------------------------------------------
            with tracer.start_as_current_span(
                "glue.transformation"
            ) as transform_span:

                transformed_df = (
                    dataframe
                    .withColumn(
                        "processed_timestamp",
                        current_timestamp(),
                    )
                    .withColumn(
                        "glue_job_name",
                        lit(JOB_NAME),
                    )
                    .withColumn(
                        "glue_batch_id",
                        lit(batch_id_str),
                    )
                    .withColumn(
                        "source_file",
                        input_file_name(),
                    )
                )

                # Stable event fingerprint for downstream deduplication.
                # Change the columns to match your business event key.
                transformed_df = transformed_df.withColumn(
                    "event_fingerprint",
                    sha2(
                        concat_ws(
                            "||",
                            col("id").cast("string"),
                            col("customer_id").cast("string"),
                            col("event_type").cast("string"),
                            col("event_timestamp").cast("string"),
                        ),
                        256,
                    ),
                )

                transform_span.set_attribute(
                    "glue.transformation",
                    "metadata_and_event_fingerprint",
                )

            # ---------------------------------------------------------------
            # Write
            # ---------------------------------------------------------------
            with tracer.start_as_current_span(
                "glue.s3.write"
            ) as write_span:

                (
                    transformed_df.write
                    .mode("append")
                    .format("parquet")
                    .save(OUTPUT_PATH)
                )

                write_span.set_attribute("aws.service", "s3")
                write_span.set_attribute("s3.output.path", OUTPUT_PATH)

            duration = time.time() - batch_start
            batch_span.set_attribute(
                "glue.batch.duration.seconds",
                round(duration, 3),
            )

            logger.info(
                "Completed batch=%s duration=%.3fs",
                batch_id_str,
                duration,
            )

        except Exception as exc:
            set_error(batch_span, exc)

            # Best-effort quarantine. Depending on your data contract, you may
            # prefer to fail the batch instead of quarantining it.
            write_quarantine(
                dataframe,
                batch_id_str,
                f"batch_processing_error: {exc}",
            )

            logger.exception(
                "Batch=%s failed",
                batch_id_str,
            )

            # IMPORTANT: raise so Spark's checkpoint semantics do not mark the
            # failed batch as successfully completed.
            raise


# ---------------------------------------------------------------------------
# Streaming query
# ---------------------------------------------------------------------------
logger.info("Starting Glue S3 streaming job")
logger.info("Input=%s", INPUT_PATH)
logger.info("Output=%s", OUTPUT_PATH)
logger.info("Checkpoint=%s", CHECKPOINT_PATH)
logger.info("OTEL endpoint=%s", OTEL_ENDPOINT)

with tracer.start_as_current_span("glue.streaming.job") as job_span:
    job_span.set_attribute("aws.glue.job.name", JOB_NAME)
    job_span.set_attribute("aws.glue.job.type", "streaming")
    job_span.set_attribute("deployment.environment", ENVIRONMENT)

    try:
        streaming_df = (
            spark.readStream
            .format("json")
            .schema(INPUT_SCHEMA)
            .option("maxFilesPerTrigger", MAX_FILES_PER_TRIGGER)
            .option("latestFirst", "false")
            .load(INPUT_PATH)
        )

        query = (
            streaming_df.writeStream
            .foreachBatch(process_batch)
            .option("checkpointLocation", CHECKPOINT_PATH)
            .trigger(processingTime=PROCESSING_INTERVAL)
            .start()
        )

        logger.info("Streaming query started")

        query.awaitTermination()

    except Exception as exc:
        set_error(job_span, exc)
        logger.exception("Glue streaming job failed")
        raise

    finally:
        # Flush spans before Glue shuts down. Shutdown is deliberately in a
        # finally block so normal and error exits both attempt a flush.
        try:
            provider.shutdown()
        except Exception:
            logger.exception("OpenTelemetry shutdown failed")

        try:
            spark.stop()
        except Exception:
            logger.exception("Spark shutdown failed")
