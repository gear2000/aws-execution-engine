# Phase 6: Integration Tests

End-to-end tests that verify the full system works together. Depends on all previous phases being complete.

---

## P6.1 — Integration Tests

```
Read CLAUDE.md and all docs.

Create tests/integration/__init__.py (empty).

Create tests/integration/test_init_job.py:

Test the full init_job flow using moto (mocked AWS):
- Create a minimal job_parameters_b64 with 2 orders (no deps), both with cmds, timeout, and s3_location
- Mock SOPS encrypt (subprocess)
- Mock VCS PR comment creation (responses)
- Invoke process_webhook handler with a synthetic API Gateway event
- Verify:
  - Response contains run_id, trace_id, flow_id, done_endpt
  - Both orders exist in DynamoDB orders table with status=queued
  - exec.zip uploaded to S3 for each order
  - Init trigger written at tmp/callbacks/runs/<run_id>/0000/result.json
  - Job-level _job order_event written
  - PR comment created with correct body (search_tag, run_id, order summary)

Create tests/integration/test_orchestrator.py:

Test the orchestrator flow using moto:
- Pre-populate DynamoDB orders table with 3 orders:
  - order-1: no deps, status=running
  - order-2: no deps, status=running
  - order-3: depends on [order-1, order-2], status=queued
- Write result.json for order-1 to S3: {"status": "succeeded", "log": "done"}
- Invoke orchestrator handler with synthetic S3 event for order-1
- Verify:
  - Lock acquired
  - order-1 status updated to succeeded in DynamoDB
  - order_event written for order-1
  - order-3 NOT dispatched (order-2 still running)
  - Lock released
- Write result.json for order-2 to S3: {"status": "succeeded", "log": "done"}
- Invoke orchestrator handler again with S3 event for order-2
- Verify:
  - order-2 status updated to succeeded
  - order-3 dispatched (all deps satisfied)
  - order-3 status updated to running
  - Watchdog Step Function started (mock stepfunctions client)

Create tests/integration/test_full_run.py:

Full end-to-end using moto:
- Submit job via process_webhook handler:
  - 3 orders: order-1 (no deps), order-2 (no deps), order-3 (depends on 1+2)
- Verify init_job completed (orders in DynamoDB, S3 trigger written)
- Invoke orchestrator with init trigger S3 event
- Verify order-1 and order-2 dispatched (status=running)
- Simulate order-1 completion (write result.json to S3)
- Invoke orchestrator with order-1 S3 event
- Verify order-3 still waiting
- Simulate order-2 completion
- Invoke orchestrator with order-2 S3 event
- Verify order-3 dispatched
- Simulate order-3 completion
- Invoke orchestrator with order-3 S3 event
- Verify:
  - All orders succeeded
  - Job-level _job event written with status=succeeded
  - Done endpoint written to done bucket
  - Lock released

Create tests/integration/test_failure_scenarios.py:

Test failure paths using moto:
- Test must_succeed failure:
  - order-1 (must_succeed=true), order-2 (depends on order-1)
  - order-1 writes result.json with status=failed
  - Invoke orchestrator
  - Verify: order-2 marked failed (dep failed), job status=failed, done endpoint written

- Test timeout via watchdog:
  - order-1 dispatched, no result.json written
  - Invoke watchdog_check with start_time in the past (exceeded timeout)
  - Verify: watchdog writes timed_out result.json
  - Invoke orchestrator
  - Verify: order-1 status=timed_out

- Test lock contention:
  - Invoke orchestrator twice for same run_id simultaneously
  - Verify: only one acquires lock, other exits cleanly

All integration tests use @pytest.mark.integration decorator.
Mock subprocess (SOPS/age), mock VCS API calls, use moto for all AWS services.
```

## P6.2 — Update GitHub Actions Test Workflow (Phase 6)

```
Update .github/workflows/test.yml to add Phase 6 integration tests.

Add a new job that depends on phase5-scripts:

  phase6-integration:
    needs: phase5-scripts
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
      - name: Run integration tests
        run: |
          python -m pytest tests/integration/test_init_job.py -v
          python -m pytest tests/integration/test_orchestrator.py -v
          python -m pytest tests/integration/test_full_run.py -v
          python -m pytest tests/integration/test_failure_scenarios.py -v
```

## P6.3 — Final test.yml Overview

```
Review .github/workflows/test.yml and verify the full job dependency chain:

  phase1-common
       │
       ▼
  phase2-lambdas
       │
       ▼
  phase3-docker
       │
       ▼
  phase4-terraform
       │
       ▼
  phase5-scripts
       │
       ▼
  phase6-integration

Each phase only runs if the previous phase passed.
Each phase runs its tests individually (one pytest call per test file) so failures are clearly isolated.

Verify:
- All test files are referenced
- Dependencies between jobs are correct
- Python version is consistent across all jobs
- pip install commands are consistent
- No test file is missing from the workflow
```
