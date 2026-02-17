# Phase 1: Common Libraries

Build the shared libraries that all Lambda functions depend on.

---

## P1.1 — models.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md, then docs/VARIABLES.md.

Create src/common/__init__.py (empty).

Create src/common/models.py with dataclasses for:

- Order: all per-order fields from VARIABLES.md (cmds, timeout, order_name, git_repo, git_folder, s3_location, env_vars, ssm_paths, secret_manager_paths, sops_key, use_lambda, queue_id, dependencies, must_succeed, callback_url)
- Job: all global fields (git_repo, pr_number or issue_number, git_token_location, git_ssh_key_location, username, flow_label, pr_comment_search_tag, presign_expiry, job_timeout) plus a list of Orders
- OrderEvent: trace_id, order_name, epoch, event_type, status, log_location, execution_url, message, flow_id, run_id
- LockRecord: run_id, orchestrator_id, status, acquired_at, ttl, flow_id, trace_id
- DynamoDB record representations for orders table (PK: run_id:order_num)

Use Python dataclasses with type hints. Include from_b64 and to_b64 class methods on Job for decoding/encoding job_parameters_b64. Include from_dict and to_dict methods on all classes. Include status constants: QUEUED, RUNNING, SUCCEEDED, FAILED, TIMED_OUT. Include reserved order name constant: _job for job-level events.
```

## P1.2 — trace.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md (Tracing section).

Create src/common/trace.py:

- generate_trace_id(): returns random hex string (8 chars)
- create_leg(trace_id): returns "<trace_id>:<current_epoch_time>"
- parse_leg(leg_str): returns (trace_id, epoch_time) tuple

Keep it simple. Use time.time() for epoch. Use secrets.token_hex(4) for trace_id generation.
```

## P1.3 — flow.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md (Tracing section).

Create src/common/flow.py:

- generate_flow_id(username, trace_id, flow_label="exec"): returns "<username>:<trace_id>-<flow_label>"
- parse_flow_id(flow_id): returns (username, trace_id, flow_label) tuple

Simple string formatting. No external dependencies.
```

## P1.4 — dynamodb.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md (DynamoDB Tables section).

Create src/common/dynamodb.py using boto3:

Orders table operations:
- put_order(run_id, order_num, order_data): insert order record
- get_order(run_id, order_num): get single order
- get_all_orders(run_id): query all orders for a run_id
- update_order_status(run_id, order_num, status, extra_fields=None): update status + last_update

Order events table operations:
- put_event(trace_id, order_name, event_type, status, extra_fields=None): insert event with current epoch as SK
- get_events(trace_id, order_name_prefix=None): query events, optional begins_with filter
- get_latest_event(trace_id, order_name): get most recent event for an order

Orchestrator locks table operations:
- acquire_lock(run_id, orchestrator_id, ttl, flow_id, trace_id): conditional put (attribute_not_exists OR status=completed)
- release_lock(run_id): update status to completed
- get_lock(run_id): get current lock record

Use environment variables for table names: IAC_CI_ORDERS_TABLE, IAC_CI_ORDER_EVENTS_TABLE, IAC_CI_LOCKS_TABLE. All functions should accept an optional dynamodb_resource parameter for testing.
```

## P1.5 — s3.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md (S3 Layout section).

Create src/common/s3.py using boto3:

- upload_exec_zip(bucket, run_id, order_num, file_path): upload to tmp/exec/<run_id>/<order_num>/exec.zip
- generate_callback_presigned_url(bucket, run_id, order_num, expiry=7200): presigned PUT URL for tmp/callbacks/runs/<run_id>/<order_num>/result.json
- read_result(bucket, run_id, order_num): read and parse result.json, return None if not exists
- write_result(callback_url, status, log): PUT result.json to presigned URL using requests library
- write_init_trigger(bucket, run_id): write tmp/callbacks/runs/<run_id>/0000/result.json
- write_done_endpoint(bucket, run_id, summary): write <run_id>/done to done bucket
- check_result_exists(bucket, run_id, order_num): return True/False if result.json exists

