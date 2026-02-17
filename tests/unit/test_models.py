"""Unit tests for src/common/models.py."""

import json
import base64

from src.common.models import (
    Order,
    Job,
    OrderEvent,
    LockRecord,
    OrderRecord,
    QUEUED,
    RUNNING,
    SUCCEEDED,
    FAILED,
    TIMED_OUT,
    JOB_ORDER_NAME,
)


class TestStatusConstants:
    def test_status_values(self):
        assert QUEUED == "queued"
        assert RUNNING == "running"
        assert SUCCEEDED == "succeeded"
        assert FAILED == "failed"
        assert TIMED_OUT == "timed_out"

    def test_job_order_name(self):
        assert JOB_ORDER_NAME == "_job"


class TestOrder:
    def test_create_minimal(self):
        order = Order(cmds=["echo hello"], timeout=300)
        assert order.cmds == ["echo hello"]
        assert order.timeout == 300
        assert order.use_lambda is False
        assert order.must_succeed is True
        assert order.dependencies is None

    def test_create_full(self):
        order = Order(
            cmds=["cmd1", "cmd2"],
            timeout=600,
            order_name="deploy-vpc",
            git_repo="org/repo",
            git_folder="terraform/vpc",
            env_vars={"KEY": "VALUE"},
            ssm_paths=["/path/1"],
            secret_manager_paths=["secret/1"],
            use_lambda=True,
            queue_id="vpc-plan",
            dependencies=["dep-1"],
            must_succeed=False,
        )
        assert order.order_name == "deploy-vpc"
        assert order.use_lambda is True
        assert order.must_succeed is False

    def test_to_dict(self):
        order = Order(cmds=["echo hi"], timeout=300)
        d = order.to_dict()
        assert "cmds" in d
        assert "timeout" in d
        # None fields should be excluded
        assert "order_name" not in d
        assert "env_vars" not in d

    def test_from_dict(self):
        data = {
            "cmds": ["echo test"],
            "timeout": 120,
            "order_name": "test-order",
            "use_lambda": True,
            "unknown_field": "ignored",
        }
        order = Order.from_dict(data)
        assert order.cmds == ["echo test"]
        assert order.timeout == 120
        assert order.order_name == "test-order"
        assert order.use_lambda is True

    def test_to_dict_from_dict_roundtrip(self):
        order = Order(
            cmds=["cmd1"],
            timeout=300,
            order_name="test",
            env_vars={"A": "B"},
        )
        d = order.to_dict()
        restored = Order.from_dict(d)
        assert restored.cmds == order.cmds
        assert restored.timeout == order.timeout
        assert restored.order_name == order.order_name
        assert restored.env_vars == order.env_vars


class TestJob:
    def _sample_job(self):
        return Job(
            git_repo="org/repo",
            git_token_location="aws:::ssm:/token",
            username="testuser",
            orders=[
                Order(cmds=["echo 1"], timeout=300, order_name="order-1"),
                Order(cmds=["echo 2"], timeout=600, order_name="order-2"),
            ],
            pr_number=42,
            flow_label="plan",
        )

    def test_create(self):
        job = self._sample_job()
        assert job.git_repo == "org/repo"
        assert len(job.orders) == 2
        assert job.pr_number == 42
        assert job.flow_label == "plan"
        assert job.presign_expiry == 7200
        assert job.job_timeout == 3600

    def test_to_dict(self):
        job = self._sample_job()
        d = job.to_dict()
        assert d["git_repo"] == "org/repo"
        assert len(d["orders"]) == 2
        assert "cmds" in d["orders"][0]

    def test_from_dict(self):
        data = {
            "git_repo": "org/repo",
            "git_token_location": "aws:::ssm:/token",
            "username": "user1",
            "pr_number": 10,
            "orders": [
                {"cmds": ["cmd1"], "timeout": 300},
            ],
        }
        job = Job.from_dict(data)
        assert job.git_repo == "org/repo"
        assert len(job.orders) == 1
        assert job.orders[0].cmds == ["cmd1"]

    def test_to_b64_from_b64_roundtrip(self):
        job = self._sample_job()
        b64_str = job.to_b64()
        # Verify it's valid base64
        decoded = json.loads(base64.b64decode(b64_str).decode())
        assert decoded["git_repo"] == "org/repo"
        # Verify roundtrip
        restored = Job.from_b64(b64_str)
        assert restored.git_repo == job.git_repo
        assert restored.username == job.username
        assert len(restored.orders) == len(job.orders)
        assert restored.orders[0].order_name == job.orders[0].order_name

    def test_default_flow_label(self):
        job = Job(
            git_repo="org/repo",
            git_token_location="aws:::ssm:/token",
            username="user",
            orders=[],
        )
        assert job.flow_label == "exec"


