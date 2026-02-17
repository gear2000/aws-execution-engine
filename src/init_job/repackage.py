"""Repackage orders with credentials and encrypted env vars."""

import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

import boto3

from src.common.bundler import OrderBundler
from src.common.models import Job, Order
from src.common import s3 as s3_ops


def _fetch_ssm_values(paths: List[str], region: Optional[str] = None) -> Dict[str, str]:
    """Fetch values from AWS SSM Parameter Store."""
    if not paths:
        return {}
    client = boto3.client("ssm", region_name=region)
    result = {}
    for path in paths:
        resp = client.get_parameter(Name=path, WithDecryption=True)
        # Use the last segment of the path as the env var name
        key = path.rsplit("/", 1)[-1].upper().replace("-", "_")
        result[key] = resp["Parameter"]["Value"]
    return result


def _fetch_secret_values(paths: List[str], region: Optional[str] = None) -> Dict[str, str]:
    """Fetch values from AWS Secrets Manager."""
    if not paths:
        return {}
    client = boto3.client("secretsmanager", region_name=region)
    result = {}
    for path in paths:
        resp = client.get_secret_value(SecretId=path)
        key = path.rsplit("/", 1)[-1].upper().replace("-", "_")
        result[key] = resp["SecretString"]
    return result


def _fetch_code_git(
    repo: str,
    token_location: str,
    folder: Optional[str] = None,
    ssh_key_location: Optional[str] = None,
) -> str:
    """Clone a git repo and return path to code directory."""
    work_dir = tempfile.mkdtemp(prefix="iac-ci-git-")
    clone_url = f"https://github.com/{repo}.git"
    subprocess.run(
        ["git", "clone", "--depth", "1", clone_url, work_dir],
        check=True,
        capture_output=True,
        text=True,
    )
    if folder:
        return os.path.join(work_dir, folder)
    return work_dir


def _fetch_code_s3(s3_location: str) -> str:
    """Download and extract a zip from S3. Returns path to extracted directory."""
    work_dir = tempfile.mkdtemp(prefix="iac-ci-s3-")
    # Parse s3://bucket/key
    parts = s3_location.replace("s3://", "").split("/", 1)
    bucket = parts[0]
    key = parts[1] if len(parts) > 1 else ""

    local_zip = os.path.join(work_dir, "code.zip")
    s3_client = boto3.client("s3")
    s3_client.download_file(bucket, key, local_zip)

    with zipfile.ZipFile(local_zip, "r") as zf:
        zf.extractall(work_dir)
    os.unlink(local_zip)
    return work_dir


def _zip_directory(code_dir: str, output_path: str) -> str:
    """Zip a directory into output_path."""
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(code_dir):
            for f in files:
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, code_dir)
                zf.write(full_path, arcname)
    return output_path


def repackage_orders(
    job: Job,
    run_id: str,
    trace_id: str,
    flow_id: str,
    internal_bucket: str,
) -> List[Dict]:
    """Repackage all orders with credentials and SOPS encryption.

    Returns list of dicts with keys:
        order_num, order_name, zip_path, callback_url, code_dir
    """
    results = []

    for i, order in enumerate(job.orders):
        order_num = str(i + 1).zfill(4)
        order_name = order.order_name or f"order-{order_num}"

        # 1. Fetch code
        if order.s3_location:
            code_dir = _fetch_code_s3(order.s3_location)
        else:
            repo = order.git_repo or job.git_repo
            code_dir = _fetch_code_git(
                repo=repo,
                token_location=job.git_token_location,
                folder=order.git_folder,
                ssh_key_location=job.git_ssh_key_location,
            )

        # 2. Fetch credentials
        ssm_values = _fetch_ssm_values(order.ssm_paths or [])
        secret_values = _fetch_secret_values(order.secret_manager_paths or [])

        # 3. Generate presigned callback URL
        callback_url = s3_ops.generate_callback_presigned_url(
            bucket=internal_bucket,
            run_id=run_id,
            order_num=order_num,
            expiry=job.presign_expiry,
        )

        # 4. Build and encrypt with OrderBundler
        bundler = OrderBundler(
            run_id=run_id,
            order_id=order_name,
            order_num=order_num,
            trace_id=trace_id,
            flow_id=flow_id,
            env_vars=order.env_vars or {},
            ssm_values=ssm_values,
            secret_values=secret_values,
            callback_url=callback_url,
        )
        bundler.repackage(code_dir, sops_key=order.sops_key)

        # 5. Re-zip
        zip_path = os.path.join(tempfile.gettempdir(), f"{run_id}_{order_num}_exec.zip")
        _zip_directory(code_dir, zip_path)

        results.append({
            "order_num": order_num,
            "order_name": order_name,
            "zip_path": zip_path,
            "callback_url": callback_url,
            "code_dir": code_dir,
        })

    return results
