"""Lambda entrypoint for init_job — Part 1: receive job, repackage, and insert orders.

Supports three invocation sources:
  - Direct Lambda invoke: {"job_parameters_b64": "..."}
  - SNS trigger: {"Records": [{"Sns": {"Message": "{...}"}}]}
  - API Gateway: {"httpMethod": "POST", "body": "{...}"}
"""

import json
import logging
import os
import secrets
import uuid
from typing import Any, Dict

from src.common.models import Job
from src.common.trace import generate_trace_id, create_leg
from src.common.flow import generate_flow_id
from src.common import s3 as s3_ops
from src.init_job.validate import validate_orders
from src.init_job.repackage import repackage_orders
from src.init_job.upload import upload_orders
from src.init_job.insert import insert_orders
from src.init_job.pr_comment import init_pr_comment

logger = logging.getLogger(__name__)


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Extract the job payload from any supported invocation source.

    Returns a flat dict with at minimum 'job_parameters_b64'.
    """
    # SNS: unwrap first record's Message
    if "Records" in event:
        records = event["Records"]
        if records and "Sns" in records[0]:
            message = records[0]["Sns"].get("Message", "{}")
            if isinstance(message, str):
                return json.loads(message)
            return message

    # API Gateway: unwrap body (+ reject non-POST)
    if "httpMethod" in event:
        if event["httpMethod"] != "POST":
            return {"_apigw_error": f"Method {event['httpMethod']} not allowed"}
        body = event.get("body", "")
        if isinstance(body, str):
            return json.loads(body) if body else {}
        return body if isinstance(body, dict) else {}

    # Direct invoke: event is the payload
    return event


def process_job_and_insert_orders(
    job_parameters_b64: str,
    trace_id: str = "",
    run_id: str = "",
    done_endpt: str = "",
) -> dict:
    """Main processing function. Orchestrates the full init_job flow."""
    internal_bucket = os.environ.get("IAC_CI_INTERNAL_BUCKET", "")
    done_bucket = os.environ.get("IAC_CI_DONE_BUCKET", "")

    # Decode job parameters
    job = Job.from_b64(job_parameters_b64)

    # Generate IDs
    if not trace_id:
        trace_id = generate_trace_id()
    if not run_id:
        run_id = str(uuid.uuid4())

    flow_id = generate_flow_id(job.username, trace_id, job.flow_label)
    search_tag = job.pr_comment_search_tag or secrets.token_hex(4)

    if not done_endpt:
        done_endpt = f"s3://{done_bucket}/{run_id}/done"

    # Step 1: Validate
    errors = validate_orders(job)
    if errors:
        return {
            "status": "error",
            "errors": errors,
            "run_id": run_id,
            "trace_id": trace_id,
        }

    # Step 2: Repackage
    leg = create_leg(trace_id)
    repackaged = repackage_orders(
        job=job,
        run_id=run_id,
        trace_id=trace_id,
        flow_id=flow_id,
        internal_bucket=internal_bucket,
    )

    # Step 3: Upload
    upload_orders(repackaged, run_id, internal_bucket)

    # Step 4: Insert into DynamoDB
    insert_orders(
        job=job,
        run_id=run_id,
        flow_id=flow_id,
        trace_id=trace_id,
        repackaged_orders=repackaged,
        internal_bucket=internal_bucket,
    )

    # Step 5: PR comment
    pr_comment_id = init_pr_comment(
        job=job,
        run_id=run_id,
        flow_id=flow_id,
        search_tag=search_tag,
        repackaged_orders=repackaged,
    )

    # Step 6: Write init trigger to kick off orchestrator
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
        "pr_search_tag": search_tag,
        "init_pr_comment": pr_comment_id,
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
    is_apigw = "httpMethod" in event

    try:
        payload = _normalize_event(event)

        # API Gateway method rejection
        if "_apigw_error" in payload:
            return _apigw_response(405, {"status": "error", "error": payload["_apigw_error"]})

        job_parameters_b64 = payload.get("job_parameters_b64", "")

        if not job_parameters_b64:
            result = {"status": "error", "error": "Missing job_parameters_b64"}
            return _apigw_response(400, result) if is_apigw else result

        result = process_job_and_insert_orders(
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
        logger.exception("init_job failed")
        result = {"status": "error", "error": str(e)}
        return _apigw_response(500, result) if is_apigw else result
