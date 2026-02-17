"""SOPS encryption/decryption for cross-account credential management."""

import json
import os
import subprocess
import tempfile
from typing import Dict, Optional, Tuple


def _run_cmd(cmd: list, env: Optional[dict] = None) -> str:
    """Run a subprocess command and return stdout."""
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        env={**os.environ, **(env or {})},
    )
    if result.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{result.stderr}")
    return result.stdout


def _generate_age_key() -> Tuple[str, str]:
    """Generate a temporary age key pair.

    Returns (public_key, secret_key_file_path).
    """
    key_file = tempfile.mktemp(suffix=".key")
    output = _run_cmd(["age-keygen", "-o", key_file])
    # age-keygen outputs public key to stderr in format: "# public key: age1..."
    # Read the key file to get the secret key
    with open(key_file, "r") as f:
        content = f.read()
    # Extract public key from the comment line
    public_key = None
    for line in content.splitlines():
        if line.startswith("# public key:"):
            public_key = line.split(":", 1)[1].strip()
            break
    if not public_key:
        raise RuntimeError("Failed to extract public key from age-keygen output")
    return public_key, key_file


def encrypt_env(
    env_vars: Dict[str, str],
    sops_key: Optional[str] = None,
) -> Tuple[str, str]:
    """Encrypt a dict of env vars with SOPS.

    If no sops_key provided, generates a temporary age key.
    Returns (path_to_encrypted_file, key_used).
    """
    # Write plaintext env vars as JSON
    plaintext_file = tempfile.mktemp(suffix=".json")
    with open(plaintext_file, "w") as f:
        json.dump(env_vars, f)

    encrypted_file = tempfile.mktemp(suffix=".enc.json")

    if sops_key is None:
        public_key, key_file = _generate_age_key()
        sops_key = public_key
        env_extra = {"SOPS_AGE_KEY_FILE": key_file}
    else:
        env_extra = {}

    _run_cmd(
        [
            "sops",
            "--encrypt",
            "--age", sops_key,
            "--input-type", "json",
            "--output-type", "json",
            "--output", encrypted_file,
            plaintext_file,
        ],
        env=env_extra,
    )

    # Clean up plaintext
    os.unlink(plaintext_file)

    return encrypted_file, sops_key


def decrypt_env(
    encrypted_path: str,
    sops_key: str,
) -> Dict[str, str]:
    """Decrypt a SOPS file and return dict of env vars."""
    env_extra = {}
    # If it looks like an age key file path, set SOPS_AGE_KEY_FILE
    if os.path.isfile(sops_key):
        env_extra["SOPS_AGE_KEY_FILE"] = sops_key
    else:
        env_extra["SOPS_AGE_KEY"] = sops_key

    output = _run_cmd(
        [
            "sops",
            "--decrypt",
            "--input-type", "json",
            "--output-type", "json",
            encrypted_path,
        ],
        env=env_extra,
    )
    return json.loads(output)


def repackage_order(
    code_dir: str,
    env_vars: Dict[str, str],
    sops_key: Optional[str] = None,
) -> str:
    """Repackage an order directory with SOPS-encrypted env vars.

    Takes a flat dict of all env vars to encrypt (caller is responsible
    for assembling credentials, callback URLs, introspection fields, etc.).
    Encrypts with SOPS and writes metadata files.

    Returns path to the repackaged directory.
    """
    merged = dict(env_vars)

    # Encrypt with SOPS
    encrypted_file, key_used = encrypt_env(merged, sops_key)

    # Copy encrypted file to code_dir
    dest_encrypted = os.path.join(code_dir, "secrets.enc.json")
    os.rename(encrypted_file, dest_encrypted)

    # Write env_vars.env — plaintext var names only (no values)
    env_file = os.path.join(code_dir, "env_vars.env")
    with open(env_file, "w") as f:
        for key in sorted(merged.keys()):
            f.write(f"{key}\n")

    return code_dir
