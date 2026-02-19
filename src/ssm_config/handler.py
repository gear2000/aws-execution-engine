"""Lambda entrypoint for ssm_config — SSM config provider.

Separate construction point for SSM orders. Packages code, fetches credentials
(no SOPS), uploads to S3, inserts into shared DynamoDB orders table, and
triggers the orchestrator.

Supports three invocation sources:
  - Direct Lambda invoke: {"job_parameters_b64": "..."}
  - SNS trigger: {"Records": [{"Sns": {"Message": "{...}"}}]}
  - API Gateway: {"httpMethod": "POST", "body": "{...}"}
"""

import json
import logging
import os
import uuid
from typing import Any, Dict

from src.common.trace import generate_trace_id, create_leg
from src.common.flow import generate_flow_id
from src.common import s3 as s3_ops
from src.ssm_config.models import SsmJob
from src.ssm_config.validate import validate_ssm_orders
from src.ssm_config.repackage import repackage_ssm_orders
from src.ssm_config.insert import insert_ssm_orders
from src.init_job.upload import upload_orders

logger = logging.getLogger(__name__)


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the job payload from any supported invocation source."""
    # SNS
    if "Records" in event:
        records = event["Records"]
        if records and "Sns" in records[0]:
            message = records[0]["Sns"].get("Message", "{}")
            if isinstance(message, str):
                return json.loads(message)
            return message

    # API Gateway format 2.0: requestContext.http
    if "requestContext" in event and "http" in event.get("requestContext", {}):
        method = event["requestContext"]["http"].get("method", "")
        if method != "POST":
            return {"_apigw_error": f"Method {method} not allowed"}
        body = event.get("body", "")
        if isinstance(body, str):
            return json.loads(body) if body else {}
        return body if isinstance(body, dict) else {}

    # API Gateway format 1.0: httpMethod
    if "httpMethod" in event:
        if event["httpMethod"] != "POST":
            return {"_apigw_error": f"Method {event['httpMethod']} not allowed"}
        body = event.get("body", "")
        if isinstance(body, str):
            return json.loads(body) if body else {}
        return body if isinstance(body, dict) else {}

    return event


def process_ssm_job(
    job_parameters_b64: str,
    trace_id: str = "",
    run_id: str = "",
    done_endpt: str = "",
) -> dict:
    """Main processing function for SSM config provider."""
    internal_bucket = os.environ.get("AWS_EXE_SYS_INTERNAL_BUCKET", "")
    done_bucket = os.environ.get("AWS_EXE_SYS_DONE_BUCKET", "")

    job = SsmJob.from_b64(job_parameters_b64)

    if not trace_id:
        trace_id = generate_trace_id()
    if not run_id:
        run_id = str(uuid.uuid4())

    flow_id = generate_flow_id(job.username, trace_id, job.flow_label)

    if not done_endpt:
        done_endpt = f"s3://{done_bucket}/{run_id}/done"

    # Step 1: Validate
    errors = validate_ssm_orders(job)
    if errors:
        return {
            "status": "error",
            "errors": errors,
            "run_id": run_id,
            "trace_id": trace_id,
        }

    # Step 2: Repackage (no SOPS)
    leg = create_leg(trace_id)
    repackaged = repackage_ssm_orders(
        job=job,
        run_id=run_id,
        trace_id=trace_id,
        flow_id=flow_id,
        internal_bucket=internal_bucket,
    )

    # Step 3: Upload
    upload_orders(repackaged, run_id, internal_bucket)

    # Step 4: Insert into DynamoDB
    insert_ssm_orders(
        job=job,
        run_id=run_id,
        flow_id=flow_id,
        trace_id=trace_id,
        repackaged_orders=repackaged,
        internal_bucket=internal_bucket,
    )

    # Step 5: Write init trigger to kick off orchestrator
    s3_ops.write_init_trigger(
        bucket=internal_bucket,
        run_id=run_id,
    )

    return {
        "status": "ok",
        "run_id": run_id,
        "trace_id": trace_id,
        "flow_id": flow_id,
        "done_endpt": done_endpt,
    }


def _apigw_response(status_code: int, body: dict) -> dict:
    """Wrap result in API Gateway proxy response format."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def handler(event: Dict[str, Any], context: Any = None) -> dict:
    """Lambda entrypoint. Supports direct invoke, SNS, and API Gateway."""
    is_apigw = "httpMethod" in event or ("requestContext" in event and "http" in event.get("requestContext", {}))

    try:
        payload = _normalize_event(event)

        if "_apigw_error" in payload:
            return _apigw_response(405, {"status": "error", "error": payload["_apigw_error"]})

        job_parameters_b64 = payload.get("job_parameters_b64", "")

        if not job_parameters_b64:
            result = {"status": "error", "error": "Missing job_parameters_b64"}
            return _apigw_response(400, result) if is_apigw else result

        result = process_ssm_job(
            job_parameters_b64=job_parameters_b64,
            trace_id=payload.get("trace_id", ""),
            run_id=payload.get("run_id", ""),
            done_endpt=payload.get("done_endpt", ""),
        )

        if is_apigw:
            code = 200 if result.get("status") == "ok" else 400
            return _apigw_response(code, result)
        return result

    except Exception as e:
        logger.exception("ssm_config failed")
        result = {"status": "error", "error": str(e)}
        return _apigw_response(500, result) if is_apigw else result
