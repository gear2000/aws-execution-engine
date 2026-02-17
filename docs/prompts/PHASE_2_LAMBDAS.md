# Phase 2: Lambda Functions

Build all Lambda function handlers. Depends on Phase 1 (common libraries) being complete and tested.

---

## P2.1 — init_job

```
Read CLAUDE.md, docs/ARCHITECTURE.md (Part 1 section), docs/VARIABLES.md, and all files in src/common/.

Create src/init_job/__init__.py (empty).

Create src/init_job/handler.py:
- Lambda entrypoint that accepts API Gateway, SNS, direct invoke, and Lambda URL events
- Normalize event format regardless of trigger source
- Extract job_parameters_b64 from event body
- Call process_job_and_insert_orders()
- Return HTTP response with run_id, trace_id, flow_id, done_endpt, pr_search_tag

Create src/init_job/validate.py:
- validate_orders(job): iterate all orders, check cmds non-empty, timeout present, has code source (s3_location or git_repo+git_token_location). Return list of errors or empty list.
- Fail fast on first invalid order.

Create src/init_job/repackage.py:
- repackage_orders(job, run_id, trace_id): for each order, fetch code (git clone or S3 download), fetch SSM/secrets manager values, generate presigned callback URL, call sops.repackage_order(), re-zip. Return list of repackaged order paths + callback URLs.

Create src/init_job/upload.py:
- upload_orders(repackaged_orders, run_id, bucket): upload each exec.zip to S3. Optional stripped copy.

Create src/init_job/insert.py:
- insert_orders(job, run_id, flow_id, trace_id, repackaged_orders): insert each order into DynamoDB orders table with all fields. Write initial job-level order_event (_job:started).

Create src/init_job/pr_comment.py:
- init_pr_comment(job, run_id, flow_id, search_tag): use VcsPrHelper to post initial PR comment with order summary table.

The handler orchestrates: generate trace_id/run_id/flow_id → validate → repackage → upload → insert → pr_comment → write init trigger to S3 → return response.
```

## P2.2 — Unit Tests for init_job

```
Read CLAUDE.md and all files in src/init_job/ and src/common/.

Create unit tests in tests/unit/ for init_job:

- test_validate.py: test valid orders pass, missing cmds fails, missing timeout fails, missing code source fails, fail-fast behavior
- test_repackage.py: mock git clone, S3 download, SSM/secrets fetch, SOPS encrypt. Test repackage produces correct file structure.
- test_upload.py: use moto S3. Test exec.zip uploaded to correct path. Test stripped copy if requested.
- test_insert.py: use moto DynamoDB. Test all orders inserted with correct fields. Test job-level _job event written. Test TTL set.
- test_pr_comment.py: mock VCS provider. Test comment body includes search_tag, run_id, flow_id, order summary.
- test_handler.py: mock all downstream calls. Test API Gateway event parsing, SNS event parsing, direct invoke parsing. Test full flow returns correct response.

Use pytest + moto + responses.
```

## P2.3 — orchestrator

```
Read CLAUDE.md, docs/ARCHITECTURE.md (Part 2 section), and all files in src/common/.

Create src/orchestrator/__init__.py (empty).

Create src/orchestrator/handler.py:
- Lambda entrypoint triggered by S3 event
- Parse run_id from S3 key path: tmp/callbacks/runs/<run_id>/<order_num>/result.json
- Call execute_orders(run_id)

Create src/orchestrator/lock.py:
- acquire_lock(run_id, flow_id, trace_id): wrapper around dynamodb.acquire_lock. Returns True/False.
- release_lock(run_id): wrapper around dynamodb.release_lock.
- If lock not acquired, handler exits immediately.

Create src/orchestrator/read_state.py:
- read_state(run_id): query all orders from DynamoDB. For each order with status=running, check S3 for result.json. If found, parse result, update order status in DynamoDB, write order_event, update PR comment. Return current state of all orders.

Create src/orchestrator/evaluate.py:
- evaluate_orders(orders): for each queued order, check dependencies. Return three lists: ready_to_dispatch, failed_due_to_deps, still_waiting. An order with a failed dependency where must_succeed=true goes into failed_due_to_deps.

Create src/orchestrator/dispatch.py:
- dispatch_orders(ready_orders, run_id, flow_id, trace_id): for each ready order, invoke Lambda or start CodeBuild based on use_lambda flag. Start watchdog Step Function. Update order status to running. Write order_event. Update PR comment. All dispatched in parallel using concurrent.futures.

Create src/orchestrator/finalize.py:
- check_and_finalize(orders, run_id, flow_id, trace_id, job): check if all orders are terminal (succeeded/failed/timed_out). If yes: determine job status (all succeeded=succeeded, any must_succeed failed=failed, timeout=timed_out), write job-level order_event, write done endpoint, final PR comment, release lock. If no: release lock and exit (next S3 callback will re-trigger).

The handler orchestrates: parse run_id → acquire lock (exit if not acquired) → read state → evaluate → dispatch ready → check and finalize or release lock.
```

## P2.4 — Unit Tests for orchestrator