class TestOrderEvent:
    def test_create(self):
        event = OrderEvent(
            trace_id="abc123",
            order_name="deploy-vpc",
            epoch=1708099200.0,
            event_type="dispatched",
            status=RUNNING,
            flow_id="user:abc123-exec",
            run_id="run-1",
        )
        assert event.trace_id == "abc123"
        assert event.order_name == "deploy-vpc"
        assert event.status == RUNNING

    def test_to_dict_excludes_none(self):
        event = OrderEvent(
            trace_id="abc",
            order_name="test",
            epoch=100.0,
            event_type="completed",
            status=SUCCEEDED,
        )
        d = event.to_dict()
        assert "log_location" not in d
        assert "execution_url" not in d

    def test_from_dict(self):
        data = {
            "trace_id": "abc",
            "order_name": "test",
            "epoch": 100.0,
            "event_type": "completed",
            "status": "succeeded",
            "extra_field": "ignored",
        }
        event = OrderEvent.from_dict(data)
        assert event.trace_id == "abc"
        assert event.status == "succeeded"


class TestLockRecord:
    def test_create(self):
        lock = LockRecord(
            run_id="run-1",
            orchestrator_id="orch-1",
            status="active",
            acquired_at=1708099200.0,
            ttl=3600,
            flow_id="user:abc-exec",
            trace_id="abc",
        )
        assert lock.run_id == "run-1"
        assert lock.status == "active"

    def test_to_dict(self):
        lock = LockRecord(
            run_id="run-1",
            orchestrator_id="orch-1",
            status="active",
            acquired_at=1000.0,
            ttl=3600,
        )
        d = lock.to_dict()
        assert d["run_id"] == "run-1"
        assert "flow_id" not in d

    def test_from_dict(self):
        data = {
            "run_id": "run-1",
            "orchestrator_id": "orch-1",
            "status": "active",
            "acquired_at": 1000.0,
            "ttl": 3600,
        }
        lock = LockRecord.from_dict(data)
        assert lock.run_id == "run-1"


class TestOrderRecord:
    def test_pk_format(self):
        record = OrderRecord(
            run_id="run-123",
            order_num="001",
            trace_id="abc",
            flow_id="user:abc-exec",
            order_name="deploy",
            cmds=["echo hi"],
        )
        assert record.pk == "run-123:001"

    def test_to_dict_includes_pk(self):
        record = OrderRecord(
            run_id="run-1",
            order_num="001",
            trace_id="abc",
            flow_id="user:abc-exec",
            order_name="deploy",
            cmds=["cmd1"],
        )
        d = record.to_dict()
        assert d["pk"] == "run-1:001"
        assert d["status"] == QUEUED

    def test_from_dict(self):
        data = {
            "run_id": "run-1",
            "order_num": "001",
            "trace_id": "abc",
            "flow_id": "user:abc-exec",
            "order_name": "deploy",
            "cmds": ["cmd1"],
            "status": RUNNING,
        }
        record = OrderRecord.from_dict(data)
        assert record.status == RUNNING
