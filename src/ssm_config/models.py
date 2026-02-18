"""Data models for SSM config provider."""

import base64
import json
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


@dataclass
class SsmOrder:
    """Per-order fields for SSM execution."""

    cmds: List[str]
    timeout: int
    ssm_targets: Dict[str, Any]
    order_name: Optional[str] = None
    git_repo: Optional[str] = None
    git_folder: Optional[str] = None
    commit_hash: Optional[str] = None
    s3_location: Optional[str] = None
    env_vars: Optional[Dict[str, str]] = None
    ssm_paths: Optional[List[str]] = None
    secret_manager_paths: Optional[List[str]] = None
    ssm_document_name: Optional[str] = None
    queue_id: Optional[str] = None
    dependencies: Optional[List[str]] = None
    must_succeed: bool = True
    callback_url: Optional[str] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "SsmOrder":
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


@dataclass
class SsmJob:
    """Job-level fields for SSM config provider."""

    username: str
    orders: List[SsmOrder]
    git_repo: Optional[str] = None
    git_token_location: Optional[str] = None
    git_ssh_key_location: Optional[str] = None
    commit_hash: Optional[str] = None
    flow_label: str = "ssm"
    presign_expiry: int = 7200
    job_timeout: int = 3600

    def to_dict(self) -> dict:
        d = asdict(self)
        d["orders"] = [o.to_dict() for o in self.orders]
        return {k: v for k, v in d.items() if v is not None}

    @classmethod
    def from_dict(cls, data: dict) -> "SsmJob":
        orders_data = data.get("orders", [])
        orders = [SsmOrder.from_dict(o) for o in orders_data]
        known_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in known_fields and k != "orders"}
        return cls(orders=orders, **filtered)

    def to_b64(self) -> str:
        return base64.b64encode(json.dumps(self.to_dict()).encode()).decode()

    @classmethod
    def from_b64(cls, b64_str: str) -> "SsmJob":
        data = json.loads(base64.b64decode(b64_str).decode())
        return cls.from_dict(data)
