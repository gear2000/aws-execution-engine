# Deploy

## GitHub Actions Workflow

Single workflow (`deploy.yml`) handles the entire deploy pipeline in 6 steps.

### Inputs

```
secrets:
  AWS_ACCESS_KEY_ID
  AWS_SECRET_ACCESS_KEY

inputs:
  aws_region              # required, e.g. "us-east-1"
  state_bucket_name       # optional, auto-generated if not provided
```

If `state_bucket_name` is not provided, one is generated:

```
state_bucket_name = "iac-ci-state-${random_5_chars}"
```

### Derived / Opinionated Defaults

```
project_name     = "iac-ci"
ecr_repo_name    = "iac-ci"
image_tag        = <git-sha>
hash             = sha256(state_bucket_name + aws_region)[-5:]
internal_bucket  = "iac-ci-internal-${hash}"
done_bucket      = "iac-ci-done-${hash}"
```

---

## Step 1: Bootstrap State Bucket

```
Directory: infra/00-bootstrap/
State:     LOCAL (not stored remotely)

Creates:
  - S3 bucket (versioning + encryption enabled)

This is the ONE resource the user must manually delete when tearing down.
```

## Step 2: Create ECR Repo

```
Directory: infra/01-ecr/
State:     s3://<state_bucket>/ecr/terraform.tfstate

Generates:
  - backend.tf (points to state bucket)

Creates:
  - ECR repository: iac-ci
```

## Step 3: Build + Push Docker Image

```
Base image: public.ecr.aws/lambda/python:3.14

Steps:
  - Login to ECR
  - docker build -f docker/Dockerfile -t iac-ci .
  - Tag: <ecr-repo>:<git-sha>
  - Tag: <ecr-repo>:latest
  - Push both tags
```

## Step 4: Deploy System

```
Directory: infra/02-deploy/
State:     s3://<state_bucket>/deploy/terraform.tfstate

Generates:
  - backend.tf
  - terraform.tfvars:
      image_tag    = <git-sha>
      ecr_repo     = <ecr_repo_url>
      state_bucket = <bucket_name>
      region       = <region>

Creates:
  - API Gateway (HTTP API, POST /webhook)
  - 4 Lambda functions (all ECR image, different entrypoints)
  - Step Function (watchdog state machine)
  - 3 DynamoDB tables (orders, order_events + GSI, locks)
  - 2 S3 buckets (internal + done, with lifecycle rules)
  - CodeBuild project
  - IAM roles
  - S3 event notification → orchestrator Lambda
```

## Step 5: Smoke Tests

```
Test 1: API Gateway health
  curl POST <api_gw_url>/webhook
  expect: 400 (no payload)

Test 2: Lambda invoke
  aws lambda invoke init_job --payload <minimal_job>
  expect: 200 + run_id

Test 3: DynamoDB tables exist
  aws dynamodb describe-table for each table

Test 4: S3 buckets exist
  aws s3 ls for each bucket

Test 5: ECR image accessible
  aws ecr describe-images
```

## Step 6: Archive for Teardown

```
Zips up:
  - infra/01-ecr/ + generated backend.tf + terraform.tfvars
  - infra/02-deploy/ + generated backend.tf + terraform.tfvars
  - TEARDOWN.md (instructions)

Uploads to:
  s3://<state_bucket>/archive/iac-ci-teardown-<sha>.zip
```

---

## Teardown

To destroy this system:

1. Download teardown archive:
   ```
   aws s3 cp s3://<state_bucket>/archive/iac-ci-teardown-<sha>.zip .
   unzip iac-ci-teardown-<sha>.zip
   ```

2. Destroy deploy resources:
   ```
   cd infra/02-deploy
   terraform init
   terraform destroy
   ```

3. Destroy ECR:
   ```
   cd infra/01-ecr
   terraform init
   terraform destroy
   ```

4. Manually delete state bucket:
   ```
   aws s3 rb s3://<state_bucket> --force
   ```

The state bucket is the only resource not tracked in terraform state that must be manually removed.
