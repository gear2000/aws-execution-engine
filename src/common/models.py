"""Data models for iac-ci."""

import base64
import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional

# Status constants
QUEUED = "queued"
RUNNING = "running"
SUCCEEDED = "succeeded"
FAILED = "failed"
TIMED_OUT = "timed_out"

# Reserved order name for job-level events
JOB_ORDER_NAME = "_job"


@dataclass
class Order:
    """Per-order fields from job parameters."""

    cmds: List[str]
    timeout: int
    order_name: Optional[str] = None
    git_repo: Optional[str] = None
    git_folder: Optional[str] = None
    commit_hash: Optional[str] = None
    s3_location: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    ssm_paths: Optional[List[str]] = None
    secret_manager_paths: Optional[List[str]] = None
    sops_key: Optional[str] = None
    use_lambda: bool = False
    queue_id: Optional[str] = None
    dependencies: Optional[List[str]] = None
    must_succeed: bool = True
    callback_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Order":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class Job:
    """Global job-level fields plus list of orders."""

    git_repo: str
    git_token_location: str
    username: str
    orders: List[Order]
    pr_number: Optional[int] = None
    issue_number: Optional[int] = None
    git_ssh_key_location: Optional[str] = None
    commit_hash: Optional[str] = None
    flow_label: str = "exec"
    pr_comment_search_tag: Optional[str] = None
    presign_expiry: int = 7200
    job_timeout: int = 3600

    def to_dict(self) -> dict:
        d = asdict(self)
        d["orders"] = [o.to_dict() for o in self.orders]
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        orders_data = data.get("orders", [])
        orders = [Order.from_dict(o) for o in orders_data]
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields and k != "orders"}
        return cls(orders=orders, **filtered)

    def to_b64(self) -> str:
        return base64.b64encode(json.dumps(self.to_dict()).encode()).decode()

    @classmethod
    def from_b64(cls, b64_str: str) -> "Job":
        data = json.loads(base64.b64decode(b64_str).decode())
        return cls.from_dict(data)


@dataclass
class OrderEvent:
    """Event record for the order_events DynamoDB table."""

    trace_id: str
    order_name: str
    epoch: float
    event_type: str
    status: str
    log_location: Optional[str] = None
    execution_url: Optional[str] = None
    message: Optional[str] = None
    flow_id: Optional[str] = None
    run_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "OrderEvent":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class LockRecord:
    """Lock record for the orchestrator_locks DynamoDB table."""

    run_id: str
    orchestrator_id: str
    status: str
    acquired_at: float
    ttl: int
    flow_id: Optional[str] = None
    trace_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "LockRecord":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class OrderRecord:
    """DynamoDB record representation for the orders table.

    PK format: <run_id>:<order_num>
    """

    run_id: str
    order_num: str
    trace_id: str
    flow_id: str
    order_name: str
    cmds: List[str]
    status: str = QUEUED
    queue_id: Optional[str] = None
    s3_location: Optional[str] = None
    callback_url: Optional[str] = None
    use_lambda: bool = False
    git_b64: Optional[str] = None
    dependencies: Optional[List[str]] = None
    must_succeed: bool = True
    timeout: int = 300
    created_at: Optional[float] = None
    last_update: Optional[float] = None
    execution_url: Optional[str] = None
    step_function_url: Optional[str] = None
    ttl: Optional[int] = None

    @property
    def pk(self) -> str:
        return f"{self.run_id}:{self.order_num}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["pk"] = self.pk
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "OrderRecord":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)
