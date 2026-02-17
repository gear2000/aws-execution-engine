"""Worker execution logic — download, decrypt, run commands, callback."""

import json
import logging
import os
import signal
import subprocess
import tempfile
import zipfile
from typing import Optional

import boto3

from src.common import sops
from src.worker.callback import send_callback

logger = logging.getLogger(__name__)


def _download_and_extract(s3_location: str) -> str:
    """Download exec.zip from S3 and extract to temp directory."""
    work_dir = tempfile.mkdtemp(prefix="iac-ci-worker-")

    # Parse s3://bucket/key
    parts = s3_location.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""

    local_zip = os.path.join(work_dir, "exec.zip")
    s3_client = boto3.client("s3")
    s3_client.download_file(bucket, key, local_zip)

    with zipfile.ZipFile(local_zip, "r") as zf:
        zf.extractall(work_dir)
    os.unlink(local_zip)

    return work_dir


def _decrypt_and_load_env(work_dir: str) -> dict:
    """Find SOPS encrypted file, decrypt, and load env vars."""
    encrypted_path = os.path.join(work_dir, "secrets.enc.json")
    if not os.path.exists(encrypted_path):
        return {}

    # SOPS_AGE_KEY should be set in environment by the caller
    sops_key = os.environ.get("SOPS_AGE_KEY", "")
    if not sops_key:
        sops_key_file = os.environ.get("SOPS_AGE_KEY_FILE", "")
        if sops_key_file:
            sops_key = sops_key_file

    if not sops_key:
        logger.warning("No SOPS key found, skipping decryption")
        return {}

    env_vars = sops.decrypt_env(encrypted_path, sops_key)

    # Load into os.environ
    for k, v in env_vars.items():
        os.environ[k] = str(v)

    return env_vars


def _execute_commands(cmds: list, work_dir: str, timeout: int = 0) -> tuple:
    """Execute commands sequentially, capturing output.

    Returns (status, combined_log).
    """
    combined_log = []
    status = "succeeded"

    for cmd in cmds:
        logger.info("Executing: %s", cmd)
        combined_log.append(f"$ {cmd}")

        try:
            proc = subprocess.Popen(
                cmd,
                shell=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=work_dir,
                env=os.environ.copy(),
            )

            if timeout > 0:
                stdout, _ = proc.communicate(timeout=timeout)
            else:
                stdout, _ = proc.communicate()

            output = stdout.decode("utf-8", errors="replace") if stdout else ""
            combined_log.append(output)

            if proc.returncode != 0:
                combined_log.append(f"Exit code: {proc.returncode}")
                status = "failed"
                break

        except subprocess.TimeoutExpired:
            proc.kill()
            proc.communicate()
            combined_log.append(f"Command timed out after {timeout}s")
            status = "timed_out"
            break
        except Exception as e:
            combined_log.append(f"Error: {e}")
            status = "failed"
            break

    return status, "\n".join(combined_log)


def run(s3_location: str, internal_bucket: str = "") -> str:
    """Main worker execution flow.

    1. Download and extract exec.zip
    2. Decrypt SOPS -> load env vars
    3. Execute commands
    4. Send callback

    Returns final status.
    """
    # 1. Download and extract
    work_dir = _download_and_extract(s3_location)

    # 2. Decrypt and load env vars
    env_vars = _decrypt_and_load_env(work_dir)

    # 3. Read commands from order config (if present) or env
    cmds_str = env_vars.get("CMDS", "")
    if cmds_str:
        try:
            cmds = json.loads(cmds_str)
        except json.JSONDecodeError:
            cmds = [cmds_str]
    else:
        # Look for cmds.json in work dir
        cmds_file = os.path.join(work_dir, "cmds.json")
        if os.path.exists(cmds_file):
            with open(cmds_file) as f:
                cmds = json.load(f)
        else:
            cmds = []

    if not cmds:
        logger.error("No commands found to execute")
        callback_url = env_vars.get("CALLBACK_URL", "")
        if callback_url:
            send_callback(callback_url, "failed", "No commands found")
        return "failed"

    # 4. Execute
    timeout = int(env_vars.get("TIMEOUT", os.environ.get("TIMEOUT", "0")))
    status, log_output = _execute_commands(cmds, work_dir, timeout=timeout)

    # 5. Callback
    callback_url = env_vars.get("CALLBACK_URL", "")
    if callback_url:
        send_callback(callback_url, status, log_output)
    else:
        logger.warning("No CALLBACK_URL found, skipping callback")

    return status
