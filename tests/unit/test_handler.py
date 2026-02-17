"""Unit tests for src/init_job/handler.py."""

import base64
import json
from unittest.mock import patch, MagicMock

import pytest

from src.common.models import Job, Order
from src.init_job.handler import handler, process_job_and_insert_orders


def _make_job_b64(**kwargs):
    defaults = {
        "git_repo": "org/repo",
        "git_token_location": "aws:::ssm:/token",
        "username": "testuser",
        "orders": [{"cmds": ["echo hi"], "timeout": 300}],
    }
    defaults.update(kwargs)
    return base64.b64encode(json.dumps(defaults).encode()).decode()


class TestHandler:
    def test_missing_job_parameters_returns_error(self):
        event = {}
        resp = handler(event)
        assert resp["status"] == "error"
        assert "Missing" in resp["error"]

    @patch("src.init_job.handler.process_job_and_insert_orders")
    def test_valid_request_returns_ok(self, mock_process):
        mock_process.return_value = {
            "status": "ok",
            "run_id": "run-1",
            "trace_id": "abc",
            "flow_id": "user:abc-exec",
            "done_endpt": "s3://done/run-1/done",
            "pr_search_tag": "tag",
            "init_pr_comment": None,
        }

        event = {"job_parameters_b64": _make_job_b64()}
        resp = handler(event)
        assert resp["status"] == "ok"
        assert resp["run_id"] == "run-1"

    @patch("src.init_job.handler.process_job_and_insert_orders")
    def test_validation_error_returns_error(self, mock_process):
        mock_process.return_value = {
            "status": "error",
            "errors": ["order[0]: cmds is empty"],
            "run_id": "run-1",
            "trace_id": "abc",
        }

        event = {"job_parameters_b64": _make_job_b64()}
        resp = handler(event)
        assert resp["status"] == "error"

    @patch("src.init_job.handler.process_job_and_insert_orders")
    def test_exception_returns_error(self, mock_process):
        mock_process.side_effect = RuntimeError("boom")

        event = {"job_parameters_b64": _make_job_b64()}
        resp = handler(event)
        assert resp["status"] == "error"
        assert "boom" in resp["error"]


class TestProcessJobAndInsertOrders:
    @patch("src.init_job.handler.s3_ops.write_init_trigger")
    @patch("src.init_job.handler.init_pr_comment")
    @patch("src.init_job.handler.insert_orders")
    @patch("src.init_job.handler.upload_orders")
    @patch("src.init_job.handler.repackage_orders")
    @patch("src.init_job.handler.validate_orders")
    def test_full_flow(
        self, mock_validate, mock_repackage, mock_upload,
        mock_insert, mock_pr, mock_trigger, monkeypatch,
    ):
        monkeypatch.setenv("IAC_CI_INTERNAL_BUCKET", "internal")
        monkeypatch.setenv("IAC_CI_DONE_BUCKET", "done")

        mock_validate.return_value = []
        mock_repackage.return_value = [
            {"order_num": "0001", "order_name": "test", "zip_path": "/tmp/x.zip",
             "callback_url": "https://cb"},
        ]
        mock_pr.return_value = 42

        result = process_job_and_insert_orders(_make_job_b64())

        assert result["status"] == "ok"
        assert "run_id" in result
        assert "trace_id" in result
        assert "flow_id" in result

        mock_validate.assert_called_once()
        mock_repackage.assert_called_once()
        mock_upload.assert_called_once()
        mock_insert.assert_called_once()
        mock_pr.assert_called_once()
        mock_trigger.assert_called_once()
