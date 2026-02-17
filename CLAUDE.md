# CLAUDE.md

## Project Overview

iac-ci is a generic, event-driven continuous delivery system for infrastructure-as-code and arbitrary command execution. It is AWS-native (Lambda, DynamoDB, S3, CodeBuild, Step Functions, API Gateway, ECR) and uses a single Docker image with multiple entrypoints for all Lambda functions and CodeBuild workers.

The system processes jobs containing multiple orders, queues them in DynamoDB, and executes them via Lambda or CodeBuild with full dependency resolution, cross-account credential management via SOPS, and PR comment tracking via VCS webhooks.

## Architecture

Two main flows:

- **Part 1 (init_job):** `process_webhook` Lambda receives job parameters, validates orders, repackages with SOPS-encrypted credentials, uploads to S3, inserts into DynamoDB, posts initial PR comment, and triggers the orchestrator via S3 event.
- **Part 2 (execute_orders):** `orchestrator` Lambda is triggered by S3 events (callbacks), acquires a per-run_id lock, evaluates dependency graphs, dispatches ready orders in parallel to Lambda or CodeBuild, and finalizes when all orders complete. Workers callback via presigned S3 PUT URLs. A Step Function watchdog per order handles timeout safety.

## Repo Structure

```
iac-ci/
├── src/
│   ├── common/          # shared libraries (dynamodb, s3, sops, vcs, trace, flow, models)
│   ├── process_webhook/ # Part 1: init_job Lambda
│   ├── orchestrator/    # Part 2: execute_orders Lambda
│   ├── watchdog_check/  # Step Function timeout watchdog Lambda
│   └── worker/          # dual-purpose: Lambda handler + CodeBuild entrypoint
├── docker/
│   └── Dockerfile       # single image, all functions
├── infra/
│   ├── 00-bootstrap/    # state bucket (local TF state)
│   ├── 01-ecr/          # ECR repo
│   └── 02-deploy/       # all AWS resources
├── scripts/             # generate backend.tf + terraform.tfvars
├── tests/
│   ├── smoke/
│   ├── unit/
│   └── integration/
└── .github/workflows/   # deploy pipeline
```

## Key Technical Decisions

- **Single Docker image:** Based on `public.ecr.aws/lambda/python:3.14`. All Lambda functions use the same image with different `image_config.command` overrides. CodeBuild uses the default CMD (`entrypoint.sh`).
- **Cross-account execution:** No IAM role assumption at worker level. Target account credentials are fetched from SSM/Secrets Manager during repackage, encrypted into SOPS bundle, and unpacked as env vars at execution time.
- **Worker callbacks:** Presigned S3 PUT URLs baked into SOPS bundle. Workers write `result.json` with status + logs. No DynamoDB write permissions needed on worker.
- **Orchestrator is event-driven:** Triggered by S3 `ObjectCreated` events on `tmp/callbacks/runs/` prefix. No polling, no chained Lambda loops.
- **Timeout safety:** Per-order Step Function watchdog polls S3 every 60s and writes `timed_out` result.json if worker is unresponsive.
- **VCS abstraction:** ABC base class in `src/common/vcs/base.py`. GitHub implementation first, designed for Bitbucket/GitLab extension.

## DynamoDB Tables

- **orders** — PK: `<run_id>:<order_num>`, TTL: 1 day
- **order_events** — PK: `trace_id`, SK: `order_name:epoch`, GSI PK: `order_name`, GSI SK: `epoch`, TTL: 90 days. Job-level events use `_job` as order_name.
- **orchestrator_locks** — PK: `lock:<run_id>`, TTL: max_timeout

## S3 Buckets

- **Internal** (1 day lifecycle): `tmp/exec/<run_id>/<order_num>/exec.zip` and `tmp/callbacks/runs/<run_id>/<order_num>/result.json`
- **Done** (1 day lifecycle, separate bucket): `<run_id>/done`
- **State** (permanent): TF state files + teardown archive

## Tracing

- Format: `<trace_id>:<epoch_time>`
- Same `trace_id` across entire run, new epoch per leg
- `flow_id`: `<username>:<trace_id>-<flow_label>`

## Build and Deploy

Single GitHub Actions workflow with 6 steps:
1. Bootstrap state bucket (local TF state)
2. Create ECR repo (state in bucket)
3. Build + push Docker image to ECR
4. Deploy system via Terraform (state in bucket)
5. Smoke tests
6. Archive TF code + tfvars to state bucket for teardown

Inputs: `aws_region` (required), `state_bucket_name` (optional, auto-generated)

## Code Style

- Python, primary language for all Lambda functions and common libraries
- Use type hints
- Use dataclasses or Pydantic for models
- Keep functions small and testable
- No CloudWatch logging dependency — capture stdout/stderr directly via subprocess
- Use `os.system` or `subprocess.Popen` for command execution, no output buffering
- Tests use pytest with moto for AWS mocking

## Commands

```bash
# Run unit tests
cd tests && python -m pytest unit/ -v

# Run smoke tests (post-deploy)
bash tests/smoke/test_deploy.sh

# Build Docker image locally
docker build -f docker/Dockerfile -t iac-ci .

# Generate terraform backend
bash scripts/generate_backend.sh <bucket_name> <stage> <region>

# Generate terraform.tfvars
bash scripts/generate_tfvars.sh <ecr_repo_url> <image_tag> <region>
```
