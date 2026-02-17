"""Unit tests for src/process_webhook/handler.py."""

import base64
import json
from unittest.mock import patch, MagicMock

import pytest

from src.common.models import Job, Order
from src.process_webhook.handler import handler, _normalize_event, process_job_and_insert_orders


def _make_job_b64(**kwargs):
    defaults = {
        "git_repo": "org/repo",
        "git_token_location": "aws:::ssm:/token",
        "username": "testuser",
        "orders": [{"cmds": ["echo hi"], "timeout": 300}],
    }
    defaults.update(kwargs)
    return base64.b64encode(json.dumps(defaults).encode()).decode()


class TestNormalizeEvent:
    def test_api_gateway_event(self):
        event = {
            "body": json.dumps({"job_parameters_b64": "abc123"}),
        }
        result = _normalize_event(event)
        assert result["job_parameters_b64"] == "abc123"

    def test_api_gateway_base64_encoded(self):
        payload = json.dumps({"job_parameters_b64": "abc123"})
        event = {
            "body": base64.b64encode(payload.encode()).decode(),
            "isBase64Encoded": True,
        }
        result = _normalize_event(event)
        assert result["job_parameters_b64"] == "abc123"

    def test_sns_event(self):
        event = {
            "Records": [{
                "Sns": {
                    "Message": json.dumps({"job_parameters_b64": "fromSNS"}),
                },
            }],
        }
        result = _normalize_event(event)
        assert result["job_parameters_b64"] == "fromSNS"

    def test_direct_invoke(self):
        event = {"job_parameters_b64": "direct"}
        result = _normalize_event(event)
        assert result["job_parameters_b64"] == "direct"


class TestHandler:
    def test_missing_job_parameters_returns_400(self):
        event = {"body": json.dumps({})}
        resp = handler(event)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert "Missing" in body["error"]

    @patch("src.process_webhook.handler.process_job_and_insert_orders")
    def test_valid_request_returns_200(self, mock_process):
        mock_process.return_value = {
            "status": "ok",
            "run_id": "run-1",
            "trace_id": "abc",
            "flow_id": "user:abc-exec",
            "done_endpt": "s3://done/run-1/done",
            "pr_search_tag": "tag",
            "init_pr_comment": None,
        }

        event = {
            "body": json.dumps({"job_parameters_b64": _make_job_b64()}),
        }
        resp = handler(event)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["run_id"] == "run-1"

    @patch("src.process_webhook.handler.process_job_and_insert_orders")
    def test_validation_error_returns_400(self, mock_process):
        mock_process.return_value = {
            "status": "error",
            "errors": ["order[0]: cmds is empty"],
            "run_id": "run-1",
            "trace_id": "abc",
        }

        event = {
            "body": json.dumps({"job_parameters_b64": _make_job_b64()}),
        }
        resp = handler(event)
        assert resp["statusCode"] == 400

    @patch("src.process_webhook.handler.process_job_and_insert_orders")
    def test_exception_returns_500(self, mock_process):
        mock_process.side_effect = RuntimeError("boom")

        event = {
            "body": json.dumps({"job_parameters_b64": _make_job_b64()}),
        }
        resp = handler(event)
        assert resp["statusCode"] == 500


class TestProcessJobAndInsertOrders:
    @patch("src.process_webhook.handler.s3_ops.write_init_trigger")
    @patch("src.process_webhook.handler.init_pr_comment")
    @patch("src.process_webhook.handler.insert_orders")
    @patch("src.process_webhook.handler.upload_orders")
    @patch("src.process_webhook.handler.repackage_orders")
    @patch("src.process_webhook.handler.validate_orders")
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