```
Read CLAUDE.md and all files in src/orchestrator/ and src/common/.

Create unit tests in tests/unit/ for orchestrator:

- test_orchestrator_lock.py: use moto DynamoDB. Test acquire succeeds first time, fails when lock exists, succeeds when lock is completed.
- test_read_state.py: use moto DynamoDB + S3. Test reads orders correctly. Test detects new result.json files. Test updates order status and writes events.
- test_evaluate.py: test no-dep orders are ready. Test all-deps-succeeded makes order ready. Test failed dep with must_succeed fails the order. Test running deps means wait.
- test_dispatch.py: mock Lambda invoke + CodeBuild start + Step Function start. Test Lambda dispatch path. Test CodeBuild dispatch path. Test watchdog started. Test parallel dispatch.
- test_finalize.py: use moto S3 + DynamoDB. Test all succeeded = job succeeded. Test must_succeed failure = job failed. Test done endpoint written. Test lock released. Test not-all-done releases lock without finalizing.
- test_handler.py: mock all downstream. Test S3 event parsing extracts run_id. Test lock not acquired = exit. Test full flow.

Use pytest + moto + responses.
```

## P2.5 — watchdog_check

```
Read CLAUDE.md and docs/ARCHITECTURE.md (Watchdog section).

Create src/watchdog_check/__init__.py (empty).

Create src/watchdog_check/handler.py:

Lambda handler invoked by Step Function. Receives input:
  - run_id
  - order_num
  - timeout (seconds)
  - start_time (epoch when order was dispatched)
  - internal_bucket

Logic:
1. Check if result.json exists at tmp/callbacks/runs/<run_id>/<order_num>/result.json
2. If exists: return {"done": true}
3. If not exists: check if current_time > start_time + timeout
   - If timeout exceeded: write result.json with {"status": "timed_out", "log": "Worker unresponsive, timed out by watchdog"}, return {"done": true}
   - If not exceeded: return {"done": false}

Step Function uses the return value to decide whether to loop (Wait 60s) or exit.
```

## P2.6 — Unit Tests for watchdog_check

```
Read CLAUDE.md and src/watchdog_check/handler.py.

Create tests/unit/test_watchdog.py:

- test_result_exists_returns_done: mock S3 with result.json present → done=true
- test_no_result_not_timed_out: mock S3 empty, current_time < start_time + timeout → done=false
- test_no_result_timed_out: mock S3 empty, current_time > start_time + timeout → writes timed_out result.json, done=true
- test_timed_out_result_content: verify written result.json has correct status and log message

Use pytest + moto.
```

## P2.7 — worker

```
Read CLAUDE.md and docs/ARCHITECTURE.md (Worker section).

Create src/worker/__init__.py (empty).

Create src/worker/handler.py:
- Lambda entrypoint. Receives event with s3_location (path to exec.zip) and internal_bucket.
- Calls run(s3_location, internal_bucket).

Create src/worker/entrypoint.sh:
- #!/bin/bash
- Reads S3_LOCATION and INTERNAL_BUCKET from environment
- Calls: python -m src.worker.run

Create src/worker/run.py:
- run(s3_location, internal_bucket):
  1. Download exec.zip from S3 to /tmp
  2. Extract zip
  3. Find and decrypt SOPS file → env vars loaded into os.environ
  4. Read cmds from order config
  5. For each cmd: execute with subprocess.Popen, capture stdout+stderr in real-time (no buffering), track exit code
  6. Determine status: exit code 0 = succeeded, non-zero = failed
  7. Call callback with status + combined logs
  8. Return status

Create src/worker/callback.py:
- send_callback(callback_url, status, log):
  - PUT to presigned URL with JSON body: {"status": status, "log": log}
  - Use requests library
  - Retry up to 3 times on failure
  - If all retries fail, log error (orchestrator watchdog will handle timeout)

Worker should handle its own timeout: read TIMEOUT from env vars, wrap cmd execution in a timeout. If timeout exceeded, set status to timed_out and callback.
```

## P2.8 — Unit Tests for worker

```
Read CLAUDE.md and all files in src/worker/.

Create unit tests in tests/unit/:

- test_worker_run.py: mock S3 download, mock SOPS decrypt, mock subprocess.Popen. Test successful cmd execution → succeeded. Test failed cmd → failed. Test timeout → timed_out. Test multiple cmds execute in order. Test stdout/stderr captured.
- test_worker_callback.py: mock requests.put. Test successful PUT. Test retry on failure. Test all retries exhausted logs error. Test correct JSON payload.

Use pytest + moto + responses.
```

## P2.9 — Update GitHub Actions Test Workflow (Phase 2)

```
Update .github/workflows/test.yml to add Phase 2 tests.

Add a new job that depends on phase1-common:

  phase2-lambdas:
    needs: phase1-common
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
      - name: Run init_job tests
        run: |
          python -m pytest tests/unit/test_validate.py -v
          python -m pytest tests/unit/test_repackage.py -v
          python -m pytest tests/unit/test_upload.py -v
          python -m pytest tests/unit/test_insert.py -v
          python -m pytest tests/unit/test_pr_comment.py -v
          python -m pytest tests/unit/test_handler.py -v
      - name: Run orchestrator tests
        run: |
          python -m pytest tests/unit/test_orchestrator_lock.py -v
          python -m pytest tests/unit/test_read_state.py -v
          python -m pytest tests/unit/test_evaluate.py -v
          python -m pytest tests/unit/test_dispatch.py -v
          python -m pytest tests/unit/test_finalize.py -v
      - name: Run watchdog tests
        run: |
          python -m pytest tests/unit/test_watchdog.py -v
      - name: Run worker tests
        run: |
          python -m pytest tests/unit/test_worker_run.py -v
          python -m pytest tests/unit/test_worker_callback.py -v
```
