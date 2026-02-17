"""Dispatch ready orders to Lambda or CodeBuild."""

import json
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List

import boto3

from src.common import dynamodb
from src.common.models import RUNNING

logger = logging.getLogger(__name__)


def _dispatch_lambda(order: dict, run_id: str, internal_bucket: str) -> str:
    """Invoke the worker Lambda for an order. Returns execution ARN/request ID."""
    lambda_client = boto3.client("lambda")
    function_name = os.environ.get("IAC_CI_WORKER_LAMBDA", "iac-ci-worker")

    payload = {
        "s3_location": order.get("s3_location", ""),
        "internal_bucket": internal_bucket,
    }

    resp = lambda_client.invoke(
        FunctionName=function_name,
        InvocationType="Event",  # async
        Payload=json.dumps(payload).encode(),
    )
    return resp.get("ResponseMetadata", {}).get("RequestId", "")


def _dispatch_codebuild(order: dict, run_id: str, internal_bucket: str) -> str:
    """Start a CodeBuild project for an order. Returns build ID."""
    codebuild_client = boto3.client("codebuild")
    project_name = os.environ.get("IAC_CI_CODEBUILD_PROJECT", "iac-ci-worker")

    resp = codebuild_client.start_build(
        projectName=project_name,
        environmentVariablesOverride=[
            {"name": "S3_LOCATION", "value": order.get("s3_location", ""), "type": "PLAINTEXT"},
            {"name": "INTERNAL_BUCKET", "value": internal_bucket, "type": "PLAINTEXT"},
        ],
    )
    return resp.get("build", {}).get("id", "")


def _start_watchdog(
    order: dict,
    run_id: str,
    internal_bucket: str,
) -> str:
    """Start the watchdog Step Function for timeout safety. Returns execution ARN."""
    sfn_client = boto3.client("stepfunctions")
    state_machine_arn = os.environ.get("IAC_CI_WATCHDOG_SFN", "")

    order_num = order.get("order_num", "")
    timeout = order.get("timeout", 300)

    sfn_input = {
        "run_id": run_id,
        "order_num": order_num,
        "timeout": timeout,
        "start_time": int(time.time()),
        "internal_bucket": internal_bucket,
    }

    resp = sfn_client.start_execution(
        stateMachineArn=state_machine_arn,
        name=f"{run_id}-{order_num}",
        input=json.dumps(sfn_input),
    )
    return resp.get("executionArn", "")


def _dispatch_single(
    order: dict,
    run_id: str,
    flow_id: str,
    trace_id: str,
    internal_bucket: str,
    dynamodb_resource=None,
) -> dict:
    """Dispatch a single order (Lambda or CodeBuild) + start watchdog."""
    order_num = order.get("order_num", "")
    order_name = order.get("order_name", order_num)

    # Dispatch to execution environment
    if order.get("use_lambda"):
        execution_id = _dispatch_lambda(order, run_id, internal_bucket)
    else:
        execution_id = _dispatch_codebuild(order, run_id, internal_bucket)

    # Start watchdog
    watchdog_arn = _start_watchdog(order, run_id, internal_bucket)

    # Update order status to running
    dynamodb.update_order_status(
        run_id=run_id,
        order_num=order_num,
        status=RUNNING,
        extra_fields={
            "execution_url": execution_id,
            "step_function_url": watchdog_arn,
        },
        dynamodb_resource=dynamodb_resource,
    )

    # Write order event
    dynamodb.put_event(
        trace_id=trace_id,
        order_name=order_name,
        event_type="dispatched",
        status=RUNNING,
        extra_fields={
            "run_id": run_id,
            "order_num": order_num,
            "flow_id": flow_id,
            "execution_url": execution_id,
        },
        dynamodb_resource=dynamodb_resource,
    )

    return {
        "order_num": order_num,
        "order_name": order_name,
        "execution_id": execution_id,
        "watchdog_arn": watchdog_arn,
    }


def dispatch_orders(
    ready_orders: List[dict],
    run_id: str,
    flow_id: str,
    trace_id: str,
    internal_bucket: str = "",
    dynamodb_resource=None,
) -> List[dict]:
    """Dispatch all ready orders in parallel.

    Returns list of dispatch results.
    """
    if not internal_bucket:
        internal_bucket = os.environ.get("IAC_CI_INTERNAL_BUCKET", "")

    if not ready_orders:
        return []

    results = []

    with ThreadPoolExecutor(max_workers=min(len(ready_orders), 10)) as executor:
        futures = {
            executor.submit(
                _dispatch_single,
                order, run_id, flow_id, trace_id,
                internal_bucket, dynamodb_resource,
            ): order
            for order in ready_orders
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
            except Exception as e:
                order = futures[future]
                logger.error(
                    "Failed to dispatch order %s: %s",
                    order.get("order_num"), e,
                )

    return results
