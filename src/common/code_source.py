"""Shared code source operations — git clone, S3 fetch, credential retrieval, zip."""

import os
import shutil
import subprocess
import tempfile
import zipfile
from typing import Dict, List, Optional, Tuple

import boto3


def fetch_ssm_values(paths: List[str], region: Optional[str] = None) -> Dict[str, str]:
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


def fetch_secret_values(paths: List[str], region: Optional[str] = None) -> Dict[str, str]:
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


def clone_repo(
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


def extract_folder(clone_dir: str, folder: Optional[str] = None) -> str:
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


def group_git_orders(
    orders,
    job,
) -> Tuple[Dict[Tuple[str, Optional[str]], list], List[int]]:
    """Group git-sourced orders by (repo, commit_hash).

    Returns:
        git_groups: dict mapping (repo, commit_hash) -> list of (order_index, order)
        s3_indices: list of order indices that use S3 source
    """
    git_groups: Dict[Tuple[str, Optional[str]], list] = {}
    s3_indices: List[int] = []

    for i, order in enumerate(orders):
        if getattr(order, "s3_location", None):
            s3_indices.append(i)
        else:
            repo = getattr(order, "git_repo", None) or getattr(job, "git_repo", None) or ""
            commit = getattr(order, "commit_hash", None) or getattr(job, "commit_hash", None)
            key = (repo, commit)
            git_groups.setdefault(key, []).append((i, order))

    return git_groups, s3_indices


def fetch_code_s3(s3_location: str) -> str:
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


def zip_directory(code_dir: str, output_path: str) -> str:
    """Zip a directory into output_path."""
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(code_dir):
            for f in files:
                full_path = os.path.join(root, f)
                arcname = os.path.relpath(full_path, code_dir)
                zf.write(full_path, arcname)
    return output_path
