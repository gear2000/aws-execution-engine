"""Unit tests for src/worker/run.py."""

import json
import os
import tempfile
import zipfile
from unittest.mock import patch, MagicMock

import pytest

from src.worker.run import run, _execute_commands, _download_and_extract


class TestExecuteCommands:
    def test_successful_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(["echo hello"], tmpdir)
            assert status == "succeeded"
            assert "hello" in log

    def test_failed_command(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(["exit 1"], tmpdir)
            assert status == "failed"
            assert "Exit code: 1" in log

    def test_multiple_commands_in_order(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(
                ["echo first", "echo second"],
                tmpdir,
            )
            assert status == "succeeded"
            assert "first" in log
            assert "second" in log

    def test_stops_on_first_failure(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(
                ["echo before", "exit 1", "echo after"],
                tmpdir,
            )
            assert status == "failed"
            assert "before" in log
            assert "after" not in log

    def test_timeout(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(
                ["sleep 10"],
                tmpdir,
                timeout=1,
            )
            assert status == "timed_out"
            assert "timed out" in log.lower()

    def test_captures_stderr(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            status, log = _execute_commands(
                ["echo error_msg >&2"],
                tmpdir,
            )
            # stderr is merged with stdout via STDOUT redirect
            assert "error_msg" in log


class TestRun:
    @patch("src.worker.run.send_callback")
    @patch("src.worker.run._decrypt_and_load_env")
    @patch("src.worker.run._download_and_extract")
    def test_successful_run(self, mock_download, mock_decrypt, mock_callback):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Create cmds.json
            with open(os.path.join(tmpdir, "cmds.json"), "w") as f:
                json.dump(["echo hello"], f)

            mock_download.return_value = tmpdir
            mock_decrypt.return_value = {"CALLBACK_URL": "https://cb.url"}

            status = run("s3://bucket/exec.zip")

            assert status == "succeeded"
            mock_callback.assert_called_once()
            call_args = mock_callback.call_args[0]
            assert call_args[0] == "https://cb.url"
            assert call_args[1] == "succeeded"

    @patch("src.worker.run.send_callback")
    @patch("src.worker.run._decrypt_and_load_env")
    @patch("src.worker.run._download_and_extract")
    def test_failed_run(self, mock_download, mock_decrypt, mock_callback):
        with tempfile.TemporaryDirectory() as tmpdir:
            with open(os.path.join(tmpdir, "cmds.json"), "w") as f:
                json.dump(["exit 1"], f)

            mock_download.return_value = tmpdir
            mock_decrypt.return_value = {"CALLBACK_URL": "https://cb.url"}

            status = run("s3://bucket/exec.zip")

            assert status == "failed"
            mock_callback.assert_called_once()
            assert mock_callback.call_args[0][1] == "failed"

    @patch("src.worker.run.send_callback")
    @patch("src.worker.run._decrypt_and_load_env")
    @patch("src.worker.run._download_and_extract")
    def test_no_commands(self, mock_download, mock_decrypt, mock_callback):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_download.return_value = tmpdir
            mock_decrypt.return_value = {"CALLBACK_URL": "https://cb.url"}

            status = run("s3://bucket/exec.zip")

            assert status == "failed"

    @patch("src.worker.run.send_callback")
    @patch("src.worker.run._decrypt_and_load_env")
    @patch("src.worker.run._download_and_extract")
    def test_cmds_from_env(self, mock_download, mock_decrypt, mock_callback):
        with tempfile.TemporaryDirectory() as tmpdir:
            mock_download.return_value = tmpdir
            mock_decrypt.return_value = {
                "CALLBACK_URL": "https://cb.url",
                "CMDS": json.dumps(["echo from_env"]),
            }

            status = run("s3://bucket/exec.zip")

            assert status == "succeeded"
