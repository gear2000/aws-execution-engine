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


def _clone_repo(
    repo: str,
    token_location: str,
    commit_hash: Optional[str] = None,
    ssh_key_location: Optional[str] = None,
) -> str:
    """Clone a git repo and optionally checkout a specific commit.

    Returns the path to the repository root directory.
    Uses --depth 1 for HEAD, --depth 2 when a specific commit_hash is requested.
    """
    work_dir = tempfile.mkdtemp(prefix="iac-ci-git-")
    clone_url = f"https://github.com/{repo}.git"

    depth = "2" if commit_hash else "1"
    subprocess.run(
        ["git", "clone", "--depth", depth, clone_url, work_dir],
        check=True,
        capture_output=True,
        text=True,
    )

    if commit_hash:
        subprocess.run(
            ["git", "checkout", commit_hash],
            check=True,
            capture_output=True,
            text=True,
            cwd=work_dir,
        )

    return work_dir


def _extract_folder(clone_dir: str, folder: Optional[str] = None) -> str:
    """Copy a folder (or the entire repo) from a shared clone into an isolated temp dir.

    Each order needs its own copy because OrderBundler writes files in-place.
    Excludes .git directory to save space.
    """
    source = os.path.join(clone_dir, folder) if folder else clone_dir
    if not os.path.isdir(source):
        raise FileNotFoundError(
            f"Folder '{folder}' not found in cloned repo at {clone_dir}"
        )
    isolated_dir = tempfile.mkdtemp(prefix="iac-ci-order-")
    shutil.copytree(source, isolated_dir, dirs_exist_ok=True,
                    ignore=shutil.ignore_patterns(".git"))
    return isolated_dir


def _group_git_orders(
    job: Job,
) -> Tuple[Dict[Tuple[str, Optional[str]], List[Tuple[int, Order]]], List[int]]:
    """Group git-sourced orders by (repo, commit_hash).

    Returns:
        git_groups: dict mapping (repo, commit_hash) -> list of (order_index, order)
        s3_indices: list of order indices that use S3 source
    """
    git_groups: Dict[Tuple[str, Optional[str]], List[Tuple[int, Order]]] = {}
    s3_indices: List[int] = []

    for i, order in enumerate(job.orders):
        if order.s3_location:
            s3_indices.append(i)
        else:
            repo = order.git_repo or job.git_repo
            commit = order.commit_hash or job.commit_hash
            key = (repo, commit)
            git_groups.setdefault(key, []).append((i, order))

    return git_groups, s3_indices


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


def _process_order(
    job: Job,
    order: Order,
    order_index: int,
    code_dir: str,
    run_id: str,
    trace_id: str,
    flow_id: str,
    internal_bucket: str,
) -> Dict:
    """Process a single order: fetch credentials, bundle, zip."""
    order_num = str(order_index + 1).zfill(4)
    order_name = order.order_name or f"order-{order_num}"

    # Fetch credentials
    ssm_values = _fetch_ssm_values(order.ssm_paths or [])
    secret_values = _fetch_secret_values(order.secret_manager_paths or [])

    # Generate presigned callback URL
    callback_url = s3_ops.generate_callback_presigned_url(
        bucket=internal_bucket,
        run_id=run_id,
        order_num=order_num,
        expiry=job.presign_expiry,
    )

    # Build and encrypt with OrderBundler
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

    # Re-zip
    zip_path = os.path.join(tempfile.gettempdir(), f"{run_id}_{order_num}_exec.zip")
    _zip_directory(code_dir, zip_path)

    return {
        "order_num": order_num,
        "order_name": order_name,
        "zip_path": zip_path,
        "callback_url": callback_url,
        "code_dir": code_dir,
    }


def repackage_orders(
    job: Job,
    run_id: str,
    trace_id: str,
    flow_id: str,
    internal_bucket: str,
) -> List[Dict]:
    """Repackage all orders with credentials and SOPS encryption.

    Groups git-sourced orders by (repo, commit_hash) to avoid redundant clones.

    Returns list of dicts with keys:
        order_num, order_name, zip_path, callback_url, code_dir
    """
    results: List[Optional[Dict]] = [None] * len(job.orders)
    shared_clone_dirs: List[str] = []

    try:
        # Phase 1: Group git orders and clone once per unique (repo, commit_hash)
        git_groups, s3_indices = _group_git_orders(job)

        for (repo, commit_hash), order_entries in git_groups.items():
            clone_dir = _clone_repo(
                repo=repo,
                token_location=job.git_token_location,
                commit_hash=commit_hash,
                ssh_key_location=job.git_ssh_key_location,
            )
            shared_clone_dirs.append(clone_dir)

            for i, order in order_entries:
                code_dir = _extract_folder(clone_dir, order.git_folder)
                results[i] = _process_order(
                    job=job,
                    order=order,
                    order_index=i,
                    code_dir=code_dir,
                    run_id=run_id,
                    trace_id=trace_id,
                    flow_id=flow_id,
                    internal_bucket=internal_bucket,
                )

        # Phase 2: Process S3-sourced orders (unchanged)
        for i in s3_indices:
            order = job.orders[i]
            code_dir = _fetch_code_s3(order.s3_location)
            results[i] = _process_order(
                job=job,
                order=order,
                order_index=i,
                code_dir=code_dir,
                run_id=run_id,
                trace_id=trace_id,
                flow_id=flow_id,
                internal_bucket=internal_bucket,
            )
    finally:
        # Clean up shared clone directories
        for clone_dir in shared_clone_dirs:
            shutil.rmtree(clone_dir, ignore_errors=True)

    return results
