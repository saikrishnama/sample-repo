import boto3
import json
import logging
from typing import Optional, Dict, Any


logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


class XRayTrace:
    """
    Represents an AWS X-Ray trace and provides helper methods
    for analyzing segments and subsegments.
    """

    def __init__(self, trace_id: str, region: str, xray_client: Optional[Any] = None):
        """
        Initialize an XRayTrace object.

        Args:
            trace_id (str): The ID of the X-Ray trace.
            region (str): AWS region for the X-Ray client.
            xray_client: Optional boto3 X-Ray client (useful for testing).
        """
        self.trace_id = trace_id
        self.region = region
        self.xray = xray_client or boto3.client("xray", region_name=region)
        self.trace = self._retrieve_trace()

    def _retrieve_trace(self) -> Dict[str, Any]:
        """
        Retrieve the X-Ray trace using the AWS X-Ray API.

        If the trace is not yet available (common when Glue starts
        before Step Functions flushes the trace), return a synthetic
        minimal trace structure so tracing can continue.

        Returns:
            dict: The retrieved or synthetic X-Ray trace.
        """
        try:
            response = self.xray.batch_get_traces(TraceIds=[self.trace_id])
            traces = response.get("Traces", [])

            if not traces:
                logger.warning(
                    "No X-Ray trace found for ID %s in region %s. "
                    "Using fallback synthetic trace.",
                    self.trace_id, self.region
                )
                return {
                    "Id": self.trace_id,
                    "Segments": []
                }

            trace = traces[0]

            for segment in trace.get("Segments", []):
                segment["Document"] = json.loads(segment["Document"])

            return trace

        except Exception as exc:
            logger.error(
                "Failed to retrieve X-Ray trace for %s in region %s: %s. "
                "Using fallback synthetic trace.",
                self.trace_id, self.region, exc
            )
            return {
                "Id": self.trace_id,
                "Segments": []
            }

    def find_segment_by_origin(self, origin: str) -> Optional[Dict[str, Any]]:
        """
        Find a segment in the trace based on its origin.
        """
        for segment in self.trace.get("Segments", []):
            if segment["Document"].get("origin") == origin:
                return segment
        return None

    def find_segment_by_request_id(self, request_id: str) -> Optional[str]:
        """
        Find a segment based on its AWS request ID.
        """
        for segment in self.trace.get("Segments", []):
            aws_data = segment["Document"].get("aws", {})
            if aws_data.get("request_id") == request_id:
                return segment["Id"]
        return None

    def find_step_subsegment(self, segment: Dict[str, Any], step_name: str) -> Optional[Dict[str, Any]]:
        """
        Find a step subsegment within a segment.
        """
        for subsegment in segment["Document"].get("subsegments", []):
            if subsegment.get("name") == step_name:
                return subsegment
        return None

    def find_aws_sdk_subsegment(self, subsegment: Dict[str, Any]) -> Optional[str]:
        """
        Find an AWS SDK subsegment within a given subsegment.
        """
        for sub in subsegment.get("subsegments", []):
            if "aws" in sub:
                return sub["aws"].get("request_id")
        return None

    def retrieve_id(self, step_name: str) -> Optional[str]:
        """
        Retrieve the AWS SDK segment ID associated with a given Step Functions step.
        """
        stepfunctions_segment = self.find_segment_by_origin("AWS::StepFunctions::StateMachine")

        if not stepfunctions_segment:
            logger.warning("StepFunctions segment not found for trace %s", self.trace_id)
            return None

        logger.info("Found StepFunctions segment ID: %s", stepfunctions_segment["Id"])

        step_subsegment = self.find_step_subsegment(stepfunctions_segment, step_name)
        if not step_subsegment:
            logger.warning("Step subsegment '%s' not found", step_name)
            return None

        logger.info("Found step subsegment: %s", step_name)

        aws_sdk_request_id = self.find_aws_sdk_subsegment(step_subsegment)
        if not aws_sdk_request_id:
            logger.warning("AWS SDK request ID not found for step '%s'", step_name)
            return None

        logger.info("Found AWS SDK request ID: %s", aws_sdk_request_id)

        aws_sdk_segment_id = self.find_segment_by_request_id(aws_sdk_request_id)
        if not aws_sdk_segment_id:
            logger.warning("AWS SDK segment not found for request ID: %s", aws_sdk_request_id)
            return None

        logger.info("Found AWS SDK segment ID: %s", aws_sdk_segment_id)
        return aws_sdk_segment_id