Use environment variables for bucket names: IAC_CI_INTERNAL_BUCKET, IAC_CI_DONE_BUCKET.
```

## P1.6 — sops.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md (Cross-Account Execution Model section).

Create src/common/sops.py:

- encrypt_env(env_vars, sops_key=None): encrypt dict of env vars with SOPS. If no sops_key provided, generate a temporary age key. Write encrypted file. Return path to encrypted file and the key used.
- decrypt_env(encrypted_path, sops_key): decrypt SOPS file, return dict of env vars
- repackage_order(code_dir, env_vars, ssm_values, secret_values, callback_url, sops_key=None):
  - merge env_vars + ssm_values + secret_values + {"CALLBACK_URL": callback_url}
  - encrypt with SOPS
  - write env_vars.env (plaintext var names only, no values)
  - write secrets.src (list of SSM/secrets manager paths fetched)
  - return path to repackaged directory

SOPS operations use subprocess to call the sops binary. Use age for key generation when auto-generating (subprocess call to age-keygen).
```

## P1.7 — vcs/base.py + github.py

```
Read CLAUDE.md and docs/ARCHITECTURE.md.

Create src/common/vcs/__init__.py (empty).

Create src/common/vcs/base.py:

ABC class VcsProvider with abstract methods:
- verify_webhook(headers, body, secret): return bool
- create_comment(repo, pr_number, body, token): return comment_id
- update_comment(repo, comment_id, body, token): return bool
- find_comment_by_tag(repo, pr_number, tag, token): return comment_id or None
- delete_comment(repo, comment_id, token): return bool

Create src/common/vcs/github.py:

GitHubProvider(VcsProvider) implementation:
- verify_webhook: HMAC-SHA256 verification of X-Hub-Signature-256
- create_comment: POST to GitHub REST API /repos/{repo}/issues/{pr_number}/comments
- update_comment: PATCH to /repos/{repo}/issues/comments/{comment_id}
- find_comment_by_tag: GET all comments, search for tag string in body
- delete_comment: DELETE /repos/{repo}/issues/comments/{comment_id}

Use requests library for HTTP calls. Token passed as Authorization: Bearer header. Handle pagination for find_comment_by_tag.
```

## P1.8 — Unit Tests for Common

```
Read CLAUDE.md and all files in src/common/.

Create tests/__init__.py (empty).
Create tests/unit/__init__.py (empty).

Create unit tests in tests/unit/ for all common modules:

- test_models.py: test dataclass creation, from_b64/to_b64 round-trip, from_dict/to_dict, status constants
- test_trace.py: test generate_trace_id format, create_leg format, parse_leg round-trip
- test_flow.py: test generate_flow_id format, parse_flow_id round-trip, default flow_label
- test_dynamodb.py: use moto to mock DynamoDB. Test all CRUD operations, conditional lock acquire/release, TTL fields
- test_s3.py: use moto to mock S3. Test upload, presigned URL generation, read/write result.json, init trigger, done endpoint
- test_sops.py: mock subprocess calls to sops/age-keygen. Test encrypt/decrypt round-trip, repackage_order file layout
- test_vcs_github.py: use responses library to mock GitHub API. Test webhook verification, CRUD on comments, find_comment_by_tag with pagination

Use pytest. Each test file should be independent. Use fixtures for common setup.
```

## P1.9 — GitHub Actions Test Workflow (Phase 1)

```
Create .github/workflows/test.yml:

name: Tests
on: [push, pull_request]

jobs:
  phase1-common:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.14'
      - name: Install dependencies
        run: |
          pip install pytest moto boto3 requests responses
          pip install -e . || pip install -r requirements.txt
      - name: Run common library tests
        run: |
          python -m pytest tests/unit/test_models.py -v
          python -m pytest tests/unit/test_trace.py -v
          python -m pytest tests/unit/test_flow.py -v
          python -m pytest tests/unit/test_dynamodb.py -v
          python -m pytest tests/unit/test_s3.py -v
          python -m pytest tests/unit/test_sops.py -v
          python -m pytest tests/unit/test_vcs_github.py -v

This workflow runs each test file individually so failures are clearly isolated.
```
