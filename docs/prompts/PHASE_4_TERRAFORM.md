# Phase 4: Terraform

Build all Terraform infrastructure code in three stages. Depends on Phases 1-3 being complete.

---

## P4.1 — infra/00-bootstrap

```
Read CLAUDE.md and docs/DEPLOY.md (Step 1 section).

Create infra/00-bootstrap/:

main.tf:
- terraform required_version >= 1.5
- aws provider with variable region
- S3 bucket with:
  - versioning enabled
  - server-side encryption (AES256)
  - block all public access
  - force_destroy = false (safety)

variables.tf:
- state_bucket_name (string, required)
- aws_region (string, required)

outputs.tf:
- bucket_name
- bucket_arn

This uses LOCAL state only. No backend.tf. No remote state.
```

## P4.2 — infra/01-ecr

```
Read CLAUDE.md and docs/DEPLOY.md (Step 2 section).

Create infra/01-ecr/:

main.tf:
- terraform required_version >= 1.5
- aws provider with variable region
- ECR repository "iac-ci"
- Image scanning on push enabled
- Lifecycle policy: keep last 10 images
- Image tag mutability: MUTABLE (for :latest tag)

variables.tf:
- aws_region (string)

outputs.tf:
- ecr_repo_url
- ecr_repo_arn

Note: backend.tf is generated at deploy time by scripts/generate_backend.sh. Do not create a backend.tf in the repo. For local validation, use a temporary local backend.
```

## P4.3 — infra/02-deploy

```
Read CLAUDE.md, docs/DEPLOY.md (Step 4), docs/ARCHITECTURE.md (full document for all resource details).

Create infra/02-deploy/:

variables.tf:
- image_tag (string)
- ecr_repo (string)
- aws_region (string)

main.tf:
- terraform required_version >= 1.5
- aws provider with variable region
- data source for current AWS account ID and region

api_gateway.tf:
- HTTP API (aws_apigatewayv2_api)
- POST /webhook route → process_webhook Lambda integration
- Stage: $default with auto_deploy

lambdas.tf:
- 4 Lambda functions, all package_type = "Image", same image_uri = "${var.ecr_repo}:${var.image_tag}", different image_config.command:
  - process_webhook: cmd = ["src.process_webhook.handler.handler"], timeout = 300, memory = 512
  - orchestrator: cmd = ["src.orchestrator.handler.handler"], timeout = 600, memory = 512
  - watchdog_check: cmd = ["src.watchdog_check.handler.handler"], timeout = 60, memory = 256
  - worker: cmd = ["src.worker.handler.handler"], timeout = 600, memory = 1024
- Environment variables on each Lambda for table names (IAC_CI_ORDERS_TABLE, IAC_CI_ORDER_EVENTS_TABLE, IAC_CI_LOCKS_TABLE) and bucket names (IAC_CI_INTERNAL_BUCKET, IAC_CI_DONE_BUCKET)
- Lambda function URL on process_webhook for direct URL invocation

step_functions.tf:
- Watchdog state machine (aws_sfn_state_machine)
- Definition:
  - StartAt: CheckResult
  - States:
    - CheckResult: Task, invoke watchdog_check Lambda, Next: IsDone
    - IsDone: Choice, if done=true → Succeed, else → WaitStep
    - WaitStep: Wait 60 seconds, Next: CheckResult
    - Succeed: Succeed

dynamodb.tf:
- orders table: hash_key = "pk" (string), TTL attribute = "ttl", billing_mode = PAY_PER_REQUEST
- order_events table: hash_key = "pk" (string), range_key = "sk" (string), TTL attribute = "ttl", billing_mode = PAY_PER_REQUEST
  - Global secondary index: "order_name_index", hash_key = "order_name" (string), range_key = "epoch" (number), projection_type = ALL
- orchestrator_locks table: hash_key = "pk" (string), TTL attribute = "ttl", billing_mode = PAY_PER_REQUEST

s3.tf:
- Internal bucket:
  - lifecycle rule: delete objects after 1 day
  - block all public access
  - server-side encryption
  - force_destroy = true (ephemeral data)
- Done bucket:
  - lifecycle rule: delete objects after 1 day
  - server-side encryption
  - force_destroy = true (ephemeral data)
- Bucket naming uses hash: sha256(terraform.workspace + var.aws_region) last 5 chars
  - iac-ci-internal-${hash}
  - iac-ci-done-${hash}

s3_notifications.tf:
- Lambda permission allowing S3 to invoke orchestrator Lambda
- S3 bucket notification on internal bucket:
  - prefix: tmp/callbacks/runs/
  - suffix: result.json
  - events: s3:ObjectCreated:*
  - → orchestrator Lambda ARN

codebuild.tf:
- CodeBuild project:
  - Environment: LINUX_CONTAINER, BUILD_GENERAL1_SMALL
  - Image: ECR worker image (same image_uri)
  - Privileged mode: false
  - Environment variables for table names and bucket names

iam.tf:
- process_webhook role + policy:
  - DynamoDB: PutItem, GetItem, Query on orders + order_events tables
  - S3: PutObject, GetObject on internal bucket
  - SSM: GetParameter
  - Secrets Manager: GetSecretValue
  - Lambda: InvokeFunction (for potential direct invoke)
- orchestrator role + policy:
  - DynamoDB: full access on all 3 tables (orders, order_events, locks)
  - S3: GetObject, PutObject on internal bucket + done bucket
  - Lambda: InvokeFunction on worker Lambda
  - CodeBuild: StartBuild
  - Step Functions: StartExecution
- watchdog_check role + policy:
  - S3: GetObject, PutObject on internal bucket (tmp/callbacks/runs/ prefix only)
- worker role + policy:
  - S3: GetObject on internal bucket (tmp/exec/ prefix only)
- CodeBuild service role + policy:
  - ECR: GetAuthorizationToken, BatchCheckLayerAvailability, GetDownloadUrlForLayer, BatchGetImage
  - S3: GetObject on internal bucket (tmp/exec/ prefix only)
  - CloudWatch Logs: CreateLogGroup, CreateLogStream, PutLogEvents (CodeBuild requires this)

outputs.tf:
- api_gateway_url
- api_gateway_id
- lambda_function_names (map)
- lambda_function_arns (map)
- dynamodb_table_names (map)
- s3_bucket_names (map)
- step_function_arn
- codebuild_project_name
```

## P4.4 — Terraform Validation + Update GHA

```
Read CLAUDE.md and all files in infra/.

For each terraform stage (00-bootstrap, 01-ecr, 02-deploy):
- Run terraform init (with local backend for validation)
- Run terraform validate
- Run terraform fmt -check
- Fix any issues

For 02-deploy specifically, verify:
- All Lambda functions reference the same ECR image URI
- All IAM policies follow least privilege (resource-level ARNs, not *)
- All DynamoDB tables have TTL enabled
- S3 lifecycle rules are set to 1 day
- S3 event notification targets orchestrator Lambda
- Step Function definition is valid JSON
- Lambda environment variables match what src/common expects

Update .github/workflows/test.yml to add Phase 4 Terraform validation:

  phase4-terraform:
    needs: phase3-docker
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: hashicorp/setup-terraform@v3
        with:
          terraform_version: '1.5'
      - name: Validate 00-bootstrap
        run: |
          cd infra/00-bootstrap
          terraform init
          terraform validate
          terraform fmt -check
      - name: Validate 01-ecr
        run: |
          cd infra/01-ecr
          terraform init
          terraform validate
          terraform fmt -check
      - name: Validate 02-deploy
        run: |
          cd infra/02-deploy
          terraform init -backend=false
          terraform validate
          terraform fmt -check
```
