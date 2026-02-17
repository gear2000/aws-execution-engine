"""Lambda entrypoint for process_webhook — Part 1: init_job."""

import base64
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
from src.process_webhook.validate import validate_orders
from src.process_webhook.repackage import repackage_orders
from src.process_webhook.upload import upload_orders
from src.process_webhook.insert import insert_orders
from src.process_webhook.pr_comment import init_pr_comment

logger = logging.getLogger(__name__)


def _normalize_event(event: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize event from API Gateway, SNS, direct invoke, or Lambda URL."""
    # SNS event
    if "Records" in event and event["Records"]:
        record = event["Records"][0]
        if "Sns" in record:
            return json.loads(record["Sns"]["Message"])

    # API Gateway (REST or HTTP API) / Lambda URL
    if "body" in event:
        body = event["body"]
        if isinstance(body, str):
            # Check if base64 encoded
            if event.get("isBase64Encoded"):
                body = base64.b64decode(body).decode()
            return json.loads(body)
        return body

    # Direct invoke — event is the payload itself
    return event


def _build_response(status_code: int, body: dict) -> dict:
    """Build API Gateway compatible response."""
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


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


def handler(event: Dict[str, Any], context: Any = None) -> dict:
    """Lambda entrypoint."""
    try:
        normalized = _normalize_event(event)
        job_parameters_b64 = normalized.get("job_parameters_b64", "")

        if not job_parameters_b64:
            return _build_response(400, {"error": "Missing job_parameters_b64"})

        result = process_job_and_insert_orders(
            job_parameters_b64=job_parameters_b64,
            trace_id=normalized.get("trace_id", ""),
            run_id=normalized.get("run_id", ""),
            done_endpt=normalized.get("done_endpt", ""),
        )

        if result.get("status") == "error":
            return _build_response(400, result)

        return _build_response(200, result)

    except Exception as e:
        logger.exception("process_webhook failed")
        return _build_response(500, {"error": str(e)})
