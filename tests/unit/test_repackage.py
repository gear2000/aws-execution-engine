"""Unit tests for src/init_job/repackage.py."""

import os
import tempfile
from unittest.mock import patch, MagicMock

import pytest

from src.common.models import Job, Order
from src.init_job.repackage import (
    repackage_orders,
    _fetch_ssm_values,
    _fetch_secret_values,
    _zip_directory,
)


def _make_job(orders=None, **kwargs):
    defaults = {
        "git_repo": "org/repo",
        "git_token_location": "aws:::ssm:/token",
        "username": "testuser",
    }
    defaults.update(kwargs)
    return Job(orders=orders or [], **defaults)


def _make_order(**kwargs):
    defaults = {
        "cmds": ["echo hello"],
        "timeout": 300,
    }
    defaults.update(kwargs)
    return Order(**defaults)


class TestZipDirectory:
    def test_creates_zip(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create some files
            with open(os.path.join(tmpdir, "test.txt"), "w") as f:
                f.write("hello")
            os.makedirs(os.path.join(tmpdir, "sub"))
            with open(os.path.join(tmpdir, "sub", "nested.txt"), "w") as f:
                f.write("nested")

            zip_path = os.path.join(tmpdir, "output.zip")
            _zip_directory(tmpdir, zip_path)
            assert os.path.exists(zip_path)
            assert os.path.getsize(zip_path) > 0


class TestRepackageOrders:
    @patch("src.init_job.repackage._fetch_code_git")
    @patch("src.init_job.repackage._fetch_ssm_values")
    @patch("src.init_job.repackage._fetch_secret_values")
    @patch("src.init_job.repackage.s3_ops.generate_callback_presigned_url")
    @patch("src.init_job.repackage.OrderBundler")
    def test_repackage_produces_correct_structure(
        self, MockBundler, mock_presign, mock_secrets,
        mock_ssm, mock_git,
    ):
        with tempfile.TemporaryDirectory() as code_dir:
            # Setup mocks
            mock_git.return_value = code_dir
            mock_ssm.return_value = {"DB_PASS": "secret"}
            mock_secrets.return_value = {}
            mock_presign.return_value = "https://presigned.url"

            bundler_instance = MagicMock()
            MockBundler.return_value = bundler_instance

            # Create a file in code_dir so zip isn't empty
            with open(os.path.join(code_dir, "main.tf"), "w") as f:
                f.write("resource {}")

            job = _make_job(orders=[
                _make_order(order_name="deploy-vpc", ssm_paths=["/db/pass"]),
            ])

            results = repackage_orders(
                job=job,
                run_id="run-1",
                trace_id="abc123",
                flow_id="user:abc123-exec",
                internal_bucket="test-bucket",
            )

            assert len(results) == 1
            r = results[0]
            assert r["order_num"] == "0001"
            assert r["order_name"] == "deploy-vpc"
            assert r["callback_url"] == "https://presigned.url"
            assert os.path.exists(r["zip_path"])

            # Verify bundler was created with correct args
            MockBundler.assert_called_once()
            call_kwargs = MockBundler.call_args[1]
            assert call_kwargs["run_id"] == "run-1"
            assert call_kwargs["trace_id"] == "abc123"
            assert call_kwargs["ssm_values"] == {"DB_PASS": "secret"}

    @patch("src.init_job.repackage._fetch_code_s3")
    @patch("src.init_job.repackage._fetch_ssm_values")
    @patch("src.init_job.repackage._fetch_secret_values")
    @patch("src.init_job.repackage.s3_ops.generate_callback_presigned_url")
    @patch("src.init_job.repackage.OrderBundler")
    def test_s3_code_source(
        self, MockBundler, mock_presign, mock_secrets,
        mock_ssm, mock_s3,
    ):
        with tempfile.TemporaryDirectory() as code_dir:
            mock_s3.return_value = code_dir
            mock_ssm.return_value = {}
            mock_secrets.return_value = {}
            mock_presign.return_value = "https://presigned.url"
            MockBundler.return_value = MagicMock()

            with open(os.path.join(code_dir, "script.sh"), "w") as f:
                f.write("#!/bin/bash")

            job = _make_job(orders=[
                _make_order(s3_location="s3://bucket/code.zip"),
            ])

            results = repackage_orders(
                job=job,
                run_id="run-1",
                trace_id="abc",
                flow_id="user:abc-exec",
                internal_bucket="test-bucket",
            )

            assert len(results) == 1
            mock_s3.assert_called_once_with("s3://bucket/code.zip")

    @patch("src.init_job.repackage._fetch_code_git")
    @patch("src.init_job.repackage._fetch_ssm_values")
    @patch("src.init_job.repackage._fetch_secret_values")
    @patch("src.init_job.repackage.s3_ops.generate_callback_presigned_url")
    @patch("src.init_job.repackage.OrderBundler")
    def test_multiple_orders(
        self, MockBundler, mock_presign, mock_secrets,
        mock_ssm, mock_git,
    ):
        with tempfile.TemporaryDirectory() as code_dir:
            mock_git.return_value = code_dir
            mock_ssm.return_value = {}
            mock_secrets.return_value = {}
            mock_presign.return_value = "https://presigned.url"
            MockBundler.return_value = MagicMock()

            with open(os.path.join(code_dir, "f.txt"), "w") as f:
                f.write("x")

            job = _make_job(orders=[
                _make_order(order_name="order-a"),
                _make_order(order_name="order-b"),
            ])

            results = repackage_orders(
                job=job,
                run_id="run-1",
                trace_id="abc",
                flow_id="user:abc-exec",
                internal_bucket="bucket",
            )

            assert len(results) == 2
            assert results[0]["order_num"] == "0001"
            assert results[1]["order_num"] == "0002"
